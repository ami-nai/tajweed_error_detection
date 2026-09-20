# Tajweed Error Detection App (Quran Recitation Verification System)

A real-time Tajweed error-detection and recitation follow-along system. A
Flutter mobile app streams live microphone audio to a FastAPI + PyTorch backend
running a fine-tuned **wav2vec2** phoneme model. The backend transcribes speech
into phoneme strings, matches them against the correct phonemic targets of the
Quranic text, and streams back per-word / per-letter highlighting, accuracy
scores, and mistake feedback — live, while the user recites.

Three recitation modes are supported:

| Mode | What the user does | What the server does |
|---|---|---|
| **Single ayah** | Picks surah + ayah, recites it | Tracks that ayah word-by-word, scores it on Stop |
| **Full surah** | Picks a surah, recites ayah by ayah | Tracks the current ayah, advances on Next/Stop per ayah |
| **Open mic** | Picks a surah (candidate filter), recites anything | **Auto-detects which ayah is being recited**, follows across ayahs automatically |

The system is the practical implementation for the **UG thesis / Tarteel
verification** project. Current branch: `highlight_reltime`.

---

## 1. System Architecture (VAD-free live loop)

Silero VAD was removed. There is no speech segmentation anymore — audio flows
continuously and a heartbeat loop decodes it in small beats:

```
┌───────────────────────────┐          WebSocket            ┌──────────────────────────────────────┐
│  Flutter mobile app       │   ────────────────────────►   │  FastAPI backend (uvicorn)           │
│  ─ Mic recording (16 kHz  │     ws(s)://<host>:8000      │  ─ heartbeat tick every 0.5 s         │
│    16-bit PCM mono)       │      /ws/recite              │  ─ energy gate (RMS silence skip)     │
│  ─ audio byte streaming   │                              │  ─ W2V2Light (wav2vec2-base + CTC    │
│  ─ JSON result rendering  │   ◄────────────────────────   │    phoneme head, 39 classes)         │
│    (word spotlight, per-  │      JSON interim + final    │  ─ phoneme trie (detection + guided) │
│    letter colors, live    │                              │  ─ quran-transcript phonetizer       │
│    phoneme strip, acc/PER)│                              │  ─ Levenshtein alignment / scoring    │
└───────────────────────────┘                              └──────────────────────────────────────┘
```

**Key design decisions:**

- The heavy ML model (≈370 MB, PyTorch) does *not* run on the phone. It runs on
  the backend (this machine), exposed to phones through an ngrok tunnel when
  they are not on the same LAN. The phone only records and streams raw audio.
- Interim results stream **every heartbeat tick** (`{final:false}` messages with
  words + spotlight + live phoneme string). The authoritative result is computed
  once, on Stop (`{final:true}`).
- The user's **Stop button is the only finalize path**. Connection errors tear
  down the channel without finalizing, so a network blip can never produce a
  mid-recite score.

---

## 2. Directory Layout

```
tajweed_detect_app/
├── README.md                  ← this document
├── PROCESS.md                 ← deeper audio→phoneme→matching pipeline notes
├── DEPLOYMENT.md              ← deployment notes
├── conversation_context.md    ← spotlight feature context (from Claude)
├── spotlight_changes.md       ← spotlight exact-diff spec (from Claude)
├── start_backend.sh           ← start uvicorn + ngrok on this laptop
├── stop_backend.sh            ← stop uvicorn + ngrok
│
├── td_backend/                ← Python FastAPI + ML backend
│   ├── main.py                ← FastAPI app, Session, heartbeat, WS endpoint
│   ├── ml_engine.py           ← model, vocabulary, index, tries, evaluation
│   ├── phonetic_trie.py       ← PhoneticTrie: fuzzy Levenshtein-automaton search
│   ├── quran_metadata.json    ← corpus index (416 rows / 104 ayahs / 18 surahs)
│   ├── quran_model_final-alif-hamja_correction.pt   ← trained checkpoint (39 classes)
│   ├── quran_model_final.pt   ← earlier checkpoint (kept for reference)
│   ├── silero_vad.jit         ← REMOVED from pipeline; file kept on disk only
│   ├── requirements.txt       ← full pinned dependency set (GPU-ready)
│   ├── requirements-colab.txt ← colab deploy deps
│   ├── deploy_colab.py        ← stale colab deploy script (predates ngrok switch)
│   ├── test_ws_smoke.py       ← WS smoke + unit tests (the contract — keep green)
│   ├── test_phonetic_trie.py  ← trie unit tests
│   ├── test_client.py         ← legacy client (predates live protocol)
│   ├── 111-1,2.m4a            ← real 15 s recitation clip (decode experiments)
│   └── fastapienv/            ← local Python virtual environment
│
└── td_frontend/               ← Flutter cross-platform app
    ├── pubspec.yaml
    └── lib/
        ├── main.dart          ← app entry (ProviderScope, MaterialApp)
        └── features/recitation/
            ├── domain/
            │   ├── entities/recitation_result.dart         ← state model
            │   └── repositories/recitation_repository.dart ← abstraction
            ├── data/
            │   ├── datasources/recitation_remote_datasource.dart  ← mic + WS
            │   └── repositories/recitation_repository_impl.dart
            └── presentation/
                ├── providers/recitation_provider.dart      ← Riverpod notifier
                └── pages/recitation_page.dart              ← full UI
```

