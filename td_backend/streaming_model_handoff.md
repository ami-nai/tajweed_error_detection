# Streaming Quran Phoneme Model: Training and Integration Handoff

## 1. Product goal

A Tarteel-style "follow along" feature. A reciter speaks into the mic, the backend turns audio into a phoneme stream, a phonetic trie matches that stream against the expected ayah text to highlight recited words in real time, and the app reports where the reciter made mistakes.

For this to work the model must give usable phonemes from a **short sliding window of live audio**, not only from a complete ayah.

## 2. What changed

| | Old model (currently in `ml_engine.py`) | New model (this document) |
|---|---|---|
| Training audio | Full ayah, one example per ayah | 3 s windows cut from each ayah, new window every 0.5 s |
| Base | `facebook/wav2vec2-base` (hidden 768) via `W2V2Light` | `jonatasgrosman/wav2vec2-large-xlsr-53-arabic` (hidden 1024) via `QuranWav2Vec2` |
| Checkpoint | `quran_model_final-alif-hamja_correction.pt` (flat state dict, 39 classes) | `streaming_quran_wav2vec.pt`, `{"model_state_dict": ...}` |
| Vocab | hard-coded `TRAINED_PHONEMES` list | `vocab.json` saved from the notebook (sorted phoneme set) |

The new model matches the server tick loop: every 0.5 s the server decodes the trailing 3 s of audio, so training examples look exactly like that.

## 3. Training pipeline (Kaggle notebook, GPU)

**Data.** Kaggle dataset `ami0nai/quran-dataset`, folder `quran_full_data_collection_19_1008_murattal`. It has `quran_metadata.json` (1232 rows with `surah_id`, `ayah_id`, `audio`) and an `audio/` folder. Audio is loaded with soundfile (ffmpeg fallback), converted to mono 16 kHz, then peak-normalized to 0.95 for the whole ayah before slicing.

**Targets.** Text comes from `quran_transcript.Aya(surah, ayah).get().uthmani`. It goes through `normalize_initial_hamzat_wasl` and then `quran_transcript.quran_phonetizer(text, moshaf_rules).phonemes`.

Rules used for training: `MoshafAttributes(rewaya="hafs", madd_monfasel_len=4, madd_mottasel_len=4, madd_mottasel_waqf=5, madd_aared_len=4)`. There was **no** `ghunnah=4`.

Package: `quran-transcript==0.5.2` installed with `--no-deps`, plus an on-disk patch of `sifa.py` (alif "cannot start a phoneme script" no longer raises, and an index guard on `alif_tafkheem_and_tarqeeq`).

If the phonetizer fails, the fallback is the raw characters minus diacritics and spaces. Madd and shadda are expressed as **repeated tokens** (for example `ا ا ا ا`, `ب ب`).

**Vocabulary.** `DeterministicVocabulary`: `<pad>=0`, `<blank>=1`, then all phonemes seen in the data in **sorted order** starting at id 2. The vocab is rebuilt from the data, so it must be loaded from `vocab.json` and not rebuilt blindly.

**Chunking (`generate_streaming_chunks`).**
- `SAMPLE_RATE=16000`, `CHUNK_WINDOW_SAMPLES=48000` (3.0 s), `STRIDE_SAMPLES=8000` (0.5 s).
- Ayahs shorter than 3 s are skipped.
- Labels use linear time alignment: `samples_per_phoneme = len(audio) / len(phonemes)`. A window `[start, end)` gets phonemes `[int(start/spp), int(end/spp)+1)`.
- Each example has exactly 48000 samples and a CTC target sequence of its phoneme ids.

**Model (`QuranWav2Vec2`).**
- `AutoModel.from_pretrained(MODEL_ID)`, CNN feature extractor frozen (`_freeze_parameters()`), gradient checkpointing off.
- `phoneme_head = nn.Linear(hidden_size, vocab_size)`.
- `forward`: `base(input_values).last_hidden_state`, then the head, giving logits of shape `(B, T, V)`.
- `nn.CTCLoss(blank=1, zero_infinity=True)`, with input lengths equal to T for every item.

**Optimization.**
- AdamW with two groups: encoder `lr=3e-5`, head `lr=3e-4`. No scheduler.
- Gradient clipping at 1.0.
- Batch size 16 on GPU, 8 epochs.
- Features come from `AutoFeatureExtractor(MODEL_ID)`, which also applies its own per-input zero-mean and unit-variance normalization.
- No data augmentation.

