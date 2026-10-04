"""In-process WebSocket smoke test for the VAD-free live protocol.

Run from td_backend with the fastapienv interpreter:
    ./fastapienv/bin/python test_ws_smoke.py
"""

import asyncio
import json
import struct
import time

from fastapi.testclient import TestClient
import main
import ml_engine

client = TestClient(main.app)


class _FakeWs:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


def _noise_bytes(byte_len: int) -> bytes:
    """16-bit PCM that reads as 'speech' to the energy gate (RMS 500), fully
    deterministic so the fast smoke fixtures keep working once silence-gating
    is applied. Plain zero-filled audio is silence by definition and would be
    skipped by the gate before decode is even called."""
    return b"".join(
        struct.pack("<h", 500 if i % 2 == 0 else -500) for i in range(byte_len // 2)
    )


def _make_session():
    return main.Session(_FakeWs(), "open_mic", 111, None, None, {}, None)


def _stub_resolve(result):
    collected = {"calls": 0}

    def _inner(pred_str, surahs=None, err_rate=0.35, min_tokens=5):
        collected["calls"] += 1
        return result

    return _inner, collected


def _stub_advance(result):
    collected = {"calls": 0}

    def _inner(pred_str, cur_s, cur_a, allowed_surahs=None):
        collected["calls"] += 1
        return result

    return _inner, collected


def _stub_decode(decode_str):
    """Pin the phrase-level decoder so advance/burst tests never touch the
    real model. main imports _bytes_to_prediction into its namespace."""
    main._bytes_to_prediction = lambda _b: decode_str


async def _advance_commits_later_ayah():
    s = _make_session()
    s.resolved = (111, 1)
    s.surah_id = 111
    s.ayah_id = 1
    s._init_guided()
    s.guided._idx = len(s.guided.words)  # exhaust the first ayah
    s.audio.extend(b"\x00\x00" * 16000)  # pretend audio has accumulated
    s.feed_tail = "يبايدواحمليدةوايديحمادننوليبدونا"
    _stub_decode("تب وتب لهب يدا ابي")
    stub, _ = _stub_advance((111, 2, 0.92))
    main.advance_target = stub
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
    # advance returns the SAME ayah -> the forward guard blocks the commit.
    s = _make_session()
    s.resolved = (111, 1)
    s.surah_id = 111
    s.ayah_id = 1
    s._init_guided()
    s.guided._idx = len(s.guided.words)
    s.audio.extend(b"\x00\x00" * 16000)
    _stub_decode("فياداومالنبنرتدب")
    stub, _ = _stub_advance((111, 1, 0.8))
    main.advance_target = stub
    await s._maybe_advance_openmic()
    assert s.resolved == (111, 1), s.resolved

    # advance returns a BACKWARD ayah -> must not advance.
    s2 = _make_session()
    s2.resolved = (111, 2)
    s2.surah_id = 111
    s2.ayah_id = 2
    s2._init_guided()
    s2.guided._idx = len(s2.guided.words)
    s2.audio.extend(b"\x00\x00" * 16000)
    _stub_decode("منولهولنحيوااللهدالاعلىعلي")
    stub2, _ = _stub_advance((111, 1, 0.7))
    main.advance_target = stub2
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
    s._om_ticks_at_attempt = s._tick_cnt  # 0 beats elapsed -> still cooling down
    stub, collected = _stub_advance((111, 2, 0.85))
    main.advance_target = stub
    await s._maybe_advance_openmic()
    assert collected["calls"] == 0, "advance must not run during cooldown"
    assert s.resolved == (111, 1), s.resolved
    print("COOLDOWN OK -> advance skipped during cooldown")


async def _advance_cooldown_recovers():
    # The OLD absolute "+5 tokens" guard locked a capped window out forever
    # (clean pinned at ~46, spam-logged 'cooldown' until stop). The new rule
    # must retry at the SAME clean length once STALL_ADVANCE_TICKS beats pass.
    s = _make_session()
    s.resolved = (111, 1)
    s.surah_id = 111
    s.ayah_id = 1
    s._init_guided()
    s.guided._idx = len(s.guided.words)
    s.audio.extend(b"\x00\x00" * 16000)
    _stub_decode("لاييداوامال حن وتب ما اغنى عنه ماله")
    s._om_ticks_at_attempt = s._tick_cnt - main.STALL_ADVANCE_TICKS
    stub, collected = _stub_advance((111, 2, 0.9))
    main.advance_target = stub
    await s._maybe_advance_openmic()
    assert collected["calls"] == 1, "must retry once the cooldown beats elapsed"
    assert s.resolved == (111, 2), s.resolved
    print("COOLDOWN-RECOVERY OK -> pinned window still retries after cooldown")


async def _advance_uses_fresh_audio():
    # O1: the advance decision is fuelled by a FRESH full-context decode of the
    # recent audio, never the chopped strip / feed_tail. Pin that the audio
    # slice passed to _bytes_to_prediction is the trailing window and that the
    # matcher receives its cleaned decode.
    s = _make_session()
    s.resolved = (111, 1)
    s.surah_id = 111
    s.ayah_id = 1
    s._init_guided()
    s.guided._idx = len(s.guided.words)
    s.audio.extend(b"\x00" * main.STALL_DECODE_BYTES)
    captured = {}

    def fake_decode(b):
        captured["bytes"] = b
        return "تب لهب وتب ما اغنى عنه ماله وما كسب"

    main._bytes_to_prediction = fake_decode
    calls = []
    main.advance_target = lambda pred, cs, ca, allowed=None: (calls.append(pred), (111, 2, 0.8))[1]
    s._om_ticks_at_attempt = s._tick_cnt - main.STALL_ADVANCE_TICKS
    await s._maybe_advance_openmic()
    expected_slice = bytes(s.audio[-main.STALL_DECODE_BYTES:])
    assert captured.get("bytes") == expected_slice, (len(captured.get("bytes", b"")), len(expected_slice))
    assert len(calls) == 1
    assert calls[0] == "تب لهب وتب ما اغنى عنه ماله وما كسب", calls[0]
    assert s.resolved == (111, 2), s.resolved
    print("FRESH-AUDIO OK -> advance decodes trailing audio, not the strip")


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


async def _stall_triggers_advance_without_full_confirmation():
    # The user's words never fully confirm (crude phonemizer); the stall
    # counter must still advance the ayah after a few unproductive beats.
    s = _make_session()
    s.resolved = (111, 1)
    s.surah_id = 111
    s.ayah_id = 1
    s._init_guided()
    original_decode = main.decode_live_window
    # Phase A: the tick decodes a trailing window, not the raw delta.
    main.decode_live_window = lambda _w, _n, _p, _f, _l: ("zzzzzzzz", None)  # never matches
    _stub_decode("وتبه لهب لما تب")
    stub, _ = _stub_advance((111, 2, 0.85))
    main.advance_target = stub
    try:
        for _ in range(main.STALL_ADVANCE_TICKS):
            s.audio.extend(_noise_bytes(8000))  # +8000 bytes (speech-like) for the next beat
            await s.tick()
    finally:
        main.decode_live_window = original_decode
    assert s.resolved == (111, 2), s.resolved
    assert any(
        msg.get("detected") == {"surah_id": 111, "ayah_id": 2}
        for msg in s.ws.sent
    ), s.ws.sent
    print("STALL-TRIGGER OK -> advanced on stall without full confirmation")


async def _low_bytes_still_streams_live():
    # A quiet heartbeat (< MIN_NEW_BYTES of new audio) must still push the live
    # phoneme strip; the window decode must NOT run on that beat.
    s = _make_session()
    s.resolved = (111, 1)
    s.surah_id = 111
    s.ayah_id = 1
    s._init_guided()
    s.audio.extend(b"\x00\x00" * 100)  # far below MIN_NEW_BYTES
    calls = {"n": 0}
    original_decode = main.decode_live_window

    def boom(_w, _n, _p, _f, _l):
        calls["n"] += 1
        raise AssertionError("window decode must not run on a low-byte beat")

    main.decode_live_window = boom
    try:
        await s.tick()
    finally:
        main.decode_live_window = original_decode
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
    s.audio.extend(_noise_bytes(8000))  # >= MIN_NEW_BYTES + speech-like (decode runs)
    original_decode = main.decode_live_window

    def boom(_w, _n, _p, _f, _l):
        raise RuntimeError("model exploded")

    main.decode_live_window = boom
    try:
        await s.tick()
    finally:
        main.decode_live_window = original_decode
    assert s.closed is False
    s.audio.extend(_noise_bytes(8000))
    main.decode_live_window = lambda _w, _n, _p, _f, _l: ("", None)
    try:
        await s.tick()
    finally:
        main.decode_live_window = original_decode
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
    original_decode = main.decode_live_window
    main.decode_live_window = lambda _w, _n, _p, _f, _l: ("", None)
    try:
        for _ in range(3):
            await s.tick()
    finally:
        main.decode_live_window = original_decode
    assert len(s.ws.sent) == 3, s.ws.sent
    for msg in s.ws.sent:
        assert msg["mode"] == "open_mic"
        assert "live" in msg
        assert "detected" not in msg
    print("PRE-DETECT LIVE OK -> strip streams before any ayah is resolved")


async def _silence_gate_skips_decode():
    # A beat with >= MIN_NEW_BYTES of PURE SILENCE must skip the window decode
    # entirely (energy gate) while still streaming the live interim. This pins
    # the exact silent-hole Claude flagged in _predict_error_keeps_session_alive:
    # without the gate running here, the covered path would silently test nothing.
    s = _make_session()
    s.resolved = (111, 1)
    s.surah_id = 111
    s.ayah_id = 1
    s._init_guided()
    s.audio.extend(b"\x00\x00" * 8000)  # >= MIN_NEW_BYTES but RMS 0 -> gated
    calls = {"n": 0}
    original_decode = main.decode_live_window

    def boom(_w, _n, _p, _f, _l):
        calls["n"] += 1
        raise AssertionError("window decode must not run on a silence-gated beat")

    main.decode_live_window = boom
    try:
        await s.tick()
    finally:
        main.decode_live_window = original_decode
    assert calls["n"] == 0
    assert s.closed is False
    assert s.stream_pred_raw == ""
    assert len(s.ws.sent) == 1, s.ws.sent
    assert "live" in s.ws.sent[0]
    print("SILENCE-GATE OK -> zero-RMS delta skips decode but still streams live")


def _gate_stickiness():
    # Hysteresis: borderline RMS must not flap. Ungated stays open through the
    # dead zone (100..200); gated stays shut until strong speech. A single
    # threshold would toggle on every 150-ish beat.
    assert main._should_gate(50, False) is True
    assert main._should_gate(99, False) is True
    assert main._should_gate(100, False) is False
    assert main._should_gate(150, False) is False
    assert main._should_gate(150, True) is True
    assert main._should_gate(199, True) is True
    assert main._should_gate(200, True) is False
    assert main._should_gate(500, True) is False
    assert main._should_gate(500, False) is False
    assert main._should_gate(0.0, False) is True
    print("GATE-STICKINESS OK -> 100/200 hysteresis holds the dead zone")


async def _frame_stride_probe():
    # Empirical pin on the frame-index math Phase A slicing depends on: two
    # inputs differing by exactly 10 CTC frames' worth of samples must decode
    # to frame counts differing by exactly 10. The delta is exact even though
    # absolute counts carry a constant conv edge effect, so this fails loudly
    # if a future backbone ever breaks the slicer's core assumption.
    n1 = ml_engine.CTC_STRIDE * 50
    n2 = n1 + ml_engine.CTC_STRIDE * 10
    f1 = len(ml_engine._infer_pred_ids(_noise_bytes(n1 * 2)))
    f2 = len(ml_engine._infer_pred_ids(_noise_bytes(n2 * 2)))
    assert f2 - f1 == 10, (f1, f2)
    assert ml_engine.CTC_STRIDE * ml_engine.CTC_PAD_FRAMES == ml_engine.CTC_PAD_SAMPLES
    print("FRAME-STRIDE OK -> +10 frames per +3200 samples; stride/pad consistent")


async def _windowed_decode_emits_only_new_tail():
    # Phase A slicing with stubbed frame ids (vocab-agnostic: look up ت/َ/ب,
    # 1=blank): consecutive windows tile exactly, and the collapse carryover
    # suppresses a boundary duplicate that a fresh collapse would emit twice.
    _T = ml_engine.vocab.phoneme2id["ت"]
    _A = ml_engine.vocab.phoneme2id["َ"]
    _B = ml_engine.vocab.phoneme2id["ب"]
    # Runs placed so the pad=0 slices ([0:24], [24:34], [34:44] with lead=16)
    # tile exactly: T crosses the first cut (proves carryover suppression),
    # A sits fully inside the third slice.
    ids1 = [1] * 20 + [_T] * 10 + [1] * 10  # 40 frames
    ids2 = ids1 + [_A] * 10                             # 50 frames
    ids3 = ids2 + [1] * 10                             # 60 frames
    frames = iter([ids1, ids2, ids3])
    original_infer = ml_engine._infer_pred_ids
    ml_engine._infer_pred_ids = lambda _b: list(next(frames))
    try:
        o1, p1 = ml_engine.decode_live_window(b"x", 0, None, True, main.LIVE_LEAD_FRAMES)
        o2, p2 = ml_engine.decode_live_window(b"x", 10, p1, False, main.LIVE_LEAD_FRAMES)
        o3, p3 = ml_engine.decode_live_window(b"x", 10, p2, False, main.LIVE_LEAD_FRAMES)
    finally:
        ml_engine._infer_pred_ids = original_infer
    assert o1 == "ت", repr(o1)
    # Without the carryover this slice would re-emit "ت" (prove the test bites):
    assert ml_engine._collapse_pred_ids(ids2[24:34])[0] == "ت"
    assert o2 == "", repr(o2)
    assert p2 is None
    assert o3 == "َ", repr(o3)
    assert o1 + o2 + o3 == "تَ"
    print("WINDOWED-SLICE OK -> tiles exactly, boundary dup suppressed")


async def _advance_from_mid_ayah_matches_tail():
    # The prefix trie cannot anchor a tail that starts mid-ayah. The new
    # match-anywhere alignment must find 111:2 even when the window begins
    # inside 111:1 and only reaches the start of 111:2.
    exp2 = ml_engine.TAJWEED_ONLY_INDEX[111][2]["expected_full"]
    tail = "لهب وتب " + exp2 + " م زيد"
    res = ml_engine.advance_target(tail, 111, 1, {111})
    assert res is not None, res
    assert res[1] == 2, res
    # Junk that matches nothing must not commit a bogus advance.
    assert ml_engine.advance_target("zzzz zzzz", 111, 1, {111}) is None
    # A tail that only covers the END of 111:1 must not invent a later ayah.
    exp1 = ml_engine.TAJWEED_ONLY_INDEX[111][1]["expected_full"]
    assert ml_engine.advance_target(exp1, 111, 1, {111}) is None
    # Unknown surah / already-at-last-ayah boundaries.
    assert ml_engine.advance_target(exp2, None, 1, {111}) is None
    last = max(ml_engine.get_surah_ayah_ids(111))
    assert ml_engine.advance_target(exp2, 111, last, {111}) is None
    print("MID-AYAH MATCH OK -> window starting mid-ayah still anchors 111:2")


def _multi_ayah_expected_span():
    exp1 = ml_engine.TAJWEED_ONLY_INDEX[111][1]["expected_full"]
    exp2 = ml_engine.TAJWEED_ONLY_INDEX[111][2]["expected_full"]
    span = _make_session()._openmic_expected_span(111, 1, 2)
    assert span == exp1 + " " + exp2, span
    assert _make_session()._openmic_expected_span(111, 1, 1) == exp1
    print("MULTI-AYAH SPAN OK -> final expected text covers the recited ayahs")


async def _guided_skips_clipped_first_word():
    # B: if word 0's phonemes never appear in the decode (mic clipped the first
    # word) but word 1 clearly matches, the ordered trie must advance past the
    # gap instead of freezing the spotlight on index 0.
    s = _make_session()
    s.resolved = (111, 1)
    s.surah_id = 111
    s.ayah_id = 1
    s._init_guided()
    g = s.guided
    assert g.targets[0] and g.targets[1], "fixture moved"
    g.feed(g.targets[1])  # only word 1's phonemes make it into the stream
    newly = g.confirm()
    assert [n["index"] for n in newly] == [1], newly  # يَدَا locked, تَبَّتْ skipped
    assert g.active_index == 2, g.active_index
    assert g._tail == "", g._tail
    print("SKIP-TOLERANCE OK -> clipped first word does not freeze the pointer")


async def _burst_phrase_feed_locks_whole_phrase():
    # B: a burst-end phrase-level decode of the real expected text must lock
    # EVERY word of the ayah from the clean decode (not the chopped strip).
    s = _make_session()
    s.resolved = (111, 1)
    s.surah_id = 111
    s.ayah_id = 1
    s._init_guided()
    s.audio.extend(b"\x00\x00" * 16000)
    s._burst_start = 0
    exp1 = main.TAJWEED_ONLY_INDEX[111][1]["expected_full"]
    _stub_decode(exp1)
    await s._feed_openmic_phrase(burst_end=True)
    assert s._burst_start is None, "burst consumed"
    assert s._last_phrase_feed == len(s.audio)
    assert s.guided.active_index is None, s.guided.active_index
    assert all(w["is_read"] for w in s.session_words), s.session_words
    print("BURST-PHRASE OK -> clean phrase decode locks every word of the ayah")


def run_single():
    with client.websocket_connect("/ws/recite") as ws:
        ws.send_text(json.dumps({"mode": "single", "surah_id": 112, "ayah_id": 1}))
        # ~0.5s of speech-like audio (keeps the live-decode path exercised)
        ws.send_bytes(_noise_bytes(8000))
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
        ws.send_bytes(_noise_bytes(8000))
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
        ws.send_bytes(_noise_bytes(8000))
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


def _window_byte_contract():
    # Regression pin for the samples-vs-bytes unit bug: session.audio is a
    # bytearray of int16 PCM, so every window must be denominated in BYTES.
    # The old sample-counted values silently halved every window (3 s -> 1.5 s),
    # which starved advance_target of the coverage it needs.
    assert main.LIVE_WINDOW_BYTES == 96000 == 48000 * 2
    assert main.STALL_DECODE_BYTES == 96000
    assert main.PHRASE_REDECODE_BYTES == 96000
    assert main.MIN_PHRASE_BYTES == 16000 == 8000 * 2
    assert main.MIN_NEW_BYTES == 8000
    print("WINDOW-BYTES OK -> 3 s windows are true 3 s (96000 B)")


def _advance_from_head_only_commits_next():
    # THE reported stuck case (111:1 -> 111:2): the 3 s window holds the
    # previous ayah's tail plus ONLY THE HEAD of the next ayah — never the
    # whole ayah. The prefix pass must commit 111:2 from its head alone.
    # (Built from stripped streams with exact token counts, like the matcher.)
    s1 = ml_engine.strip_diacritics(ml_engine.TAJWEED_ONLY_INDEX[111][1]["expected_full"])
    s2 = ml_engine.strip_diacritics(ml_engine.TAJWEED_ONLY_INDEX[111][2]["expected_full"])
    body = s1[-8:] + " " + s2[:16]  # ~8 residue + ~16 new tokens
    res = ml_engine.advance_target(body, 111, 1, {111})
    assert res is not None, (body, res)
    assert res[1] == 2, res
    print("HEAD-ADVANCE OK -> 111:2 commits from its head alone:", res)


def _advance_single_token_stays_quiet():
    # Only ~4 new tokens (barely-started ayah plus previous-ayah residue):
    # genuinely ambiguous, so no commit — the next retry (1.5 s later, more
    # tokens) decides instead of guessing.
    exp1 = ml_engine.TAJWEED_ONLY_INDEX[111][1]["expected_full"]
    exp2 = ml_engine.TAJWEED_ONLY_INDEX[111][2]["expected_full"]
    body = exp1[-6:] + " " + exp2[:4]
    assert ml_engine.advance_target(body, 111, 1, {111}) is None
    print("SINGLE-TOKEN QUIET OK -> barely-started ayah commits nothing")


def _advance_noisy_head_still_commits():
    # Live mic decode runs ~13% PER (mostly deletions): 1 dropped token out of
    # ~13 (~8%) must not break the commit. (2+ deletions on a short head is
    # harsher than measured reality — covered by retries, not asserted here.)
    s1 = ml_engine.strip_diacritics(ml_engine.TAJWEED_ONLY_INDEX[111][1]["expected_full"])
    s2 = ml_engine.strip_diacritics(ml_engine.TAJWEED_ONLY_INDEX[111][2]["expected_full"])
    head = s2[:14]
    noisy = head[:6] + head[7:]  # simulate 1 deleted token
    body = s1[-10:] + " " + noisy
    res = ml_engine.advance_target(body, 111, 1, {111})
    assert res is not None and res[1] == 2, (body, res)
    print("NOISY-HEAD OK -> 1 deletion tolerated, still 111:2:", res)


def _advance_shared_opening_picks_correct_ayah():
    # 113:4 and 113:5 share the two-word opening 'وَمِن شَرِّ': reading ayah 4
    # must commit 4 (not furthest-win 5) — the 12-token head must diverge.
    exp3 = ml_engine.TAJWEED_ONLY_INDEX[113][3]["expected_full"]
    exp4 = ml_engine.TAJWEED_ONLY_INDEX[113][4]["expected_full"]
    body = exp3[-8:] + " " + exp4[:16]
    res = ml_engine.advance_target(body, 113, 3, {113})
    assert res is not None and res[1] == 4, (body, res)
    print("SHARED-HEAD OK -> 113:4 wins over 113:5 on shared opening:", res)


def _advance_junk_and_tail_only_never_commit():
    # Junk matches nothing; ayah-1-only tail must not invent ayah 2.
    assert ml_engine.advance_target("zzzz zzzz", 111, 1, {111}) is None
    exp1 = ml_engine.TAJWEED_ONLY_INDEX[111][1]["expected_full"]
    assert ml_engine.advance_target(exp1, 111, 1, {111}) is None
    print("NO-FALSE-ADVANCE OK -> junk / previous-ayah-only commits nothing")


async def _advance_at_last_ayah_stays_quiet():
    # Terminal ayah: no later ayah exists, so the attempt must short-circuit
    # (no wasted GPU forward) and log once instead of retrying forever.
    s = _make_session()
    last = max(ml_engine.get_surah_ayah_ids(111))
    s.resolved = (111, last)
    s.surah_id = 111
    s.ayah_id = last
    s._init_guided()
    s.audio.extend(b"\x00\x00" * 16000)
    s._stall_ticks = main.STALL_ADVANCE_TICKS
    calls = {"n": 0}
    original_infer = ml_engine._infer_pred_ids
    ml_engine._infer_pred_ids = lambda _b: (calls.__setitem__("n", calls["n"] + 1), [])[1]
    try:
        await s.tick()
    finally:
        ml_engine._infer_pred_ids = original_infer
    assert calls["n"] == 0, "no inference must run at the last ayah"
    assert s.resolved == (111, last), s.resolved
    assert s.ws.sent, "interim must still stream"
    print("LAST-AYAH OK -> no decode, no commit, interim still streams")


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
    asyncio.run(_advance_cooldown_recovers())
    asyncio.run(_first_commit_keeps_audio_marker())
    asyncio.run(_advance_uses_fresh_audio())
    asyncio.run(_stall_triggers_advance_without_full_confirmation())
    asyncio.run(_low_bytes_still_streams_live())
    asyncio.run(_predict_error_keeps_session_alive())
    asyncio.run(_send_failure_keeps_session_alive())
    asyncio.run(_pre_detection_streams_live_each_tick())
    asyncio.run(_silence_gate_skips_decode())
    _gate_stickiness()
    asyncio.run(_frame_stride_probe())
    asyncio.run(_windowed_decode_emits_only_new_tail())
    asyncio.run(_advance_from_mid_ayah_matches_tail())
    _multi_ayah_expected_span()
    asyncio.run(_guided_skips_clipped_first_word())
    asyncio.run(_burst_phrase_feed_locks_whole_phrase())
    _window_byte_contract()
    _advance_from_head_only_commits_next()
    _advance_single_token_stays_quiet()
    _advance_noisy_head_still_commits()
    _advance_shared_opening_picks_correct_ayah()
    _advance_junk_and_tail_only_never_commit()
    asyncio.run(_advance_at_last_ayah_stays_quiet())
    print("ALL SMOKE TESTS PASSED")