---

## 3. Backend (FastAPI + PyTorch)

### 3.1 Startup sequence (`ml_engine.py`)

On import, in order:

1. **Reads the trained vocabulary size from the checkpoint head**:
   `V_SIZE = phoneme_head.weight.shape[0]` (currently **39** = 2 special tokens
   `<pad>`, `<blank>` + **37 phoneme tokens**). Adapts automatically to a
   differently-sized checkpoint.
2. **Builds the Tajweed index** (`TAJWEED_ONLY_INDEX[surah][ayah] =
   {words, expected_full}`) from `quran_metadata.json` using
   `quran_transcript.quran_phonetizer` with Hafs `MoshafAttributes`
   (ghunnah/madd lengths aligned to training) plus the **alif-hamja
   correction** (`normalize_initial_hamzat_wasl`). Full-ayah phonetization is
   preferred (cross-word tajweed rules preserved); per-word is the fallback.
   Done once — inference never re-phonetizes text.
3. **Loads weights** into `W2V2Light(V_SIZE)` and eval mode; loads the
   `facebook/wav2vec2-base` feature extractor (first run downloads it into the
   HuggingFace cache).

### 3.2 The acoustic model — `W2V2Light`

Backbone `facebook/wav2vec2-base` (12 transformer layers, 768 hidden dim) + a
single linear head to 39 classes, trained with **CTC loss** (≈one label per
20 ms frame). CUDA if available, else CPU. A threading `_INFERENCE_LOCK`
serializes inference because heartbeat decodes and the final eval can overlap.
CTC frame stride is **320 samples/frame** at 16 kHz
(`conv_stride [5,2,2,2,2,2,2]` in the wav2vec2-base config).

### 3.3 Vocabulary (critical)

The checkpoint does **not** store token ordering; `ml_engine.py` replays the
exact training order so head row *i* maps to the phoneme it was trained with:

| id | token | id | token | id | token | id | token |
|----|------------|----|-------|----|-------|----|-------|
| 0  | `<pad>`    | 10 | ِ     | 20 | ُ     | 31 | ج     |
| 1  | `<blank>`  | 11 | ۦ     | 21 | ك     | 32 | ق     |
| 2  | ت          | 12 | ل     | 22 | س     | 33 | ۥ     |
| 3  | َ (fatha)  | 13 | ه     | 23 | ص     | 34 | ں     |
| 4  | ب          | 14 | ن     | 24 | ر     | 35 | ش     |
| 5  | ␣ (space)  | 15 | و     | 25 | ذ     | 36 | خ     |
| 6  | ي          | 16 | ڇ     | 26 | أ     | 37 | ث     |
| 7  | د          | 17 | م     | 27 | ح     | 38 | إ     |
| 8  | ا          | 18 | غ     | 28 | ة     |    |       |
| 9  | ء          | 19 | ع     | 29 | ط     |    |       |
|    |            |    |       | 30 | ف     |    |       |

37 phonemes: `ت َ ب ␣ ي د ا ء ِ ۦ ل ه ن و ڇ م غ ع ُ ك س ص ر ذ أ ح ة ط ف ج ق ۥ ں ش خ ث إ`.

### 3.4 Session + heartbeat (`main.py`)

Each `/ws/recite` connection gets a `Session`:

- `audio`: full-session `bytearray` of everything the mic sent.
- `eval_pos`: how much audio has been decoded so far (delta = `audio[eval_pos:]`).
- `stream_pred_raw`: cumulative decoded phoneme string (drives the live strip).
- `feed_tail`: cumulative decoded string since the last ayah commit (detection input).
- `openmic_ayah_start`: byte offset where the current open-mic ayah's audio begins.

A heartbeat task calls `tick()` every `EVAL_INTERVAL_SEC = 0.5 s`:

1. If fewer than `MIN_NEW_BYTES = 8000` (≈0.25 s) of new audio arrived, skip
   decoding — but **still send the live interim** so the strip never freezes.
2. Otherwise decode **only the delta** via `predict_phonemes_bytes`, append to
   both accumulators, and run the mode logic (guided confirm / resolve /
   stall-advance), then send an interim.
3. Any per-beat exception is caught, logged (`⚠️ [TICK EXCEPTION]`), and never
   kills the session. A failed `send_json` is logged and retried next beat —
   only a receive-side disconnect closes the connection.

### 3.5 Energy gate (silence filter)

Before decoding a delta, its RMS amplitude is checked against
`ENERGY_RMS_THRESHOLD = 150` (16-bit PCM; speech is typically 500+, idle-mic
silence far below 100). Below-threshold beats skip the model entirely — no
hallucinated junk phonemes into the strip or `feed_tail` — while the interim,
stall bookkeeping, and detection flow continue with an empty token batch.
Transitions are logged (`🔇 silence beat` / `🔊 decoding resumed`), not every
beat. This replaced Silero VAD: it only *gates*, which covers the easy most of
the silence problem at zero model cost (see §9 on why VAD is not the accuracy
fix).

### 3.6 Inference pipeline

`_bytes_to_prediction(bytes)` = `_infer_pred_ids` → `_collapse_pred_ids`:

1. int16 PCM → float32 waveform → 4800-sample trailing zero-pad →
   `AutoFeatureExtractor("facebook/wav2vec2-base")` → model forward → argmax
   per frame → raw id list (`_infer_pred_ids`; no printing, reusable for
   windowed decoding).
2. **CTC decode** (`_collapse_pred_ids`, matches training notebook): collapse
   adjacent duplicates first (preserves madd/long vowels), skipping `<pad>`,
   `<blank>`, `<dummy_pad_*>`. Result is one flat phoneme string — deliberately
   *not* word-segmented.

### 3.7 The two tries

`phonetic_trie.py` — `TrieNode{children, end, surah_id, ayah_id, word_idx,
ayahs}` where `ayahs` is the set of every ayah passing through the node (shared
prefixes attribute to all users). `search_fuzzy` is a bounded Levenshtein
automaton over the trie: DP edit-distance rows carried down the tree, branches
pruned past `budget = max(2, 35% of query)`, depths restricted to
`[0.7×, 1.4×]` query length, normalized `score = cost / max(len, depth)`,
rejected if `score > 0.35` or the top two candidates are within margin
(ambiguous = keep listening).

- **Global ayah trie** (detection): every ayah's full `expected_full` stream
  inserted once. `resolve_ayah(pred_str, surahs)` returns
  `(surah, ayah, cost, score, depth)` or `None`.
- **GuidedLiveTrie** (per-ayah highlighting): built from the tracked ayah's word
  targets (in-vocab phonemes, diacritics stripped). `feed()` appends decoded
  tokens to a tail; `confirm()` monotonically locks the next unread word via
  exact-substring-then-sliding-Levenshtein span search and emits
  `{index, text, is_read, letters}` with per-consonant `ok`/`miss` from a
  traceback alignment. `active_index` exposes the current pointer read-only
  (the word **spotlight** — no state mutation).

### 3.8 Open-mic detection + advance

- **First detection**: once `feed_tail` holds `≥ MIN_RESOLVE_TOKENS (5)` clean
  tokens, `resolve_ayah` runs each tick until an ayah wins → `_commit_openmic`:
  seeds `session_words`, builds the guided trie, replays `feed_tail` through it
  (catches already-spoken words), clears the tail. Before detection, a
  live-only interim (`{live}`) streams every tick.
- **Advance**: after `STALL_ADVANCE_TICKS = 3` beats with no new confirmation,
  `_maybe_advance_openmic` re-resolves on a **recent window**
  (`max(40, 2× current ayah length)` tokens — the full tail would just re-match
  the old ayah), trims the stale prefix regardless, and commits only on a
  strictly-later ayah. `openmic_ayah_start` marks the new ayah's audio start.
- Forward-guard (same/backward never advances) and a resolve cooldown prevent
  thrash. All covered by unit tests in `test_ws_smoke.py`.

### 3.9 WebSocket protocol — `/ws/recite`

Client → server: one JSON metadata (`{"mode","surah_id"[, "ayah_id"]}`; open-mic
`surah_id` only narrows candidates), then raw PCM bytes, then `{"type":"stop"}`
or `{"type":"next"}` (surah).

Server → client interims (`final:false`, every tick when active):