**Validation.** `train_test_split(streaming_chunks, test_size=0.1)` on chunks. **This split is leaky**: overlapping windows from the same ayah land in both train and validation. Validation loss and PER from the notebook are optimistic. Best-val-loss checkpoint was saved (the best was epoch 8).

**Artifacts.** `streaming_quran_wav2vec.pt` and `vocab.json`, uploaded to Kaggle as the model `ami0nai/quran-model` (framework PyTorch, variation `default`, version 1). The uploaded file may be named `streaming_quran_wav2vec (1).pt`.

## 4. Inference contract

- Input: mono 16 kHz float waveform, window up to 48000 samples (3 s), run through `AutoFeatureExtractor(MODEL_ID)`.
- The model was **never trained with trailing zero padding** and never on windows longer than 3 s.
- Output: about 50 frames per second (20 ms, 320 samples per frame), so about 149 frames for a 3 s window.
- Decode: argmax per frame, collapse adjacent duplicates, **blank and pad reset adjacency** (so `ا blank ا` gives two alifs), then drop ids 0 and 1. This is the same decode as `_collapse_pred_ids` in `ml_engine.py`.
- Because repeats need a blank between them, fast elongations can lose a repeat.

## 5. Results so far

- Server simulation (3 s window, 0.5 s tick) on a training ayah (1:1) tracked the target closely.
- One external test: a phone recording by the developer of Surah 111 ayah 1, 7.68 s, decoded in one pass: **PER 13.2%** (5 deletions out of 38 target tokens).
  - Missing: two `ا` of a madd, one `و`, one `ب` of a shadda, one `َ`.
  - There were **no substitutions**: errors are duration (repeat count), not wrong phonemes.
- Speed for one 3 s window: **0.053 s on the Kaggle GPU**, **0.78 s on the Kaggle CPU** (too slow for a 0.5 s tick on CPU).

## 6. Known limitations

1. Linear-alignment labels are noisy (long madd stretches and fast passages are not uniform), so the model under-predicts repeated tokens.
2. Validation split is leaky; split by ayah (or by reciter) before chunking for honest numbers.
3. Only ~1 external recording tested; test more voices and phones.
4. Window edges are unstable: the first and last 2-3 phonemes of a window can be wrong or hallucinated (for example a stray `ذ` at a left edge).
5. Ayahs under 3 s never appear in training.
6. Possible spoken bismillah mismatch on ayah 1 of each surah was not checked.

Suggested improvements: split by ayah, forced alignment for labels (or train on full ayahs with CTC plus windows), noise and gain augmentation, collapse or count repeats separately for madd evaluation.

## 7. Integration checklist for the existing backend

The existing `ml_engine.py` and `main.py` already do live decoding (`decode_live_window`, 0.5 s heartbeat, trailing 3 s window, 16-frame lookahead, hysteresis RMS gate, open-mic resolution with the phonetic trie). Swapping the model needs these changes:

1. **Model class.** Replace `W2V2Light` with the notebook class:
   ```python
   class QuranWav2Vec2(nn.Module):
       def __init__(self, model_id, vocab_size):
           super().__init__()
           self.base = AutoModel.from_pretrained(model_id)
           self.phoneme_head = nn.Linear(self.base.config.hidden_size, vocab_size)
       def forward(self, input_values):
           return self.phoneme_head(self.base(input_values).last_hidden_state)
   ```
   `MODEL_ID = "jonatasgrosman/wav2vec2-large-xlsr-53-arabic"`. The old manual `feature_extractor -> feature_projection -> encoder` path was written for wav2vec2-base; just use `base(...)`.
