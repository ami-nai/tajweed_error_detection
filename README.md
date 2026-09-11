# Tajweed Error Detection App (Quran Recitation Verification System)

A real-time Tajweed error-detection system that listens to a user reciting a
Quranic ayah (or a continuous surah) and gives instant, per-letter and per-word
feedback on how accurately the recitation matched the correct phonemes.

The system is the practical implementation for the **UG thesis / Tarteel
verification** project. A Flutter mobile app streams live microphone audio to a
FastAPI + PyTorch backend that runs a fine-tuned **wav2vec2** speech model and
a phoneme-level evaluator.

---

## 1. Overview and Goal

Correct Arabic recitation (Tajweed) differs from plain spoken Arabic mainly at
the **phoneme level**: letters, harakat (short vowels / ـَ ـِ ـُ), madd (long
vowels), heavy letters (tafkhim), and articulation details such as ghunnah
(nasalization of نـ/ـن). The goal of this project is to build a system that:

1. Records a user's recitation on a smartphone.
2. Transcribes it into a *phoneme string* in real time.
3. Compares that string against the *correct* phonemic target of the ayah
   (produced by an Arabic-phonetics library).
4. Reports whether each word and each letter/haraka was read correctly, with
   an overall accuracy score.

The current delivered system covers the memorized (short) surahs commonly used
when learning recitation: **Al-Fatihah and the 15th–30th juz' short surahs**,
with the app UI currently exposing Al-Masad (111) through An-Nas (114).

---

## 2. System Architecture

```
┌───────────────────────────┐          WebSocket            ┌──────────────────────────────────────┐
│  Flutter mobile app       │   ────────────────────────►   │  FastAPI backend (uvicorn)           │
│  ─ Mic recording (16 kHz  │     ws(s)://<host>:8000      │  ─ Silero VAD (speech segmentation)   │
│    16-bit PCM mono)       │      /ws/recite              │  ─ W2V2Light model (wav2vec2-base +  │
│  ─ audio byte streaming   │                              │    CTC phoneme head, 39 classes)      │
│  ─ JSON result rendering  │   ◄────────────────────────   │  ─ quran-transcript phonetizer       │
│    (per-word + per-letter │         JSON feedback        │  ─ Levenshtein alignment / scoring    │
│    colors, accuracy, PER) │                              │  ─ Tajweed index (18 surahs, 104 ayah)│
└───────────────────────────┘                              └──────────────────────────────────────┘
```

**Key design decision:** the heavy ML model (≈370 MB, PyTorch) does *not* run on
the phone. It runs on the backend (this machine), exposed to phones through an
ngrok tunnel when they are not on the same LAN, and the phone only records and
streams raw audio. This keeps the APK small and lets a single trained model
serve all devices.

---

## 3. Directory Layout

```
tajweed_detect_app/
├── README.md                  ← this document
├── PROCESS.md                 ← detailed audio→phoneme→feedback pipeline notes
├── start_backend.sh           ← start uvicorn + ngrok on this laptop
├── stop_backend.sh            ← stop uvicorn + ngrok
│
├── td_backend/                ← Python FastAPI + ML backend
│   ├── main.py                ← FastAPI app, /ws/recite endpoint, VAD loop
│   ├── ml_engine.py           ← model, vocabulary, index, evaluation
│   ├── quran_metadata.json    ← corpus index (416 rows / 104 ayahs / 18 surahs)
│   ├── quran_model_final-alif-hamja_correction.pt   ← trained checkpoint (39 classes)
│   ├── silero_vad.jit         ← pre-trained Silero VAD model (TorchScript)
│   ├── requirements.txt       ← full pinned dependency set (GPU-ready)
│   ├── test_client.py         ← WS smoke-test client
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

## 4. Backend (FastAPI + PyTorch)

### 4.1 Startup sequence (`main.py` + `ml_engine.py`)

On launch the backend, in order:

1. **Loads Silero VAD** (`silero_vad.jit`) and puts it in eval mode.
2. **Reads the trained vocabulary** from the checkpoint head: the number of
   output classes `V_SIZE` is read directly from
   `phoneme_head.weight.shape[0]` (currently **39** = 2 special tokens
   `<pad>`, `<blank>` + **37 phoneme tokens**). The code adapts automatically
   if a differently-sized checkpoint is later substituted.
3. **Builds the Tajweed index** from `quran_metadata.json` (see §6). For every
   ayah it pre-computes the full-ayah phoneme stream and per-word phonemes
   once, so inference never re-phonetizes text.
4. **Loads model weights** into `W2V2Light(V_SIZE)` (strict-load off) and
   puts the model in eval mode.

### 4.2 The acoustic model — `W2V2Light`

```python
class W2V2Light(nn.Module):
    def __init__(self, v_size):
        self.base = AutoModel.from_pretrained("facebook/wav2vec2-base")
        self.phoneme_head = nn.Linear(768, v_size)

    def forward(self, x):
        x = self.base.feature_extractor(x).transpose(1, 2)
        x = self.base.feature_projection(x)[0]
        x = self.base.encoder(x).last_hidden_state
        return self.phoneme_head(x)