- single: `{mode, ayah_id, words, active_index}`
- surah: `{mode, current_ayah, next_ayah, ayah_order, words, active_index}`
- open_mic: `{mode, detected{s surah_id, ayah_id}, ayah_id, words, live, active_index}`
  (pre-detection: `{mode:"open_mic", final:false, live}` only)

Server → client finals (`final:true`, on Stop/Next): full `evaluate_audio`
scoring — `words` with letters, `real_text/expected/predicted`, `accuracy`,
`per`, `diff` (M/S/D/I alignment), `mistakes`. Open-mic finalizes only the
segment since `openmic_ayah_start`. Finals carry no `active_index`.

### 3.10 Word matching, letter scoring, metrics

`evaluate_audio` matches each target word inside the flat prediction (exact
consonant substring, else sliding-window Levenshtein ≤ 35%, else whole-string
≤ 40%), then traceback-aligns read words to `ok`/`miss` per consonant.
Diacritics and out-of-vocab letters render `neutral`; mistake sentences are
emitted **only for model-verifiable characters** (`_is_feedback_char`) so the
system never fabricates errors on marks it cannot output (sukun, shadda,
tanween, ظ ض ز, …). `PER = edit_dist/len(expected)×100`,
`accuracy = max(0, 100−PER)`.

---

## 4. Frontend (Flutter)

### 4.1 Stack & data flow

Flutter + Material 3, Riverpod `Notifier`, `record` mic streaming (PCM16 /
16 kHz / mono), `web_socket_channel`. `kBackendUrl` is a `--dart-define`
(`BACKEND_URL`, LAN default); `quranTextDatabase` in the provider seeds static
word chips before server data arrives.

Start → datasource opens WS, sends metadata, pipes mic bytes in; provider
listens: payload handler is sandboxed (a malformed message is logged and
ignored, never tears down the stream); `onError` calls `abortStreaming()`
(mic + channel torn down **without** sending `stop`, so errors never
auto-finalize); `onDone`/15 s fallback go idle. The Stop button's `stop`
is the only finalize trigger; `forceClose` is the no-reply fallback.

### 4.2 Provider logic (`recitation_provider.dart`)

- **single interim**: OR-merge server words/letters into local words; apply
  `active_index`.
- **surah**: rebuild per-ayah word maps from `ayah_order`; interim applies the
  spotlight, finals replace + clear it (`active_index: null`).
- **open_mic**: `detected` updates selection (+reseeds the surah map on surah
  change); first detection seeds wholesale; a later ayah **replaces** state and
  clears metrics (forward advance); otherwise OR-merge; `live` → `livePhonemes`
  (absent key keeps previous); final → wholesale replace + `success`.
- Resets: mode/ayah/start-reciting set the spotlight (`0` for single/surah,
  `null` for pre-detection open-mic); teardown paths clear it. Sentinel
  `copyWith` (`_omit`) distinguishes "set null" from "unchanged".

### 4.3 UI (`recitation_page.dart`)

- Mode toggle (Single / Full Surah / Open Mic), surah+ayah selectors, Start/Stop.
- **Open-mic card**: the whole surah as one continuous RTL word flow with a teal
  `۝` ayah-end marker, plus the **Live Predicted Phonemes** strip (Amiri font).
- **Word spotlight** (all modes): the word at `activeWordIndex` gets an amber
  wash + border; read words are teal with per-letter `ok/miss/neutral` colors.
- Results card: accuracy + PER, alignment-colored real/predicted
  transcriptions, expandable mistakes; surah mode adds per-ayah blocks, badges
  (`▶ Read now`), and surah averages.

---

## 5. Corpus / Tajweed Index

`quran_metadata.json`: 416 rows → **104 ayahs across 18 surahs**
(1, 94, 95, 97, 99, 102–114), 4 reciters. The backend index supports all 18;
the app dropdown exposes the graded memorization surahs (111–114).

---

## 6. How to Run

### 6.1 Backend + ngrok (one command, from repo root)

```bash
./start_backend.sh     # starts uvicorn + ngrok, prints the public wss:// URL
./stop_backend.sh      # stops both
```

Model load takes ~3 min CPU before `:8000` serves; docs at
`http://localhost:8000/docs`. Backend log: `/tmp/opencode/uvicorn.log`.

> Memory rule: **stop uvicorn before running the smoke suite** — two model
> copies (server + test process) thrash swap and hang the run.

### 6.2 Frontend

```bash
cd td_frontend && flutter pub get
flutter run --dart-define=BACKEND_URL=ws://<PC_LAN_IP>:8000        # LAN dev
flutter build apk --release --dart-define=BACKEND_URL=wss://<host> # via tunnel
```

