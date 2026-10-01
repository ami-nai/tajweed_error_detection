import json
import os
import tempfile
import asyncio

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from ml_engine import (
    TAJWEED_ONLY_INDEX,
    evaluate_audio,
    get_surah_ayah_ids,
    decode_live_window,
    CTC_STRIDE,
    GuidedLiveTrie,
    resolve_ayah,
    advance_target,
    strip_diacritics,
    _bytes_to_prediction,
)

app = FastAPI()

EVAL_INTERVAL_SEC = 0.5
MIN_NEW_BYTES = 8000
# Open mic: hysteresis energy gate on heartbeat-delta RMS amplitude (16-bit
# PCM). Drops into silence below SILENCE_RMS_THRESHOLD and only resumes
# decoding above SPEECH_RMS_THRESHOLD, so a mic hovering between the two no
# longer flaps decode on/off every beat. Skipped beats keep the live strip
# and feed_tail free of hallucinated junk phonemes. Tune from the
# 📊 [MIC LEVEL] summaries, not by guessing.
SILENCE_RMS_THRESHOLD = 100
SPEECH_RMS_THRESHOLD = 200
# RMS stats are summarized to the log this often (eligible beats).
MIC_LEVEL_LOG_EVERY = 10
MIN_RESOLVE_TOKENS = 5
# Open mic: if no new word confirms for STALL_ADVANCE_TICKS consecutive beats,
# assume the user has moved on and re-resolve the ayah on a recent-token window.
STALL_ADVANCE_TICKS = 3
# Phase A live decode: each speech beat decodes this trailing context window
# (3 s, exactly the streaming training window) and emits only the
# newly-covered frames (see decode_live_window).
# Right-context lookahead in frames: the emitted slice always ends this far
# before the newest audio so no emitted frame decodes with "silence ahead".
# (No trailing zero-pad: the streaming model never saw padding.)
LIVE_WINDOW_SAMPLES = 48000
LIVE_LEAD_FRAMES = 16
# Phrase-level decisions (advance + in-ayah smooth follow) decode the trailing
# audio in ONE full-context pass instead of the chopped 0.5 s-slice strip,
# which our offline experiment measured as the weakest signal class.
# Capped at 3 s: the streaming model was trained on exactly-3 s windows, so
# longer one-pass decodes are out-of-contract (verify on Kaggle before raising).
STALL_DECODE_SAMPLES = LIVE_WINDOW_SAMPLES  # ~3 s of audio
# Smallest speech burst worth a phrase-level decode (0.5 s at 16 kHz).
MIN_PHRASE_BYTES = 8000
# Mid-burst phrase re-decode cadence: bytes of NEW speech since the last feed
# that forces another phrase-level decode while a long burst is still going.
PHRASE_REDECODE_BYTES = 48000  # ~3 s


def _rms(audio_bytes: bytes) -> float:
    """RMS amplitude of a 16-bit PCM delta; 0.0 for empty input."""
    if not audio_bytes:
        return 0.0
    a = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float64)
    if a.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(a * a)))