```

- Backbone: **facebook/wav2vec2-base** (12 transformer layers, 768 hidden dim)
  from HuggingFace `transformers`.
- Head: a single linear layer to 39 classes — trained with **CTC loss** so it
  aligns unsegmented audio to a phoneme label per ~20 ms frame.
- Inference device: CUDA if available, else CPU.

### 4.3 Vocabulary (critical)

The model outputs one token per frame. The saved checkpoint does **not** store
the token ordering; `ml_engine.py` reconstructs the **exact training order**
(replayed from the training notebook's dataset build) so row *i* of the head
maps back to the same phoneme the model was trained with:

| id | token      | id | token | id | token | id | token |
|----|------------|----|-------|----|-------|----|-------|
| 0  | `<pad>`    | 10 | ِ     | 20 | ُ     | 30 | ف     |
| 1  | `<blank>`  | 11 | ۦ     | 21 | ك     | 31 | ج     |
| 2  | ت          | 12 | ل     | 22 | س     | 32 | ق     |
| 3  | َ (fatha)  | 13 | ه     | 23 | ص     | 33 | ۥ     |
| 4  | ب          | 14 | ن     | 24 | ر     | 34 | ں     |
| 5  | ␣ (space)  | 15 | و     | 25 | ذ     | 35 | ش     |
| 6  | ي          | 16 | ڇ     | 26 | أ     | 36 | خ     |
| 7  | د          | 17 | م     | 27 | ح     | 37 | ث     |
| 8  | ا          | 18 | غ     | 28 | ة     | 38 | إ     |
| 9  | ء          | 19 | ع     | 29 | ط     |    |       |

Phoneme set actually used (37): `ت َ ب ␣ ي د ا ء ِ ۦ ل ه ن و ڇ م غ ع ُ ك س ص
ر ذ أ ح ة ط ف ج ق ۥ ں ش خ ث إ`. (`V_SIZE - 2 = 37` tokens are taken, in order,
from this list, so every head row 2–38 maps to a real phoneme.)

> Note: some tokens such as `ڇ` (the ghunnah-heavy ن) and `ۦ`/`ۥ` (harakat
> presented as separate phonemes by the phonetizer) are genuine output classes
> of the model.

`MoshafAttributes` used by the phonetizer are aligned to training:
`rewaya="hafs"`, `ghunnah=4`, `madd_monfasel_len=4`, `madd_mottasel_len=4`,
`madd_mottasel_waqf=5`, `madd_aared_len=4`. The phonetizer also applies the
**alif-hamja correction** (`normalize_initial_hamzat_wasl`): leading `ٱل`/`ال`
→ `ءَ + text[1:]` and leading `ٱ`/`ا` → `ءِ + text[1:]`, matching the
`-alif-hamja_correction` training checkpoint.

### 4.4 WebSocket protocol — `/ws/recite`

The whole interaction is a single WebSocket connection.

**Client → server:**

1. One JSON text message with the target. Two modes:
   - *Single-ayah:* `{"surah_id": 112, "ayah_id": 3}`
   - *Full-surah (sequential):* `{"surah_id": 112}` (no `ayah_id` key)
2. Then a continuous stream of raw **16-bit PCM, 16 kHz, mono** bytes.

**Server → client (single-ayah mode):** after each detected pause, one JSON:

```json
{
  "mode": "single",
  "ayah_id": 3,
  "words": [ {"text": "لَمْ", "is_read": true, "letters": [{"ch":"ل","status":"ok"}, ...]}, ... ],
  "real_text": "لَمْ يَلِدْ وَلَمْ يُولَدْ",
  "expected":  "<full-ayah phoneme string>",
  "predicted": "<decoded phoneme string from the model>",
  "accuracy": 82.86,
  "per": 17.14,
  "diff": [ {"e": "ل", "h": "ل", "status": "M"}, ... ],
  "mistakes": ["In «يَلِدْ», you said «ل» instead of «ن»."]
}
```

**Server → client (surah mode):**

1. Immediately after a pause is detected (before slow inference), a fast
   **advance** message so the UI can prompt the next ayah without waiting:
   `{"mode":"surah", "advance": true, "read_now": 2, "finished": false}`
2. Then the evaluated-result message:
   `{"mode":"surah", "current_ayah": 1, "next_ayah": 2, "ayah_order": [1..N],
   "words": [ [word list of ayah 1], ... ], "ayah_accuracies": {...},
   "surah_average": 85.5, "ayah_pers": {...}, "surah_per": 14.5,
   "diff": [...], "expected": "...", "predicted": "...", "mistakes": [...],
   "finished": false}`
3. After the last ayah: `{"mode":"surah", "finished": true}`.

### 4.5 Voice activity detection (VAD)

- Silero VAD is run on the **trailing 1024 samples (64 ms)** of the
  accumulating audio buffer.
- A frame is "speech" if `speech_prob > SILENCE_PROB_THRESHOLD (0.5)`.
- A **pause** is declared when 15 consecutive frames are silent while the user
  was speaking (`PAUSE_FRAME_THRESHOLD = 15`; per source comment ≈0.45–0.9 s),
  intended to separate ayah boundaries during continuous surah recitation.
- On every pause the backend writes the accumulated raw PCM bytes to a temp
  file and evaluates the whole segment, then clears the buffer.

### 4.6 Inference pipeline (`_predict_phonemes`)

1. Raw PCM bytes → `np.float32 / 32768.0` waveform.
2. Feature extraction with `AutoFeatureExtractor("facebook/wav2vec2-base")`
   (plus a 4800-sample trailing zero-pad) → `input_values`.
3. Model forward → logits per frame, `argmax` → predicted token IDs.
4. **CTC decode** (matches the notebook): collapse consecutive duplicate
   tokens first (this preserves long madd as a repeated-then-collapsed
   pattern as trained), skipping `<pad>`, `<blank>` and any `<dummy_pad_*>`.
5. Result is one **flat phoneme string**, e.g. `تَبَت يَدَا ءَبِۦ لَهَبِن وَتَبڇ`
   — the model output is deliberately *not* word-segmented.

### 4.7 Word matching & letter scoring (`evaluate_audio`)

Each stored target word is matched **inside** the flat prediction:

1. The word's phonemes are filtered to in-vocabulary tokens, and diacritics are
   stripped for the consonant comparison (`ا ى` etc. are normalized).
2. **Exact substring** of the consonant-only target in the consonant-only
   prediction → word read.
3. Else **sliding-window Levenshtein**: scan every window of equal length in
   the prediction; the word counts as read if edit distance
   `<= max(1, 0.35 × target_len)`.
4. If the prediction is shorter than the target, a whole-string distance with
   a 0.4 threshold is used.
5. For read words, a Levenshtein **alignment with traceback**
   (`_align_with_ops`) marks each consonant as `ok`/`miss`; unread words mark
   all consonants `miss`. Diacritics and out-of-vocabulary letters are rendered
   `neutral` (the model simply cannot emit them — see §8).

### 4.8 Metrics returned

All computed in Python on the **full phoneme streams** (harakat included):

- `per` (**Phoneme Error Rate**) = `edit_distance(predicted, expected) / len(expected) × 100`.
- `accuracy` = `max(0, 100 − PER)` (percentage of phonemes matched).
- `diff`: per-character alignment list with status per position —
  `M` match, `S` substitution, `D` deletion, `I` insertion.
- `mistakes`: plain-text, human-readable sentences (see `_build_mistakes`),
  e.g. `In «يَلِدْ», you said «ل» instead of «ن».`,
  `You didn't recite the word «أَحَدٌ» correctly.`

