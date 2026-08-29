# Tajweed Error Detection App

Real-time Tajweed/recitation verification for Quranic verses (surah 111–114 currently).

## Architecture

```
┌────────────────────────┐         WebSocket        ┌──────────────────────────────┐
│  Flutter mobile app    │  ──────────────────────► │  FastAPI backend (uvicorn)   │
│  - records mic (16kHz  │      ws://IP:8000       │  - Silero VAD (speech detect)│
│    PCM mono)            │                         │  - Wav2Vec2 model + CTC head │
│  - streams audio bytes  │                         │  - quran-transcript phonet.  │
│  - renders words/score  │  ◄─────────────────────  │                              │
└────────────────────────┘       JSON results       └──────────────────────────────┘
```

## How it works (data flow)

1. The Flutter app records the microphone at **16 kHz, 16-bit PCM, mono** and streams the raw bytes to the backend over a WebSocket (`/ws/recite`).
2. The backend first receives a JSON metadata message: `{"surah_id": 112, "ayah_id": 1}`.
3. **Silero VAD** analyzes the stream. When it detects speech followed by a short silence (**~0.25 s**, `silence_frames > 8`), it cuts the accumulated audio into a chunk.
4. The chunk is passed to `evaluate_audio()`:
   - Audio → Wav2Vec2 features → `phoneme_head` → argmax → predicted phoneme IDs per frame.
   - IDs are mapped to QPS phoneme symbols via `id2phoneme` (see vocab note below).
   - `<pad>`/`<blank>` removed + CTC dedup (consecutive repeats collapsed).
   - Each word of the target ayah is phonetized with `quran-transcript` and matched against the prediction (exact substring, then Levenshtein sliding window).
5. The backend sends back a JSON payload:

```json
{
  "words":     [{"text": "قُلْ", "is_read": true}, ...],
  "real_text": "قُلْ هُوَ اللَّهُ أَحَدٌ",
  "expected":  "قُل هُۥۥ اللَّه ڇ...",
  "predicted": "قُل هُو لَاهُ ءَحَدڇ...",
  "accuracy":  82.86
}
```

6. Flutter highlights read words in teal and updates the **Results card** (accuracy %, real transcription, predicted transcription).

## The vocabulary / index mapping (critical)

The model was trained with a **34-class** vocabulary (2 base tokens `<pad>`, `<blank>` + 32 phonemes) using a specific token order. The saved checkpoint (`quran_model_final.pt`) does **not** store the token order — it only stores weight rows.

`ml_engine.py` uses the **exact training order**, reconstructed by replaying the training notebook's dataset build (`quran_transcript.Aya(...).uthmani_script` + the notebook's `MoshafAttributes` + the audio-presence filter from `quran_full_data_collection_new_send.zip`):

```
TRAINED_PHONEMES = ['ت', 'َ', 'ب', ' ', 'ي', 'د', 'ا', 'ء', 'ِ', 'ۦ', 'ل', 'ه',
                    'ن', 'و', 'ڇ', 'م', 'غ', 'ع', 'ُ', 'ك', 'س', 'ص', 'ر', 'ذ',
                    'ف', 'ج', 'ح', 'ۥ', 'ں', 'ش', 'خ', 'ق']
```

`V_SIZE` is read from the checkpoint head (`phoneme_head.weight.shape[0]`), and the vocab is built to match it. If the checkpoint is replaced with a 40-class model (after a full retrain), the same code adapts automatically.

`MoshafAttributes` in `ml_engine.py` are also aligned to training: `madd_mottasel_len=4`, `madd_aared_len=4`.

## How to run

### Backend

```bash
cd td_backend
./fastapienv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
```

- `--host 0.0.0.0` is required so phones on the LAN can connect.
- On startup it loads the Tajweed index (18 surahs), the Wav2Vec2 model (~370 MB), and Silero VAD.
- Required local files (in `td_backend/`): `quran_model_final.pt`, `quran_metadata.json`, `silero_vad.jit`.

### Frontend (Flutter)

1. Set the backend address in `td_frontend/lib/features/recitation/presentation/providers/recitation_provider.dart`:
   ```dart
   return RecitationRemoteDataSource('ws://<YOUR_PC_LAN_IP>:8000');
   ```
2. Run on a device/emulator:
   ```bash
   cd td_frontend
   flutter run
   ```
   Phone and PC must be on the same network.

## Current dataset & model status

- **Training dataset**: `quran_full_data_collection_new_send.zip` — 220 rows (surahs 111–114, multiple reciters). After filtering (missing audio + phonetizer failures) **81 samples** were used.
- **Model**: `W2V2Light` (facebook/wav2vec2-base + linear phoneme head), CTC loss, ~100 epochs, batch 1.
- **Accuracy**: ~90% phonetic accuracy on samples the model saw in training (e.g., Surah 111). Generalization to unseen reciters (e.g., some Surah 112 clips) is still weak because of the small training set.

## Known limitations / next steps

- Model returns the **whole utterance at once** — results appear after you finish speaking a phrase, not word-by-word in real time.
- Recognition is strongest on surahs/reciters present in the 81 training samples; other reciters often misrecognize.
- Retraining with the full dataset (and the 40-token vocabulary) is the recommended path to improve accuracy and remove the 34-class trimming.