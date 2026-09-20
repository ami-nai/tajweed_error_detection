"""In-process WebSocket smoke test for the VAD-free live protocol.

Run from td_backend with the fastapienv interpreter:
    ./fastapienv/bin/python test_ws_smoke.py
"""

import asyncio
import json
import time

from fastapi.testclient import TestClient
import main

client = TestClient(main.app)


class _FakeWs:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


def _make_session():
    return main.Session(_FakeWs(), "open_mic", 111, None, None, {}, None)


def _stub_resolve(result):
    collected = {"calls": 0}

    def _inner(pred_str, surahs=None, err_rate=0.35, min_tokens=5):
        collected["calls"] += 1
        return result

    return _inner, collected


async def _advance_commits_later_ayah():
    s = _make_session()
    s.resolved = (111, 1)
    s.surah_id = 111
    s.ayah_id = 1
    s._init_guided()
    s.guided._idx = len(s.guided.words)  # exhaust the first ayah
    s.audio.extend(b"\x00\x00" * 16000)  # pretend audio has accumulated
    s.feed_tail = "يبايدواحمليدةوايديحمادننوليبدونا"
    stub, _ = _stub_resolve((111, 2, 5, 0.1, 25))
    main.resolve_ayah = stub
    await s._maybe_advance_openmic()
    assert s.resolved == (111, 2), s.resolved
    assert s.ayah_id == 2
    assert s.feed_tail == "", s.feed_tail
    assert s.openmic_ayah_start == len(s.audio), s.openmic_ayah_start
    n_words = len(main.TAJWEED_ONLY_INDEX[111][2]["words"])
    assert len(s.session_words) == n_words, len(s.session_words)
    assert len(s.ws.sent) == 1 and s.ws.sent[0]["final"] is False
    assert s.ws.sent[0]["detected"] == {"surah_id": 111, "ayah_id": 2}
    print("ADVANCE OK -> 111:1 -> 111:2, words:", n_words)


async def _same_or_backward_never_advances():
    # Resolve returns the SAME ayah -> must not advance.
    s = _make_session()
    s.resolved = (111, 1)
    s.surah_id = 111
    s.ayah_id = 1
    s._init_guided()
    s.guided._idx = len(s.guided.words)
    s.feed_tail = "فياداومالنبنرتدب"
    stub, collected = _stub_resolve((111, 1, 2, 0.2, 20))
    main.resolve_ayah = stub
    await s._maybe_advance_openmic()
    assert s.resolved == (111, 1), s.resolved
    assert s.feed_tail == "فياداومالنبنرتدب", s.feed_tail

    # Resolve returns a BACKWARD ayah -> must not advance.
    s2 = _make_session()
    s2.resolved = (111, 2)
    s2.surah_id = 111
    s2.ayah_id = 2
    s2._init_guided()
    s2.guided._idx = len(s2.guided.words)
    s2.feed_tail = "منولهولنحيوااللهدالاعلىعلي"
    stub2, _ = _stub_resolve((111, 1, 9, 0.3, 30))
    main.resolve_ayah = stub2
    await s2._maybe_advance_openmic()
    assert s2.resolved == (111, 2), s2.resolved

    assert s.ws.sent == [] and s2.ws.sent == []
    print("FORWARD-GUARD OK -> same/backward rejected")


async def _cooldown_prevents_spam():
    s = _make_session()
    s.resolved = (111, 1)
    s.surah_id = 111
    s.ayah_id = 1
    s._init_guided()
    s.guided._idx = len(s.guided.words)
    s.feed_tail = "لاييداوامالحن"
    s._om_last_attempt = 10 ** 9  # a fresh scan is far above the cooldown budget
    stub, collected = _stub_resolve((111, 2, 5, 0.1, 25))
    main.resolve_ayah = stub
    await s._maybe_advance_openmic()
    assert collected["calls"] == 0, "resolve must not run during cooldown"
    assert s.resolved == (111, 1), s.resolved
    print("COOLDOWN OK -> resolve skipped during cooldown")


async def _first_commit_keeps_audio_marker():
    s = _make_session()
    s.audio.extend(b"\x00\x00" * 8000)
    s.feed_tail = "الهودايةطبيببوامنركلعالم"
    stub, _ = _stub_resolve((111, 1, 4, 0.12, 22))
    main.resolve_ayah = stub
    await s._commit_openmic(111, 1, 0.12)
    assert s.resolved == (111, 1)
    assert s.openmic_ayah_start == 0, s.openmic_ayah_start
    assert len(s.session_words) == len(main.TAJWEED_ONLY_INDEX[111][1]["words"])
    print("FIRST-COMMIT OK -> marker stays 0, words seeded")