The frontend additionally computes its own normalized word-level matching
(ألف/آ/أ→ا, ى/ي→ي, ة/ه→ه) to decide word highlight colors robustly.

---

## 5. Frontend (Flutter)

### 5.1 Tech stack

- **Flutter** (Material 3, `useMaterial3: true`, seed color teal).
- **Riverpod** (`Notifier`/`NotifierProvider`) for all state.
- `record` → live microphone streaming (16 kHz, 16-bit PCM, mono).
- `web_socket_channel` → WebSocket transport to the backend.

### 5.2 State & data flow

- `recitation_provider.dart` holds the single `RecitationResult` state machine
  (`idle → recording → success / retry / error`).
- User taps **Start** → `RecitationRemoteDataSource.startStreamingRecording()`
  opens the WS, sends the metadata JSON (`{"surah_id":…, "ayah_id":…}` or
  `{"surah_id":…}` for surah mode), then pipes raw PCM mic bytes into the
  socket.
- Incoming JSON is parsed into `RecitationResult` and rendered.
- `quranTextDatabase` (in `recitation_provider.dart`) is the local static copy
  of the ayah texts for the 4 surahs shown in the UI (111–114), used to
  initialize word chips before the first server result arrives.

### 5.3 Backend URL configuration

The backend URL is a compile-time define:

