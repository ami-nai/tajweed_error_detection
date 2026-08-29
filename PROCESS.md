# Recitation Processing Flow

How the app turns recorded audio into per-word "read/not-read" results.

## 1. Startup (once)
The backend loads `quran_metadata.json` and builds an in-memory index:

```
TAJWEED_ONLY_INDEX[surah_id][ayah_id] = {
  "words": [ {"text": "تَبَّتْ", "phonemes": ["ت","َ","ب","ب","َ","ت"]}, ... ]
}
```

Each word's `phonemes` are pre-computed once with the `quran-transcript`
phonetizer, so inference never re-phonetizes text.

## 2. Audio → phoneme string
- The Flutter app streams mic audio (16 kHz PCM) over WebSocket.
- The backend detects pauses with Silero VAD; when a pause ends a chunk, the
  chunk's audio goes to the model.
- `W2V2Light` (wav2vec2-base + phoneme head) outputs one token per ~20 ms
  frame. Argmax → token ID → mapped to a phoneme symbol via the vocabulary,
  dropping pad/blank tokens and collapsing consecutive repeats (CTC dedup).
- Result: one **flat phoneme string**, e.g. `تَبَت يَدَا ءَبِۦ لَهَبِن وَتَبڇ`.

> The model output is **never split into words** — it stays one flat string.

## 3. Matching (the important part)
The flat prediction is **not** segmented. Instead, each stored word's target
phonemes are **searched inside** the flat string:

```
for each stored word target_str:
    if target_str is a substring of pred_str        → word read
    else: sliding-window Levenshtein (≤35% error)   → word read if close
```

Any hit marks that word as read. Words are matched independently and
order-blindly — sequence/word boundaries are not checked.

## 4. Results
Backend sends back per WebSocket:

```json
{
  "words":     [{"text": "قُلْ", "is_read": true}, ...],
  "real_text": "قُلْ هُوَ اللَّهُ أَحَدٌ",
  "expected":  "قُل هُۥۥ اللَّه ڇ...",
  "predicted": "قُل هُو لَاهُ ءَحَدڇ...",
  "accuracy":  82.86
}
```

- `accuracy` is **computed in Python** (Levenshtein edit distance between
  `predicted` and `expected`), not produced by the model.
- `expected` comes from **per-word** phonetization, while the model was
  trained on **full-ayah** phonetization — a small word-boundary mismatch
  absorbed by the matching tolerance.

## Current limitation
Each pause triggers an evaluation of only that fragment. If the reciter
pauses mid-ayah, partial fragments are scored against the whole ayah, giving
misleading accuracy/read results. The planned fix: enlarge the VAD pause
threshold so the whole ayah accumulates as one chunk before evaluation.