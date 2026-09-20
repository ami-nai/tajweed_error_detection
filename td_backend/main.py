import json
import os
import tempfile
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from ml_engine import (
    TAJWEED_ONLY_INDEX,
    evaluate_audio,
    get_surah_ayah_ids,
    predict_phonemes_bytes,
    GuidedLiveTrie,
    resolve_ayah,
    strip_diacritics,
)

app = FastAPI()

EVAL_INTERVAL_SEC = 0.5
MIN_NEW_BYTES = 8000
MIN_RESOLVE_TOKENS = 5
# Open mic: if no new word confirms for STALL_ADVANCE_TICKS consecutive beats,
# assume the user has moved on and re-resolve the ayah on a recent-token window.
STALL_ADVANCE_TICKS = 3
# Lower bound on the recent-token window used for ayah re-resolution.
MIN_ADVANCE_WINDOW_TOKENS = 40


class Session:
    def __init__(
        self,
        ws,
        mode,
        surah_id,
        ayah_id,
        allowed_surahs,
        word_data_by_ayah,
        session_ayahs,
    ):
        self.ws = ws
        self.mode = mode
        self.surah_id = surah_id
        self.ayah_id = ayah_id
        self.allowed_surahs = allowed_surahs

        # Word-tracking state (mirrors old session_words / session_read)
        if mode == "surah":
            self.session_ayahs = session_ayahs
            self.ayah_index = 0
            self.ayah_accuracies = {a: None for a in session_ayahs}
            self.ayah_pers = {a: None for a in session_ayahs}
            self.session_read = {
                a: [
                    {"text": w["text"], "is_read": False, "letters": []}
                    for w in word_data_by_ayah[surah_id][a]
                ]
                for a in session_ayahs
            }
        else:
            self.session_ayahs = None
            self.ayah_index = None
            self.session_words = (
                [
                    {"text": w["text"], "is_read": False, "letters": []}
                    for w in word_data_by_ayah[surah_id][ayah_id]
                ]
                if mode == "single" and surah_id in word_data_by_ayah and ayah_id in word_data_by_ayah.get(surah_id, {})
                else []
            )

        self.audio = bytearray()
        self.ayah_start = 0
        self.eval_pos = 0
        self.stream_pred_raw = ""
        self.feed_tail = ""
        self.openmic_ayah_start = 0
        self._om_scanning = False
        self._om_last_attempt = None
        self._stall_ticks = 0
        self.guided = None
        self.resolved = None
        self.resolving_sent = False
        self.closed = False
        self._hb = None

        self._init_guided()

    def _init_guided(self):
        s = self.surah_id
        if self.mode == "single":
            if s in TAJWEED_ONLY_INDEX and self.ayah_id in TAJWEED_ONLY_INDEX[s]:
                self.guided = GuidedLiveTrie(TAJWEED_ONLY_INDEX[s][self.ayah_id]["words"])
        elif self.mode == "surah":
            if s in TAJWEED_ONLY_INDEX and self.session_ayahs:
                cur = self.session_ayahs[self.ayah_index]
                self.guided = GuidedLiveTrie(TAJWEED_ONLY_INDEX[s][cur]["words"])
        elif self.mode == "open_mic":
            self.guided = None
            if self.resolved is not None:
                s, a = self.resolved
                if s in TAJWEED_ONLY_INDEX and a in TAJWEED_ONLY_INDEX[s]:
                    self.guided = GuidedLiveTrie(TAJWEED_ONLY_INDEX[s][a]["words"])

    def _current_ayah_id(self):
        if self.mode == "surah":
            return self.session_ayahs[self.ayah_index]
        if self.mode == "open_mic" and self.resolved:
            return self.resolved[1]
        return self.ayah_id

    def start_heartbeat(self):
        self._hb = asyncio.ensure_future(self._heartbeat())

    async def stop_heartbeat(self):
        if self._hb and not self._hb.done():
            self._hb.cancel()
            try:
                await self._hb
            except asyncio.CancelledError:
                pass
        self._hb = None

    async def _heartbeat(self):
        try:
            while not self.closed:
                await asyncio.sleep(EVAL_INTERVAL_SEC)
                await self.tick()
        except asyncio.CancelledError:
            return

    async def tick(self):
        if self.closed:
            return
        try:
            await self._tick_once()
        except Exception as exc:
            # A single bad beat must never kill the session; log and carry on.
            print(f"⚠️ [TICK EXCEPTION] {type(exc).__name__}: {exc}")

    async def _tick_once(self):
        tok = ""
        if len(self.audio) - self.eval_pos >= MIN_NEW_BYTES:
            delta = bytes(self.audio[self.eval_pos :])
            self.eval_pos = len(self.audio)
            tok = await asyncio.to_thread(predict_phonemes_bytes, delta)
            self.stream_pred_raw += tok
            self.feed_tail += tok

        if self.mode in ("single", "surah") and self.guided is not None:
            self.guided.feed(tok)
            newly = self.guided.confirm()
            if newly:
                self._merge_newly(newly)
                await self._send_interim()

        elif self.mode == "open_mic":
            if self.resolved is not None and self.guided is not None:
                self.guided.feed(tok)
                newly = self.guided.confirm()
                if newly:
                    self._stall_ticks = 0
                    self._merge_newly(newly)
                else:
                    # No word confirmed on this beat: count the stall so a
                    # finished (or stuck-mid-word) ayah triggers a
                    # re-resolution attempt.
                    self._stall_ticks += 1
                    if self._stall_ticks >= STALL_ADVANCE_TICKS:
                        await self._maybe_advance_openmic()
                # Interim streams the live phoneme strip on every tick even
                # between word confirmations (smooth real-time update).
                await self._send_interim()
            else:
                clean = self._clean_openmic_tail()
                if len(clean) >= MIN_RESOLVE_TOKENS:
                    res = await asyncio.to_thread(
                        resolve_ayah, self.feed_tail, self.allowed_surahs
                    )
                    if res is not None:
                        await self._commit_openmic(res[0], res[1], res[3])
                    elif not self.resolving_sent:
                        self.resolving_sent = True
                        await self._send_openmic_live_interim()
                else:
                    await self._send_openmic_live_interim()

    async def _send_openmic_live_interim(self):
        try:
            await self.ws.send_json({
                "mode": "open_mic",
                "final": False,
                "live": self.stream_pred_raw,
            })
        except Exception:
            # Transient send failure: keep the session alive and let the next
            # beat retry. Only a receive-side disconnect closes the connection.
            pass

    def _clean_openmic_tail(self, src=None):
        source = src if src is not None else self.feed_tail
        return "".join(
            c for c in source
            if c in " " or c.isalnum() or c in "َُِّْٰٓۦ"
        )

    async def _commit_openmic(self, s, a, score):
        is_first = self.resolved is None
        self.resolved = (s, a)
        self.surah_id = s
        self.ayah_id = a
        self.session_words = [
            {"text": w["text"], "is_read": False, "letters": []}
            for w in TAJWEED_ONLY_INDEX[s][a]["words"]
        ]
        self._init_guided()
        self.guided.feed(self.feed_tail)
        newly = self.guided.confirm()
        self._merge_newly(newly)
        self.resolving_sent = False
        self._om_last_attempt = None
        self._stall_ticks = 0
        self.feed_tail = ""
        if not is_first:
            self.openmic_ayah_start = len(self.audio)
        print(f"🎯 [OPEN MIC] Detected: surah {s}, ayah {a} (score={score})")
        await self._send_interim()

    async def _maybe_advance_openmic(self):
        if self._om_scanning or self.resolved is None or self.guided is None:
            return
        # Resolve on the RECENT tokens only: the full feed_tail is polluted with
        # the previous ayah's stream and would match it again. Size the window
        # from the current ayah's expected length so a following ayah of the
        # same class fits.
        try:
            cur = TAJWEED_ONLY_INDEX[self.surah_id][self.ayah_id]["expected_full"]
            window_len = max(MIN_ADVANCE_WINDOW_TOKENS, 2 * len(strip_diacritics(cur)))
        except Exception:
            window_len = MIN_ADVANCE_WINDOW_TOKENS
        tail = self.feed_tail[-window_len:] if window_len > 0 else ""
        clean = self._clean_openmic_tail(tail)
        if len(clean) < MIN_RESOLVE_TOKENS:
            return
        if self._om_last_attempt is not None and len(clean) < self._om_last_attempt + MIN_RESOLVE_TOKENS:
            return
        self._om_scanning = True
        self._om_last_attempt = len(clean)
        try:
            res = await asyncio.to_thread(resolve_ayah, tail, self.allowed_surahs)
        finally:
            self._om_scanning = False
        # Whatever the outcome, drop the stale (previous-ayah) prefix now so the
        # window keeps sliding toward the most recent tokens on the next try.
        self.feed_tail = tail
        if res is None:
            return
        s, a = res[0], res[1]
        if (s, a) <= (self.surah_id, self.ayah_id):
            return
        await self._commit_openmic(s, a, res[3])

    def _merge_newly(self, newly):
        if self.mode == "single":
            for item in newly:
                idx = item["index"]
                if idx < len(self.session_words):
                    self.session_words[idx]["is_read"] = True
                    self.session_words[idx]["letters"] = item["letters"]
        elif self.mode == "surah":
            target = self.session_ayahs[self.ayah_index]
            ayah_words = self.session_read[target]
            for item in newly:
                idx = item["index"]
                if idx < len(ayah_words):
                    ayah_words[idx]["is_read"] = True
                    ayah_words[idx]["letters"] = item["letters"]
        elif self.mode == "open_mic":
            target = self.ayah_id
            if not hasattr(self, "session_words"):
                self.session_words = []
            while len(self.session_words) <= max((item["index"] for item in newly), default=-1):
                self.session_words.append({"text": "", "is_read": False, "letters": []})
            for item in newly:
                idx = item["index"]
                if idx < len(self.session_words):
                    self.session_words[idx]["is_read"] = True
                    self.session_words[idx]["letters"] = item["letters"]

    async def _send_interim(self):
        try:
            if self.mode == "single":
                await self.ws.send_json({
                    "mode": "single",
                    "final": False,
                    "ayah_id": self.ayah_id,
                    "words": self.session_words,
                })
            elif self.mode == "surah":
                target = self.session_ayahs[self.ayah_index]
                words_by_ayah = [self.session_read[a] for a in self.session_ayahs]
                await self.ws.send_json({
                    "mode": "surah",
                    "final": False,
                    "current_ayah": target,
                    "next_ayah": self.session_ayahs[self.ayah_index + 1] if self.ayah_index + 1 < len(self.session_ayahs) else None,
                    "ayah_order": self.session_ayahs,
                    "words": words_by_ayah,
                })
            elif self.mode == "open_mic":
                await self.ws.send_json({
                    "mode": "open_mic",
                    "final": False,
                    "detected": {"surah_id": self.surah_id, "ayah_id": self.ayah_id},
                    "ayah_id": self.ayah_id,
                    "words": self.session_words,
                    "live": self.stream_pred_raw,
                })
        except Exception:
            # Transient send failure: keep the session alive and let the next
            # beat retry. Only a receive-side disconnect closes the connection.
            print(f"⚠️ [INTERIM SEND FAILED] send_json raised")

    # ---- control commands ----

    async def do_next(self):
        if self.closed or self.mode != "surah":
            return
        await self.stop_heartbeat()
        await self._evaluate_and_finalize_surah()
        self.ayah_index += 1
        if self.ayah_index >= len(self.session_ayahs):
            print("🎉 Surah complete.")
            try:
                await self.ws.send_json({"mode": "surah", "finished": True})
            except Exception:
                pass
            self.closed = True
            try:
                await self.ws.close()
            except Exception:
                pass
            return
        self.ayah_start = len(self.audio)
        self.eval_pos = len(self.audio)
        self.stream_pred_raw = ""
        self._init_guided()
        self.resolving_sent = False
        self.start_heartbeat()

    async def _evaluate_and_finalize_surah(self):
        target = self.session_ayahs[self.ayah_index]
        segment = bytes(self.audio[self.ayah_start :])
        chunk_result = None
        if segment and self.surah_id in TAJWEED_ONLY_INDEX and target in TAJWEED_ONLY_INDEX[self.surah_id]:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                tmp.write(segment)
                tmp_path = tmp.name
            try:
                chunk_result = await asyncio.to_thread(
                    evaluate_audio, self.surah_id, target, tmp_path
                )
            finally:
                os.remove(tmp_path)

        if chunk_result is not None:
            ayah_words = self.session_read[target]
            for i, word in enumerate(chunk_result.get("words", [])):
                if i < len(ayah_words) and word.get("is_read"):
                    ayah_words[i]["is_read"] = True
                    if word.get("letters"):
                        ayah_words[i]["letters"] = word["letters"]
            self.ayah_accuracies[target] = chunk_result.get("accuracy", 0.0)
            self.ayah_pers[target] = chunk_result.get("per", 0.0)

        scored = [acc for acc in self.ayah_accuracies.values() if acc is not None]
        surah_average = round(sum(scored) / len(scored), 2) if scored else 0.0
        scored_per = [p for p in self.ayah_pers.values() if p is not None]
        surah_per = round(sum(scored_per) / len(scored_per), 2) if scored_per else 0.0
        words_by_ayah = [self.session_read[a] for a in self.session_ayahs]

        payload = {
            "mode": "surah",
            "final": True,
            "current_ayah": target,
            "next_ayah": self.session_ayahs[self.ayah_index + 1] if self.ayah_index + 1 < len(self.session_ayahs) else None,
            "ayah_order": self.session_ayahs,
            "words": words_by_ayah,
            "ayah_accuracies": self.ayah_accuracies,
            "surah_average": surah_average,
            "ayah_pers": self.ayah_pers,
            "surah_per": surah_per,
            "finished": False,
        }
        if chunk_result:
            payload.update({
                "diff": chunk_result.get("diff", []),
                "expected": chunk_result.get("expected", ""),
                "predicted": chunk_result.get("predicted", ""),
                "mistakes": chunk_result.get("mistakes", []),
            })
        try:
            await self.ws.send_json(payload)
        except Exception:
            self.closed = True

    async def do_stop(self):
        if self.closed:
            return
        await self.stop_heartbeat()
        self.closed = True
        try:
            if self.mode == "single":
                await self._finalize_single()
            elif self.mode == "surah":
                await self._finalize_surah_stop()
            elif self.mode == "open_mic":
                await self._finalize_open_mic()
        except Exception:
            pass
        try:
            await self.ws.close()
        except Exception:
            pass

    async def _finalize_single(self):
        segment = bytes(self.audio[self.ayah_start :])
        chunk_result = None
        if segment and self.surah_id in TAJWEED_ONLY_INDEX and self.ayah_id in TAJWEED_ONLY_INDEX.get(self.surah_id, {}):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                tmp.write(segment)
                tmp_path = tmp.name
            try:
                chunk_result = await asyncio.to_thread(
                    evaluate_audio, self.surah_id, self.ayah_id, tmp_path
                )
            finally:
                os.remove(tmp_path)

        if chunk_result is not None:
            for i, word in enumerate(chunk_result.get("words", [])):
                if i < len(self.session_words):
                    self.session_words[i]["is_read"] = word.get("is_read", False)
                    self.session_words[i]["letters"] = word.get("letters", [])

        payload = {
            "mode": "single",
            "final": True,
            "ayah_id": self.ayah_id,
            "words": self.session_words,
        }
        if chunk_result:
            payload.update({
                "real_text": chunk_result.get("real_text", ""),
                "expected": chunk_result.get("expected", ""),
                "predicted": chunk_result.get("predicted", ""),
                "accuracy": chunk_result.get("accuracy", 0.0),
                "per": chunk_result.get("per", 0.0),
                "diff": chunk_result.get("diff", []),
                "mistakes": chunk_result.get("mistakes", []),
            })
        await self.ws.send_json(payload)

    async def _finalize_surah_stop(self):
        if self.session_ayahs is None or self.ayah_index >= len(self.session_ayahs):
            return
        await self._evaluate_and_finalize_surah()
        is_last = self.ayah_index + 1 >= len(self.session_ayahs)
        # re-send with finished flag
        words_by_ayah = [self.session_read[a] for a in self.session_ayahs]
        scored = [acc for acc in self.ayah_accuracies.values() if acc is not None]
        surah_average = round(sum(scored) / len(scored), 2) if scored else 0.0
        scored_per = [p for p in self.ayah_pers.values() if p is not None]
        surah_per = round(sum(scored_per) / len(scored_per), 2) if scored_per else 0.0
        try:
            await self.ws.send_json({
                "mode": "surah",
                "final": True,
                "current_ayah": self.session_ayahs[self.ayah_index],
                "next_ayah": self.session_ayahs[self.ayah_index + 1] if not is_last else None,
                "ayah_order": self.session_ayahs,
                "words": words_by_ayah,
                "ayah_accuracies": self.ayah_accuracies,
                "surah_average": surah_average,
                "ayah_pers": self.ayah_pers,
                "surah_per": surah_per,
                "finished": is_last,
            })
        except Exception:
            pass

    async def _finalize_open_mic(self):
        full_segment = bytes(self.audio[self.openmic_ayah_start :])
        resolved = self.resolved

        if not resolved and self.stream_pred_raw:
            res = await asyncio.to_thread(resolve_ayah, self.stream_pred_raw, self.allowed_surahs)
            if res:
                resolved = (res[0], res[1])
                confidence = res[3] <= 0.35
            else:
                confidence = False
        elif resolved:
            confidence = True
        else:
            confidence = False

        if not resolved:
            try:
                await self.ws.send_json({
                    "mode": "open_mic",
                    "final": True,
                    "detected": None,
                    "confidence": False,
                    "words": [],
                })
            except Exception:
                pass
            return

        s, a = resolved
        chunk_result = None
        if full_segment and s in TAJWEED_ONLY_INDEX and a in TAJWEED_ONLY_INDEX.get(s, {}):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                tmp.write(full_segment)
                tmp_path = tmp.name
            try:
                chunk_result = await asyncio.to_thread(evaluate_audio, s, a, tmp_path)
            finally:
                os.remove(tmp_path)

        words = []
        if chunk_result:
            for w in chunk_result.get("words", []):
                words.append({"text": w.get("text", ""), "is_read": w.get("is_read", False), "letters": w.get("letters", [])})
        elif s in TAJWEED_ONLY_INDEX and a in TAJWEED_ONLY_INDEX.get(s, {}):
            words = [
                {"text": w["text"], "is_read": False, "letters": []}
                for w in TAJWEED_ONLY_INDEX[s][a]["words"]
            ]

        payload = {
            "mode": "open_mic",
            "final": True,
            "detected": {"surah_id": s, "ayah_id": a},
            "confidence": confidence,
            "words": words,
        }
        if chunk_result:
            payload.update({
                "real_text": chunk_result.get("real_text", ""),
                "expected": chunk_result.get("expected", ""),
                "predicted": chunk_result.get("predicted", ""),
                "accuracy": chunk_result.get("accuracy", 0.0),
                "per": chunk_result.get("per", 0.0),
                "diff": chunk_result.get("diff", []),
                "mistakes": chunk_result.get("mistakes", []),
            })
        await self.ws.send_json(payload)