```dart
const String kBackendUrl = String.fromEnvironment(
  'BACKEND_URL',
  defaultValue: 'ws://192.168.1.113:8000',   // local dev default
);
```

Override at build/run time with
`--dart-define=BACKEND_URL=wss://<host>` (see §7.3).

### 5.4 UI features (`recitation_page.dart`)

- **Mode toggle:** `Single Ayah` / `Full Surah` (ChoiceChips, disabled while
  recording).
- **Selector card:** Surah dropdown (Al-Masad 111, Al-Ikhlas 112, Al-Falaq 113,
  An-Nas 114) and, in single-ayah mode, an Ayah dropdown (boundaries per surah).
- **Start / Stop** buttons.
- **Quran Text card** (RTL, Amiri font):
  - *Single ayah:* each ayah word rendered as a chip; read words turn teal
    with a tinted background.
  - *Letter-level colors:* `ok` → teal, `miss` → red, `neutral` → black.
  - *Full surah:* each ayah in its own bordered block, current ayah highlighted
    with a teal border and a **"▶ Read now"** badge; per-ayah summary chips
    show `Acc X%` and `PER X%`.
- **Results card:**
  - Accuracy (color-coded: ≥70 teal, ≥40 orange, else red) and PER.
  - **Real transcription** and **Predicted transcription** rendered from the
    alignment `diff` (matched chars teal, mismatches red).
  - Expandable **"Show mistakes"** list of plain-text error sentences.
  - In surah mode: Surah Average Accuracy, Surah PER, current/next ayah, and
    per-ayah expandable transcriptions + mistakes.
- **Status banner:** shows a friendly error if the backend is unreachable
  (with the configured `kBackendUrl`), or "Reciting…" while streaming.

### 5.5 Android permissions

```xml
<uses-permission android:name="android.permission.INTERNET" />
<uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />
<uses-permission android:name="android.permission.RECORD_AUDIO" />
```

---

## 6. Corpus / Tajweed Index (`quran_metadata.json`)

- **416 rows** of ayah metadata.
- **104 unique ayahs** (the indexer keeps the last row per `(surah_id, ayah_id)`)
  across **18 surahs**:

  ```
  1 (Al-Fatihah), 94, 95, 97, 99, 102, 103, 104, 105, 106, 107, 108,
  109, 110, 111, 112, 113, 114
  ```

- Each row includes: `surah_id`, `ayah_id`, `surah_name_ar/en/tr`, `ayah_count`,
  `ayah_ar/en/tr`, `reciter_id`, `reciter_name`, `audio` (URL), and
  `local_audio_path`.
- **4 reciters** contribute the recordings:
  `Husary_Mujawwad_64kbps`, `warsh_yassin_64kbps`, `Ghamadi_40kbps`,
  `Minshawy_Teacher_128kbps`.

The backend index (`TAJWEED_ONLY_INDEX`) therefore supports all 18 surahs even
though the current app dropdown intentionally exposes the four gradated
memorization surahs (111–114).

---

## 7. How to Run