2. **Checkpoint format.** Load `torch.load(path)["model_state_dict"]`. The old code reads `checkpoint["phoneme_head.weight"]` from a flat dict. Use `strict=True` for the first load; the current `strict=False` would silently hide a mismatched load.
3. **Extractor.** Use `AutoFeatureExtractor.from_pretrained(MODEL_ID)` instead of `facebook/wav2vec2-base`.
4. **Vocab.** Replace the hard-coded `TRAINED_PHONEMES` order with the exact `phoneme2id` from `vocab.json` (ids 0 and 1 are pad and blank). `V_SIZE` must equal `len(vocab)` from that file. The new order and size differ from the old 39-class list.
5. **Phonetizer rules.** The backend's `moshaf_rules` has `ghunnah=4`; the training notebook did not. Check that every token in the backend's expected streams (`TAJWEED_ONLY_INDEX`, the global trie) exists in the new vocab, and align the rules with training if not. Use the same `quran-transcript==0.5.2` and the same `sifa.py` patch.
6. **Source text.** Training used `Aya(s, a).get().uthmani`; the backend builds expected text from `row["ayah_ar"]` in `quran_metadata.json`. Confirm they produce identical phoneme streams.
7. **Trailing pad.** `_infer_pred_ids` appends 4800 zero samples and `decode_live_window` subtracts `CTC_PAD_FRAMES`. The new model never saw padding. Compare `pad=0` against `pad=4800` on real recordings before keeping it, and re-tune `LIVE_LEAD_FRAMES` (currently 16).
8. **Window length.** `STALL_DECODE_SAMPLES` decodes about 4 s, and `evaluate_audio` decodes whole ayahs. The model was trained on exactly 3 s windows. The 7.68 s one-pass test worked (13.2% PER), but verify the 4 s phrase decode and full-ayah paths on several recordings.
9. **Performance.** Needs a GPU. On CPU it exceeds the 0.5 s tick. If CPU is unavoidable: int8 dynamic quantization, a 1 s evaluation interval, and dropping stale ticks.
10. **Trie behavior.** The trie matcher is fuzzy (`err_rate=0.35`), and the new model's errors are mostly deletions, which it tolerates. Duration (madd) feedback should not rely on repeat counts from the model alone.

## 8. Hosting note

The laptop scripts (`start_backend.sh`, `stop_backend.sh`) run `uvicorn main:app` plus `ngrok http 8000`. For a GPU, the same backend can run inside a Kaggle notebook (GPU on, Internet on, `NGROK_TOKEN` in Kaggle Secrets, attach `ami0nai/quran-model`), start `uvicorn` in a thread, and expose it with `pyngrok`. Stop ngrok on the laptop first, since free ngrok accounts typically allow one agent session.

## 9. Vocabulary comparison (new `vocab.json` vs hard-coded `TRAINED_PHONEMES`)

- New vocab: **40 classes** = `<pad>=0`, `<blank>=1`, plus 38 tokens at ids 2-39 in sorted order (space is id 2). Old backend list: 39 classes (37 tokens). Expected checkpoint head: `phoneme_head.weight.shape[0] == 40`.
- Shared: 34 tokens, but **every id differs** from the old order, so the old order cannot be reused.
- In old list only (not in new vocab): `أ` `ة` `إ`.
- In new vocab only: `ز` `ض` `ظ` `۾`.

Backend code that depends on `TRAINED_PHONEMES` (must be rebuilt from `vocab.json`, keeping the variable name): `_is_feedback_char`, the target filtering in `evaluate_audio`, the plain-text fallback target, and the vocab construction at startup.

Loader to replace the hard-coded block:

```python
with open("vocab.json", encoding="utf-8") as f:
    _v = json.load(f)
vocab.clear()
vocab.phoneme2id = dict(_v)
vocab.id2phoneme = {i: p for p, i in _v.items()}
vocab.idx = max(_v.values()) + 1
TRAINED_PHONEMES = [p for p, i in sorted(_v.items(), key=lambda kv: kv[1]) if i >= 2]
assert V_SIZE == vocab.idx == 40
```
(with `checkpoint = torch.load(path, map_location="cpu")["model_state_dict"]` and `V_SIZE = checkpoint["phoneme_head.weight"].shape[0]`).

Behavior changes to verify:
1. `ز` `ض` `ظ` were deliberately excluded from feedback because the old model could not output them. Once they are in `TRAINED_PHONEMES` they become reportable. Count how often each appears in the training data first; if rare, keep them excluded from mistake feedback until validated.
2. `أ` `ة` `إ` can no longer be predicted. If expected streams still contain them, the trie and diff will expect tokens the model never emits. Run the check below.
3. `۾` is a new special token. Check where it appears in expected streams and make sure feedback and letter assignment handle it without mapping it to a display letter.

Check to run after the index is built with the final phonetizer rules:

```python
import json, collections
new_vocab = json.load(open("vocab.json", encoding="utf-8"))
cnt = collections.Counter()
for surah in TAJWEED_ONLY_INDEX.values():
    for ayah in surah.values():
        cnt.update(ayah["expected_full"])
print("In expected streams but NOT in vocab:", {t: n for t, n in cnt.items() if t not in new_vocab})
print("Token counts:", {t: cnt.get(t, 0) for t in new_vocab if not t.startswith("<")})
```