async def _windowed_resolve_and_trim():
    # feed_tail is polluted with the whole previous ayah; re-resolution must use
    # only the RECENT window and drop the stale prefix.
    s = _make_session()
    s.resolved = (111, 1)
    s.surah_id = 111
    s.ayah_id = 1
    s._init_guided()
    s.guided._idx = len(s.guided.words)
    s.feed_tail = ("A" * 200) + ("B" * 200)
    window_len = max(
        main.MIN_ADVANCE_WINDOW_TOKENS,
        2 * len(main.strip_diacritics(main.TAJWEED_ONLY_INDEX[111][1]["expected_full"])),
    )
    calls = []

    def stub(pred_str, surahs=None, err_rate=0.35, min_tokens=5):
        calls.append(pred_str)
        return (111, 1, 2, 0.2, 20)  # same ayah -> forward-guard blocks the commit

    main.resolve_ayah = stub
    await s._maybe_advance_openmic()
    assert len(calls) == 1
    assert "A" not in calls[0], f"stale prefix leaked into resolve window: {calls[0]!r}"
    assert len(s.feed_tail) == window_len, (len(s.feed_tail), window_len)
    assert s.feed_tail == calls[0]
    assert all(c == "B" for c in s.feed_tail)
    print("WINDOWED-RESOLVE OK -> stale prefix trimmed, window =", window_len)


async def _stall_triggers_advance_without_full_confirmation():
    # The user's words never fully confirm (crude phonemizer); the stall
    # counter must still advance the ayah after a few unproductive beats.
    s = _make_session()
    s.resolved = (111, 1)
    s.surah_id = 111
    s.ayah_id = 1
    s._init_guided()
    original_predict = main.predict_phonemes_bytes
    main.predict_phonemes_bytes = lambda _b: "zzzzzzzz"  # never matches the targets
    stub, _ = _stub_resolve((111, 2, 5, 0.1, 25))
    main.resolve_ayah = stub
    try:
        for _ in range(main.STALL_ADVANCE_TICKS):
            s.audio.extend(b"\x00\x00" * 4000)  # +8000 bytes for the next beat
            await s.tick()
    finally:
        main.predict_phonemes_bytes = original_predict
    assert s.resolved == (111, 2), s.resolved
    assert any(
        msg.get("detected") == {"surah_id": 111, "ayah_id": 2}
        for msg in s.ws.sent
    ), s.ws.sent
    print("STALL-TRIGGER OK -> advanced on stall without full confirmation")


async def _low_bytes_still_streams_live():
    # A quiet heartbeat (< MIN_NEW_BYTES of new audio) must still push the live
    # phoneme strip; predict must NOT be called on that beat.
    s = _make_session()
    s.resolved = (111, 1)
    s.surah_id = 111
    s.ayah_id = 1
    s._init_guided()
    s.audio.extend(b"\x00\x00" * 100)  # far below MIN_NEW_BYTES
    calls = {"n": 0}
    original_predict = main.predict_phonemes_bytes

    def boom(_b):
        calls["n"] += 1
        raise AssertionError("predict must not run on a low-byte beat")

    main.predict_phonemes_bytes = boom
    try:
        await s.tick()
    finally:
        main.predict_phonemes_bytes = original_predict
    assert calls["n"] == 0
    assert s.closed is False
    assert len(s.ws.sent) == 1, s.ws.sent
    assert "live" in s.ws.sent[0]
    assert s.ws.sent[0]["detected"] == {"surah_id": 111, "ayah_id": 1}
    print("LOW-BYTES LIVE OK -> interim streams without decode")


async def _predict_error_keeps_session_alive():
    s = _make_session()
    s.resolved = (111, 1)
    s.surah_id = 111
    s.ayah_id = 1
    s._init_guided()
    s.audio.extend(b"\x00\x00" * 8000)  # >= MIN_NEW_BYTES -> decode runs
    original_predict = main.predict_phonemes_bytes

    def boom(_b):
        raise RuntimeError("model exploded")

    main.predict_phonemes_bytes = boom
    try:
        await s.tick()
    finally:
        main.predict_phonemes_bytes = original_predict
    assert s.closed is False
    s.audio.extend(b"\x00\x00" * 8000)
    main.predict_phonemes_bytes = lambda _b: ""
    await s.tick()
    assert s.closed is False
    assert len(s.ws.sent) == 1
    print("PREDICT-ERROR OK -> session survives a bad inference beat")