@app.websocket("/ws/recite")
async def websocket_stream(websocket: WebSocket):
    await websocket.accept()
    print("Client connected for continuous streaming (VAD-free live mode).")

    session = None
    try:
        metadata_str = await websocket.receive_text()
        metadata = json.loads(metadata_str)

        mode = metadata.get("mode")
        raw_surah = metadata.get("surah_id")
        surah_id = int(raw_surah) if raw_surah is not None else None
        ayah_id = int(metadata["ayah_id"]) if "ayah_id" in metadata else None

        if mode is None:
            mode = "surah" if ayah_id is None else "single"

        if mode in ("single", "surah") and surah_id not in TAJWEED_ONLY_INDEX:
            print(f"Warning: Surah {surah_id} not found in index.")
            await websocket.close()
            return

        allowed_surahs = None
        if mode == "open_mic":
            allowed_surahs = {surah_id} if surah_id in TAJWEED_ONLY_INDEX else None

        session_ayahs = None
        word_data_by_ayah = {}

        if mode == "single":
            if surah_id in TAJWEED_ONLY_INDEX and ayah_id in TAJWEED_ONLY_INDEX[surah_id]:
                word_data_by_ayah[surah_id] = {ayah_id: TAJWEED_ONLY_INDEX[surah_id][ayah_id]["words"]}
        elif mode == "surah":
            session_ayahs = get_surah_ayah_ids(surah_id)
            word_data_by_ayah[surah_id] = {
                a: TAJWEED_ONLY_INDEX[surah_id][a]["words"] for a in session_ayahs
            }

        session = Session(
            websocket, mode, surah_id, ayah_id, allowed_surahs,
            word_data_by_ayah, session_ayahs,
        )
        session.start_heartbeat()

        while not session.closed:
            msg = await websocket.receive()
            msg_type = msg.get("type")
            if msg_type == "websocket.disconnect":
                break
            if msg_type == "websocket.receive":
                if "text" in msg:
                    ctrl = json.loads(msg["text"])
                    ctype = ctrl.get("type")
                    if ctype == "next":
                        await session.do_next()
                        if session.closed:
                            break
                    elif ctype == "stop":
                        await session.do_stop()
                        break
                elif "bytes" in msg:
                    session.audio.extend(msg["bytes"])

    except WebSocketDisconnect:
        print("Client disconnected.")
    except Exception as exc:
        print(f"WebSocket error: {exc}")
    finally:
        if session and not session.closed:
            await session.stop_heartbeat()
            session.closed = True