### 7.1 Backend (local, with GPU or CPU)

Prerequisites in `td_backend/`: `quran_metadata.json`,
`quran_model_final-alif-hamja_correction.pt`, `silero_vad.jit`, plus the
packages in `requirements.txt` (a ready local env `fastapienv/` is included).

```bash
cd td_backend
./fastapienv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
```

- `--host 0.0.0.0` lets phones on the same LAN connect.
- First run downloads `facebook/wav2vec2-base` into the HuggingFace cache.
- Interactive docs: `http://localhost:8000/docs`.

### 7.2 Backend + ngrok tunnel on this laptop (one command)

```bash
./start_backend.sh     # starts uvicorn + ngrok, prints the public wss:// URL
./stop_backend.sh      # stops both
```

### 7.3 Frontend (Flutter)

```bash
cd td_frontend
flutter pub get

# Local device (phone + PC on same network)
flutter run --dart-define=BACKEND_URL=ws://<PC_LAN_IP>:8000

# Release APK pointed at the public backend (ngrok tunnel)
flutter build apk --release --dart-define=BACKEND_URL=wss://<host>.ngrok-free.app
# output: build/app/outputs/flutter-apk/app-release.apk
```

### 7.4 Backend smoke test

```bash
cd td_backend && ./fastapienv/bin/python test_client.py
```

(`test_client.py` connects to `ws://127.0.0.1:8000/ws/recite`, sends metadata
for Surah 112 / Ayah 3 plus a WAV file, and prints the JSON result.)

---

## 8. Evaluation Metrics (Thesis notes)

| Metric              | Definition                                                      | Where computed |
|---------------------|-----------------------------------------------------------------|----------------|
| **Accuracy**        | `max(0, 100 − PER)` — % of phonemes matched                      | backend        |
| **PER**             | `edit_distance(pred, expected) / len(expected) × 100`            | backend        |
| **Word hit**        | Target word phonemes found in predicted stream (substring or ≤35% edit-distance window) | backend |
| **Letter status**   | per-consonant `ok`/`miss` from Levenshtein alignment; `neutral` for model-unverifiable chars | backend |
| **diff**            | per-position `M`/`S`/`D`/`I` alignment (drives colored transcription) | backend |

The model can only emit the 37 tokens of its vocabulary, so the feedback layer
deliberately reports mistakes **only for characters the model was trained on**
(`_is_feedback_char`); marks such as sukun (ْ), shadda (ّ), tanween (ًٌٍ),
maddah (ٓ), superscript alif (ٰ) and out-of-vocabulary consonants (ظ، ض، ز)
are never asserted as mistakes — this prevents fabricating errors the model
cannot verify.

---

## 9. Known Limitations & Current Status

1. **Chunked, not word-streamed.** The model evaluates the whole utterance
   between VAD pauses — feedback appears after you pause, not letter-by-letter
   while speaking.
2. **Recognition is data-dependent.** It is most accurate on reciters/surahs
   similar to the training data; unseen reciters may misrecognize.
3. **Training scale.** The current checkpoint was trained on a small set of
   short surahs (~81 usable samples after filtering; ~220 collected rows).
   Larger-tablet retraining is the recommended accuracy improvement.
4. **Mistake-list coverage.** `_build_mistakes` returns an empty list when the
   phonetizer merges words across boundaries (full-stream spacing does not
   equal word count), e.g. Al-Ikhlas ayah 4 — those ayahs then show no
   "Show mistakes" button (the diff/transcriptions still appear).
5. **UI surahs.** Backend index covers 18 surahs; the app dropdown currently
   exposes 4 (111–114).
6. **Vocabulary fixed during inference.** The deployed checkpoint has 39
   classes (2 special + 37 phonemes, no letter ى). Should a future retrained
   checkpoint change `V_SIZE`, `ml_engine.py` adapts automatically and the
   vocab is taken from the ordered `TRAINED_PHONEMES` list.

---

## 10. Related Documents

- **PROCESS.md** — deeper notes on the audio→phoneme→matching pipeline.
- Training notebook (referenced by `ml_engine.py`): the dataset build and CTC
  training/decode were performed in a separate **problamalif.ipynb** notebook;
  `TRAINED_PHONEMES` order, `MoshafAttributes`, and the CTC decode in the code
  are kept in lock-step with that notebook.