### 6.3 Tests (the contract — keep green)

```bash
cd td_backend && ./fastapienv/bin/python test_ws_smoke.py   # uvicorn STOPPED
./fastapienv/bin/python test_phonetic_trie.py
cd ../td_frontend && flutter analyze                        # 0 errors
```

`test_ws_smoke.py` covers the live WS protocol (single/open-mic/surah) plus
advance units (forward-guard, cooldown, first-commit marker, windowed
resolve+trim, stall-trigger) and resilience units (low-bytes-live,
predict-error survival, send-failure survival, pre-detection streaming,
silence-gate). Zero-filled PCM fixtures are pure silence by definition, so any
test that needs decode to run uses the deterministic `_noise_bytes` helper
(RMS 500) — swapping those back to zeros silently un-tests them.

---

## 7. Live-decode quality: what the experiments showed

On a real 15 s clip of 111:1–2 (`td_backend/111-1,2.m4a`), scored by
Levenshtein distance to `expected_full` ground truth (lower = better):

| Decode strategy | Dist | Note |
|---|---|---|
| One-shot full clip (old VAD-segment quality) | **24** | model ceiling on this clip |
| Stitched 0.5 s deltas = current live strip | **62** | fragmentation destroys it |
| 3 s windows, naive frame crop | 43 | — |
| 3 s windows + LEAD lookahead + collapse carry | **29** | full Phase A design |

Conclusions: (1) the strip's inaccuracy is ~3× worse than the model ceiling and
almost entirely a **framing** problem, not a model or CPU problem — CPU vs GPU
computes the same argmax, only faster; (2) naive window stitching is not
enough — the LEAD lookahead (never emit pad-adjacent frames) and the
cross-slice collapse `prev` carryover are both load-bearing; (3) VAD can only
ever *gate*, never improve a decode — it is not the fix (see §3.5).

**Roadmap — Phase A (LANDED):** production windowed decode is live on the tick
path (`decode_live_window` in `ml_engine.py`, wired into `_tick_once` in
`main.py`): trailing 3 s window (`LIVE_WINDOW_SAMPLES`), frame-index slicing
with stride computed from the loaded model (`CTC_STRIDE`, never hardcoded),
16-frame LEAD lookahead (`LIVE_LEAD_FRAMES`), and cross-slice collapse
carryover (`_win_prev`, reset on silence-gated beats so repeats after a pause
are not merged). Finals untouched; interim payload shape unchanged (no
frontend changes needed). Measured cost ≈2.3 s per window on this CPU, so the
live cadence is accurate chunks every ~2.5–3 s (self-throttled, no backlog)
rather than 0.5 s-smooth — the available knob is window size (2 s windows ≈
2 s cadence). Cloud GPU was evaluated and deferred: it buys smoothness, never
accuracy — revisit only if post-Phase-A cadence feels too slow after free CPU
optimizations (thread tuning, quantization/ONNX).

---

## 8. Known Limitations & Status

1. **Live strip framing (being fixed).** Until Phase A lands, the strip is
   stitched 0.5 s deltas — visibly worse than finals. Finals are unaffected
   (full-segment one-shot decode).
2. **Recognition is data-dependent.** Best on reciters/surahs near the training
   distribution.
3. **Training scale.** Small short-surah set; retraining is the accuracy lever
   once framing is fixed.
4. **Mistake-list coverage.** Empty when the phonetizer merges words across
   boundaries (diff/transcriptions still appear).
5. **CPU cadence.** ~1 s effective live cadence today; ~2.5–3 s accurate chunks
   after Phase A on this box (see §7).
6. **Stale files.** `deploy_colab.py` predates the ngrok switch;
   `test_client.py` predates the live protocol; `silero_vad.jit` is unused.
7. **Checkpoint swap.** A retrained checkpoint with different `V_SIZE` is picked
   up automatically; the ordered `TRAINED_PHONEMES` list must still match its
   training order.

---

## 9. Related Documents

- **PROCESS.md** — deeper audio→phoneme→matching pipeline notes.
- **DEPLOYMENT.md** — deployment notes.
- **conversation_context.md / spotlight_changes.md** — word-spotlight feature
  specs (Tarteel-style follow-along, all three modes).
- Training notebook (referenced by `ml_engine.py`): dataset build + CTC
  training/decode in **problamalif.ipynb**; `TRAINED_PHONEMES` order,
  `MoshafAttributes`, and the CTC decode stay in lock-step with it.