async def _send_failure_keeps_session_alive():
    class _BoomWs:
        def __init__(self):
            self.sent = []

        async def send_json(self, payload):
            raise ConnectionError("nope")

    s = main.Session(_BoomWs(), "open_mic", 111, None, None, {}, None)
    s.resolved = (111, 1)
    s.surah_id = 111
    s.ayah_id = 1
    s._init_guided()
    s.audio.extend(b"\x00\x00" * 100)  # low byte -> resolved branch sends interim
    await s.tick()
    assert s.closed is False, "send failure must not kill the session"
    print("SEND-FAILURE OK -> session survives a failed interim send")


async def _pre_detection_streams_live_each_tick():
    s = _make_session()  # open_mic, resolved=None
    s.feed_tail = ""  # clean tokens < MIN_RESOLVE_TOKENS
    s.audio.extend(b"\x00\x00" * 100)  # low byte -> live-only branch
    original_predict = main.predict_phonemes_bytes
    main.predict_phonemes_bytes = lambda _b: ""
    try:
        for _ in range(3):
            await s.tick()
    finally:
        main.predict_phonemes_bytes = original_predict
    assert len(s.ws.sent) == 3, s.ws.sent
    for msg in s.ws.sent:
        assert msg["mode"] == "open_mic"
        assert "live" in msg
        assert "detected" not in msg
    print("PRE-DETECT LIVE OK -> strip streams before any ayah is resolved")


def run_single():
    with client.websocket_connect("/ws/recite") as ws:
        ws.send_text(json.dumps({"mode": "single", "surah_id": 112, "ayah_id": 1}))
        # ~0.5s of silence (decodes to blank tokens -> no interim words)
        ws.send_bytes(b"\x00\x00" * 8000)
        time.sleep(1.3)
        ws.send_text(json.dumps({"type": "stop"}))
        final = None
        for _ in range(20):
            msg = json.loads(ws.receive_text())
            if msg.get("final") is True:
                final = msg
                break
        assert final is not None, "no final message"
        assert final["mode"] == "single"
        assert final["words"] is not None
        assert len(final["words"]) == 4, f"112:1 has 4 words, got {len(final['words'])}"
        print("SINGLE OK -> words:", len(final["words"]), "accuracy:", final.get("accuracy"))


def run_openmic():
    with client.websocket_connect("/ws/recite") as ws:
        ws.send_text(json.dumps({"mode": "open_mic", "surah_id": None}))
        ws.send_bytes(b"\x00\x00" * 8000)
        time.sleep(1.3)
        ws.send_text(json.dumps({"type": "stop"}))
        final = None
        for _ in range(20):
            msg = json.loads(ws.receive_text())
            if msg.get("final") is True:
                final = msg
                break
        assert final is not None, "no final message"
        assert final["mode"] == "open_mic"
        assert "detected" in final
        print("OPENMIC OK -> detected:", final["detected"], "confidence:", final.get("confidence"))


def run_surah():
    with client.websocket_connect("/ws/recite") as ws:
        ws.send_text(json.dumps({"mode": "surah", "surah_id": 112}))
        ws.send_bytes(b"\x00\x00" * 8000)
        time.sleep(1.3)
        ws.send_text(json.dumps({"type": "next"}))
        got = []
        for _ in range(20):
            msg = json.loads(ws.receive_text())
            got.append(msg)
            # surah final for ayah 1 includes next_ayah 2
            if msg.get("final") is True and msg.get("current_ayah") == 1:
                assert msg.get("next_ayah") == 2, msg
                print("SURAH OK -> ayah 1 finalized, next:", msg.get("next_ayah"))
                break
        ws.send_text(json.dumps({"type": "stop"}))
        print("SURAH STOP OK")


if __name__ == "__main__":
    print("=== SINGLE ===")
    run_single()
    print("=== OPEN MIC ===")
    run_openmic()
    print("=== SURAH ===")
    run_surah()
    print("=== OPEN MIC ADVANCE UNITS ===")
    asyncio.run(_advance_commits_later_ayah())
    asyncio.run(_same_or_backward_never_advances())
    asyncio.run(_cooldown_prevents_spam())
    asyncio.run(_first_commit_keeps_audio_marker())
    asyncio.run(_windowed_resolve_and_trim())
    asyncio.run(_stall_triggers_advance_without_full_confirmation())
    asyncio.run(_low_bytes_still_streams_live())
    asyncio.run(_predict_error_keeps_session_alive())
    asyncio.run(_send_failure_keeps_session_alive())
    asyncio.run(_pre_detection_streams_live_each_tick())
    print("ALL SMOKE TESTS PASSED")