def _should_gate(rms: float, currently_gated: bool) -> bool:
    """Hysteresis gate decision: enter silence below SILENCE_RMS_THRESHOLD,
    leave only above SPEECH_RMS_THRESHOLD. Sticky between the two so borderline
    mic levels can't flap decoding on and off."""
    if currently_gated:
        return rms < SPEECH_RMS_THRESHOLD
    return rms < SILENCE_RMS_THRESHOLD


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
        self._om_ticks_at_attempt = -STALL_ADVANCE_TICKS
        self._tick_cnt = 0
        self._stall_ticks = 0
        self._burst_start = None
        self._last_phrase_feed = 0
        self._gate_silence = False
        self._rms_n = 0
        self._rms_sum = 0.0
        self._rms_min = None
        self._rms_max = None
        self._win_prev = None
        self._win_started = False
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

    def _rms_note(self, rms: float):
        """Accumulate per-beat mic levels; every MIC_LEVEL_LOG_EVERY eligible
        beats print a summary so real mic distributions (not guesses) drive
        gate tuning."""
        self._rms_n += 1
        self._rms_sum += rms
        self._rms_min = rms if self._rms_min is None else min(self._rms_min, rms)
        self._rms_max = rms if self._rms_max is None else max(self._rms_max, rms)
        if self._rms_n >= MIC_LEVEL_LOG_EVERY:
            avg = self._rms_sum / self._rms_n
            print(f"📊 [MIC LEVEL] beats={self._rms_n} avg={avg:.0f} "
                  f"min={self._rms_min:.0f} max={self._rms_max:.0f} "
                  f"gated={self._gate_silence}")
            self._rms_n = 0
            self._rms_sum = 0.0
            self._rms_min = None
            self._rms_max = None

    async def _tick_once(self):
        self._tick_cnt += 1
        tok = ""
        if len(self.audio) - self.eval_pos >= MIN_NEW_BYTES:
            delta = bytes(self.audio[self.eval_pos :])
            new_samples = len(delta) // 2
            self.eval_pos = len(self.audio)
            rms = _rms(delta)
            self._rms_note(rms)
            if _should_gate(rms, self._gate_silence):
                # Silence separates utterances: drop the collapse carryover so
                # a repeated phoneme after the pause is not wrongly merged.
                # Everything downstream still runs (interim streaming, stall
                # bookkeeping) with an empty batch of tokens.
                self._win_prev = None
                if not self._gate_silence:
                    self._gate_silence = True
                    print(f"🔇 [ENERGY GATE] silence beat (rms={rms:.0f}): decode skipped")
                    # Burst ended: lock the whole spoken phrase with a CLEAN
                    # full-context decode (recovers words the chopped strip
                    # mangled or clipped at the burst's start).
                    await self._feed_openmic_phrase(burst_end=True)
            else:
                if self._gate_silence:
                    self._gate_silence = False
                    print(f"🔊 [ENERGY GATE] speech beat (rms={rms:.0f}): decoding resumed")
                n = len(self.audio)
                if self._burst_start is None:
                    # Approximate the burst start at the head of this delta so
                    # a burst-end phrase decode can still recover the first
                    # word even when the mic clipped it from the snippet.
                    self._burst_start = max(0, n - new_samples * 2)
                # Phase A: decode the trailing context window, emit only the
                # newly-covered frames (frame-index slicing + LEAD lookahead
                # + collapse carryover inside decode_live_window).
                start = max(0, n - LIVE_WINDOW_SAMPLES)
                window = bytes(self.audio[start:])
                new_frames = int(round(new_samples / CTC_STRIDE))
                tok, self._win_prev = await asyncio.to_thread(
                    decode_live_window, window, new_frames,
                    self._win_prev, not self._win_started, LIVE_LEAD_FRAMES,
                )
                self._win_started = True
                self.stream_pred_raw += tok
                self.feed_tail += tok
                if len(self.audio) - self._last_phrase_feed >= PHRASE_REDECODE_BYTES:
                    # Long uninterrupted burst: keep the phrase pointers
                    # moving mid-burst instead of waiting for the end.
                    await self._feed_openmic_phrase()

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
        # Keep streaming-vocab tokens only: Arabic letters/digits, space, live
        # harakat (َ ُ ِ ّ ْٰٓ) and the model-emitted marks (ڇ ں ۥ ۦ).
        # The special token ۾ must never reach the ayah matcher.
        return "".join(
            c for c in source
            if c != "۾" and (c in " " or c.isalnum() or c in "َُِّْٰٓڇںۥۦ")
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
        self._om_ticks_at_attempt = -STALL_ADVANCE_TICKS
        self._stall_ticks = 0
        self._burst_start = None
        self.feed_tail = ""
        if not is_first:
            self.openmic_ayah_start = len(self.audio)
        print(f"🎯 [OPEN MIC] Detected: surah {s}, ayah {a} (score={score})")
        await self._send_interim()

    async def _maybe_advance_openmic(self):
        if self._om_scanning or self.resolved is None or self.guided is None:
            print(f"🔍 [ADVANCE] skip: scanning={self._om_scanning} "
                  f"resolved={self.resolved} guided={self.guided is not None}")
            return
        # Cooldown: at most one attempt per STALL_ADVANCE_TICKS beats. Each
        # attempt is a self-contained fresh decode of the recent audio, so the
        # old "did the token window grow?" guard is meaningless here.
        beats = self._tick_cnt - self._om_ticks_at_attempt
        if beats < STALL_ADVANCE_TICKS:
            print(f"🔍 [ADVANCE] cooldown: beats={beats}")
            return
        self._om_scanning = True
        self._om_ticks_at_attempt = self._tick_cnt
        decode_str = ""
        try:
            end = len(self.audio)
            start = max(0, end - STALL_DECODE_SAMPLES)
            if end - start < MIN_PHRASE_BYTES:
                print(f"🔍 [ADVANCE] need-more-audio: bytes={end - start}")
                return
            # A: fresh phrase-level decode of the RECENT AUDIO (the same
            # one-shot signal as the final's evaluate_audio) instead of the
            # chopped 0.5 s-slice strip, which our offline experiment measured
            # as the weakest representation and never aligned within budget.
            decode_str = await asyncio.to_thread(
                _bytes_to_prediction, bytes(self.audio[start:end])
            )
            clean = self._clean_openmic_tail(decode_str)
            if len(clean) < MIN_RESOLVE_TOKENS:
                print(f"🔍 [ADVANCE] need-more-tokens: clean={len(clean)}")
                return
            # Match-from-anywhere alignment (see advance_target) so a window
            # that starts mid-ayah can still anchor the NEXT ayah.
            # resolve_ayah's prefix trie cannot do this, which is why the old
            # strip-based live advance kept failing even when the correct
            # ayah's tokens were sitting in the tail.
            res = await asyncio.to_thread(
                advance_target, clean, self.surah_id, self.ayah_id, self.allowed_surahs
            )
        finally:
            self._om_scanning = False
        if res is None:
            print(f"🔍 [ADVANCE] resolve=None (ambiguous/insufficient) decode={decode_str[-32:]!r}")
            return
        s, a, score = res
        # advance_target can only emit later ayahs, but keep the guard as
        # defense in depth against stubs/regressions.
        if (s, a) <= (self.surah_id, self.ayah_id):
            print(f"🔍 [ADVANCE] blocked: winner={(s, a)} <= current={(self.surah_id, self.ayah_id)} "
                  f"score={score}")
            return
        print(f"🔍 [ADVANCE] commit: {(s, a)} score={score}")
        await self._commit_openmic(s, a, score)

    async def _feed_openmic_phrase(self, burst_end=False):
        """B (smooth in-ayah): phrase-level full-context decode of the current
        speech burst, fed to the guided trie so words lock from a CLEAN decode
        (not the chopped strip). Called at burst end (silence transition,
        burst_end=True) and every PHRASE_REDECODE_BYTES of new speech during a
        long burst (burst_end=False, the burst stays open so the final tail is
        still re-decoded at the real end)."""
        if self.mode != "open_mic" or self.resolved is None or self.guided is None:
            return
        b = self._burst_start
        if b is None:
            return
        end = len(self.audio)
        if end - b < MIN_PHRASE_BYTES:
            return
        # Decode the burst (or the new audio since the last feed plus a small
        # overlap for cross-word context). One full-context pass per burst so
        # the trie sees the same high-quality signal the final uses.
        start = max(b, self._last_phrase_feed - MIN_PHRASE_BYTES)
        if end - start < MIN_PHRASE_BYTES:
            start = max(0, end - MIN_PHRASE_BYTES)
        if end - start < MIN_PHRASE_BYTES:
            return
        phrase = await asyncio.to_thread(
            _bytes_to_prediction, bytes(self.audio[start:end])
        )
        if burst_end:
            self._burst_start = None
        self._last_phrase_feed = end
        self.guided.feed(phrase)
        newly = self.guided.confirm()
        if newly:
            self._stall_ticks = 0
            self._merge_newly(newly)
            await self._send_interim()

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
                    "active_index": self.guided.active_index if self.guided else None,
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
                    "active_index": self.guided.active_index if self.guided else None,
                })
            elif self.mode == "open_mic":
                await self.ws.send_json({
                    "mode": "open_mic",
                    "final": False,
                    "detected": {"surah_id": self.surah_id, "ayah_id": self.ayah_id},
                    "ayah_id": self.ayah_id,
                    "words": self.session_words,
                    "live": self.stream_pred_raw,
                    "active_index": self.guided.active_index if self.guided else None,
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

        # Multi-ayah coverage: the "real transcription" should reflect how far
        # the user actually recited, not just the last committed ayah. Re-run
        # the advance matcher over the authoritative decode and extend the
        # expected text with every later ayah it genuinely matches.
        covered_last = a
        decode_str = (chunk_result or {}).get("predicted", "") or self.stream_pred_raw
        if self.resolved is not None and decode_str:
            clean_decode = self._clean_openmic_tail(decode_str)
            cov = await asyncio.to_thread(
                advance_target, clean_decode, s, a, self.allowed_surahs
            )
            if cov:
                covered_last = cov[1]
                print(f"📚 [OPEN MIC] final coverage: ayahs {a}..{covered_last}")

        expected = ""
        if covered_last > a:
            expected = self._openmic_expected_span(s, a, covered_last)
        elif chunk_result:
            expected = chunk_result.get("expected", "")

        payload = {
            "mode": "open_mic",
            "final": True,
            "detected": {"surah_id": s, "ayah_id": a},
            "confidence": confidence,
            "words": words,
        }
        if chunk_result:
            payload.update({
                "real_text": expected,
                "expected": expected,
                "predicted": chunk_result.get("predicted", ""),
                "accuracy": chunk_result.get("accuracy", 0.0),
                "per": chunk_result.get("per", 0.0),
                "diff": chunk_result.get("diff", []),
                "mistakes": chunk_result.get("mistakes", []),
            })
        await self.ws.send_json(payload)

    def _openmic_expected_span(self, s: int, low: int, high: int) -> str:
        """Canonical expected text for the ayah range [low, high] of surah s."""
        parts = []
        for a_id in range(low, high + 1):
            if a_id in TAJWEED_ONLY_INDEX.get(s, {}):
                parts.append(TAJWEED_ONLY_INDEX[s][a_id]["expected_full"])
        return " ".join(parts)


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
