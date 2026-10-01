import json
import os
import threading
import warnings
import Levenshtein
import numpy as np
import quran_transcript
import torch
import torch.nn as nn
import torchaudio
import torchaudio.functional as F
from transformers import AutoFeatureExtractor, AutoModel

from phonetic_trie import PhoneticTrie

# Guards model inference: heartbeat delta evals (to_thread) may overlap the
# final full-buffer eval, and torch isn't safe for concurrent forwards.
_INFERENCE_LOCK = threading.Lock()

warnings.filterwarnings("ignore")
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# Streaming model contract (see half-a-second-stride-training.ipynb + streaming_model_handoff.md):
# base jonatasgrosman/wav2vec2-large-xlsr-53-arabic, 3 s windows / 0.5 s stride,
# sorted vocab.json (40 classes), NO trailing zero-pad, extractor per-input norm.
MODEL_ID = "jonatasgrosman/wav2vec2-large-xlsr-53-arabic"
MODEL_PATH = os.environ.get("STREAMING_MODEL_PATH", "streaming_quran_wav2vec.pt")
VOCAB_PATH = os.environ.get("STREAMING_VOCAB_PATH", "vocab.json")


# 1. Model Definition (notebook Cell 6 verbatim, inference path returns logits)
class QuranWav2Vec2(nn.Module):

    def __init__(self, model_id, vocab_size):
        super().__init__()
        self.base = AutoModel.from_pretrained(model_id)
        try:
            self.base.feature_extractor._freeze_parameters()
        except Exception:
            pass
        try:
            self.base.config.gradient_checkpointing = False
            self.base.gradient_checkpointing_disable()
        except Exception:
            pass
        self.phoneme_head = nn.Linear(self.base.config.hidden_size, vocab_size)
        self.ctc_loss = nn.CTCLoss(blank=1, zero_infinity=True)

    def forward(self, input_values, attention_mask=None, targets=None, target_lengths=None):
        outputs = self.base(input_values, attention_mask=attention_mask)
        logits = self.phoneme_head(outputs.last_hidden_state)
        if targets is not None:
            log_probs = logits.transpose(0, 1).log_softmax(2)
            input_lengths = torch.full(
                (log_probs.size(1),), log_probs.size(0),
                dtype=torch.long, device=log_probs.device,
            )
            loss = self.ctc_loss(log_probs, targets, input_lengths, target_lengths)
            return loss.unsqueeze(0), logits
        return logits


# 2. Vocabulary Setup
class QPSVocabulary:

    def __init__(self):
        self.phoneme2id = {"<pad>": 0, "<blank>": 1}
        self.id2phoneme = {0: "<pad>", 1: "<blank>"}
        self.idx = 2

    def add(self, p):
        if p not in self.phoneme2id:
            self.phoneme2id[p] = self.idx
            self.id2phoneme[self.idx] = p
            self.idx += 1

    def clear(self):
        """Resets vocabulary maps cleanly back to initial state."""
        self.phoneme2id = {"<pad>": 0, "<blank>": 1}
        self.id2phoneme = {0: "<pad>", 1: "<blank>"}
        self.idx = 2

    def __len__(self):
        """Allows Python's len() function to read vocabulary size directly."""
        return self.idx


vocab = QPSVocabulary()
# Aligned to the streaming training notebook (Cell 3): NO ghunnah=4 there.
# Backend previously had ghunnah=4, which emits tokens the model never saw.
moshaf_rules = quran_transcript.MoshafAttributes(
    rewaya="hafs",
    madd_monfasel_len=4,
    madd_mottasel_len=4,
    madd_mottasel_waqf=5,
    madd_aared_len=4,
)

# 3. Phonetizer normalization (same as training notebook: problemalif.ipynb)
def normalize_initial_hamzat_wasl(text):
    text = text.strip()
    if text.startswith('ٱل') or text.startswith('ال'):
        return 'ءَ' + text[1:]
    elif text.startswith('ٱ') or text.startswith('ا'):
        return 'ءِ' + text[1:]
    return text

def safe_phonetizer(text, rules):
    normalized = normalize_initial_hamzat_wasl(text)
    try:
        return list(quran_transcript.quran_phonetizer(normalized, rules).phonemes)
    except Exception:
        try:
            return list(quran_transcript.quran_phonetizer(text, rules).phonemes)
        except Exception:
            return [c for c in normalized if c not in ' ۚۛۗۖ ۘۜ\u064b\u064c\u064d\u064e\u064f\u0650\u0651\u0652\u0670']

# 4. Load the exact training vocab (sorted order, ids 0/1 = pad/blank).
# V_SIZE comes from vocab.json so import stays light on weak machines
# (no 1.2 GB torch.load, no HF base download); the heavy model loads lazily
# on first inference (Kaggle GPU). TRAINED_PHONEMES keeps its name so all
# trie/feedback call sites keep working.
with open(VOCAB_PATH, "r", encoding="utf-8") as _vf:
    _saved_vocab = json.load(_vf)
vocab.clear()
vocab.phoneme2id = dict(_saved_vocab)
vocab.id2phoneme = {i: p for p, i in _saved_vocab.items()}
vocab.idx = max(_saved_vocab.values()) + 1
TRAINED_PHONEMES = [p for p, i in sorted(_saved_vocab.items(), key=lambda kv: kv[1]) if i >= 2]
V_SIZE = vocab.idx
print(f"Streaming vocab: {V_SIZE} classes from {VOCAB_PATH} (expect 40)")


def _ayah_phonetize_text(s_id: int, a_id: int, fallback_text: str) -> str:
    """Training-aligned source text: notebook used Aya(s,a).get().uthmani.
    Prefer that when the installed quran-transcript data supports it, else fall
    back to the row text from quran_metadata.json (offline/Kaggle-safe)."""
    try:
        return quran_transcript.Aya(s_id, a_id).get().uthmani
    except Exception:
        return fallback_text

# 4. Build Tajweed Index with the training-aligned phonetizer rules.
TAJWEED_ONLY_INDEX = {}
print("Loading Tajweed index...")
with open("quran_metadata.json", "r", encoding="utf-8") as f:
    data = json.load(f)

    # 3. Run the startup indexer using full-ayah phonetization (same as training)
    for row in data:
        s_id, a_id = int(row["surah_id"]), int(row["ayah_id"])
        if s_id not in TAJWEED_ONLY_INDEX:
            TAJWEED_ONLY_INDEX[s_id] = {}

        raw_text = row["ayah_ar"]
        words = raw_text.split()

        # Full-ayah phonetization (in-context cross-word rules preserved).
        # Phonetize the training-aligned text (Aya uthmani when available);
        # display words stay from the metadata row so the UI text is unchanged.
        phon_text = _ayah_phonetize_text(s_id, a_id, raw_text)
        full_phonemes = safe_phonetizer(phon_text, moshaf_rules)
        full_stream = "".join(full_phonemes)
        segments = full_stream.split()

        if len(segments) == len(words):
            word_list = [{"text": w, "phonemes": list(seg)} for w, seg in zip(words, segments)]
        else:
            # Fallback: per-word phonetization
            word_list = []
            for word in words:
                phoneme_list = safe_phonetizer(word, moshaf_rules)
                word_list.append({"text": word, "phonemes": phoneme_list})

        TAJWEED_ONLY_INDEX[s_id][a_id] = {"words": word_list, "expected_full": full_stream}

    print(
        f"🎉 Initialization complete! Stable vocab size: {len(vocab)}. Indexed {len(TAJWEED_ONLY_INDEX)} Surahs."
    )

# Remove empty surahs
TAJWEED_ONLY_INDEX = {k: v for k, v in TAJWEED_ONLY_INDEX.items() if v}

# 5. Lazy model + extractor (Kaggle GPU). Import stays light: no 1.2 GB
# checkpoint read and no HF download until the first real inference, so weak
# machines can still import, build the index, and run stubbed smoke tests.
_MODEL = None
_EXTRACTOR = None


def _ensure_model():
    """Load base + streaming head on first use (strict: hide nothing)."""
    global _MODEL, _EXTRACTOR
    if _MODEL is not None and _EXTRACTOR is not None:
        return _MODEL, _EXTRACTOR
    if not os.path.isfile(MODEL_PATH):
        raise FileNotFoundError(
            f"Streaming checkpoint not found: {MODEL_PATH}. "
            "On Kaggle, copy it from the attached ami0nai/quran-model "
            "as streaming_quran_wav2vec.pt (see deploy_colab.py)."
        )
    from transformers import AutoFeatureExtractor as _AFE
    _EXTRACTOR = _AFE.from_pretrained(MODEL_ID)
    _MODEL = QuranWav2Vec2(MODEL_ID, V_SIZE).to(DEVICE)
    _ckpt = torch.load(MODEL_PATH, map_location=DEVICE)
    _state = _ckpt.get("model_state_dict", _ckpt) if isinstance(_ckpt, dict) else _ckpt
    _head = _state.get("phoneme_head.weight", None) if isinstance(_state, dict) else None
    if _head is not None and _head.shape[0] != V_SIZE:
        raise RuntimeError(
            f"Head mismatch: checkpoint {_head.shape[0]} vs vocab {V_SIZE}"
        )
    _MODEL.load_state_dict(_state, strict=True)
    _MODEL.eval()
    print(f"Streaming model loaded: {MODEL_ID} head={V_SIZE} on {DEVICE}")
    return _MODEL, _EXTRACTOR


# Back-compat for any external probe touching ml_engine.model / extractor.
model = None
extractor = None


def _detect_ctc_stride() -> int:
    """Samples-per-output-frame from the wav2vec2 conv stack (320 for both
    wav2vec2-base and xlsr-53). Introspects the loaded model when available,
    else the verified constant so import never triggers a download."""
    try:
        m, _ = _ensure_model()
        prod = 1
        for layer in m.base.feature_extractor.conv_layers:
            prod *= layer.conv.stride[0]
        return int(prod)
    except Exception as exc:
        print(f"⚠️ [CTC STRIDE] using 320 (model not loaded yet: {exc})")
        return 320


CTC_STRIDE = 320
# Training never saw trailing zero-pad: pad=0 is the contract. Keep the
# frames math intact (0 // stride == 0) so decode_live_window needs no branch.
CTC_PAD_SAMPLES = 0
CTC_PAD_FRAMES = 0
print(f"CTC stride: {CTC_STRIDE} samples/frame, pad frames: {CTC_PAD_FRAMES}")


def _infer_pred_ids(audio_bytes: bytes) -> list:
    """Run model inference over raw 16-bit PCM bytes, returning the raw CTC
    argmax id list (one id per output frame, blanks/pads included). Split out
    of _bytes_to_prediction so overlapped-window live decoding can slice ids
    at frame boundaries BEFORE collapse (see Phase A) instead of diffing
    decoded text after the fact."""
    m, ext = _ensure_model()
    # 1. Convert raw bytes to numpy array (16-bit PCM)
    audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
    audio_np /= 32768.0

    # No trailing pad: the streaming model was trained on exact 3 s windows
    # with no zero-pad (pad=0 contract). The extractor applies the same
    # per-input zero-mean/unit-variance norm as training.
    inputs = ext(
        audio_np,
        sampling_rate=16000,
        return_tensors="pt",
    ).input_values.to(DEVICE)

    # Inference
    with _INFERENCE_LOCK:
        with torch.no_grad():
            logits = m(inputs)
            if isinstance(logits, (tuple, list)):
                logits = logits[-1]
            pred_ids = torch.argmax(logits[0], dim=-1)

    return [p.item() for p in pred_ids]


def _collapse_pred_ids(pred_ids, prev_in=None):
    """CTC Decode: collapse adjacent duplicates FIRST (preserves madd/long vowels),
    THEN remove blanks/pads. Matches the streaming notebook decode.

    prev_in threads the collapse state across slice boundaries: a phoneme
    spanning a cut must not be emitted twice. Returns (text, prev_out) where
    prev_out feeds the next slice. Called with no prev_in, prev_out is
    discarded and behavior is exactly the legacy single-shot decode.
    """
    final_pred = []
    prev = prev_in
    for token_id in pred_ids:
        if token_id in [0, 1]:  # skip <pad>, <blank> during collapse
            prev = None  # break adjacency across blanks
            continue
        token_str = vocab.id2phoneme.get(token_id, "")
        if token_str:
            if token_str != prev:
                final_pred.append(token_str)
            prev = token_str
    return "".join(final_pred), prev


def _bytes_to_prediction(audio_bytes: bytes) -> str:
    """Run model inference over raw 16-bit PCM bytes, returning the decoded
    phoneme prediction string (shared by evaluate_audio, live deltas, and
    open-mic resolution)."""
    pred_str, _ = _collapse_pred_ids(_infer_pred_ids(audio_bytes))
    print(f"🔮 [ML ENGINE] Cleaned AI Prediction String: '{pred_str}'")
    return pred_str


def predict_phonemes_bytes(audio_bytes: bytes) -> str:
    """Public bytes-based prediction (live heartbeat deltas, open-mic lookup)."""
    return _bytes_to_prediction(audio_bytes)


def decode_live_window(window_bytes: bytes, new_frames: int, prev, is_first: bool,
                       lead_frames: int):
    """Phase A live decode: infer CTC ids over a trailing context window, then
    collapse ONLY the newly-covered frames.

    window_bytes: trailing-window PCM (up to LIVE_WINDOW_SAMPLES of audio).
    new_frames: output frames corresponding to audio not yet emitted.
    prev: collapse carryover from the previous slice (None to start fresh).
    is_first: first decode of the session -> emit from frame 0.
    lead_frames: right-context lookahead; the slice always ends this far
      before the pad so no emitted frame decodes with "silence ahead".
      Consecutive slices tile exactly (experiment-verified).

    Returns (text, prev_out). Empty text (not an error) when there is not yet
    enough audio past the lookahead.
    """
    ids = _infer_pred_ids(window_bytes)
    audio_frames = len(ids) - CTC_PAD_FRAMES
    if is_first:
        lo, hi = 0, audio_frames - lead_frames
    else:
        lo, hi = audio_frames - new_frames - lead_frames, audio_frames - lead_frames
    lo = max(0, lo)
    if hi <= lo:
        return "", prev
    return _collapse_pred_ids(ids[lo:hi], prev)


def _load_audio_bytes(audio_path: str) -> bytes:
    """Load an audio FILE as 16 kHz mono 16-bit PCM bytes (training parity).

    Real container files (WAV/MP3/M4A at any rate/channels — e.g. the 48 kHz
    stereo phone recording): decoded with soundfile, mixed to mono, resampled
    to 16 kHz via torchaudio (already a dependency), peak-normalized to 0.95
    exactly like training's load_audio_safely, then int16-encoded.
    Headerless raw temp segments (what main.py writes: bare int16 mono 16 kHz
    bytes with a .wav suffix): soundfile raises, so fall back to a raw read —
    bit-identical to the old behavior. The live socket path never touches this
    (mic bytes are already 16 kHz mono int16).
    """
    try:
        import soundfile as _sf
        y, sr = _sf.read(audio_path, dtype="float32")
    except Exception:
        with open(audio_path, "rb") as f:
            return f.read()
    if y.ndim > 1:
        y = np.mean(y, axis=1)
    if sr != 16000:
        y_t = torch.from_numpy(np.ascontiguousarray(y))
        y = F.resample(y_t, sr, 16000).numpy()
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if peak > 0:
        y = (y / peak) * 0.95
    pcm = np.clip(np.asarray(y) * 32768.0, -32768, 32767).astype(np.int16)
    return pcm.tobytes()


def _predict_phonemes(audio_path: str) -> str:
    """Load an audio file (any rate/channels, or a headerless raw segment)
    and run model inference, returning the decoded phoneme prediction string
    (shared by evaluate_audio and sequential mode)."""
    return _bytes_to_prediction(_load_audio_bytes(audio_path))


def DIACRITICS_STRIP():
    return set('ًٌٍَُِّْٰٓۥۦۖۗۚۛۜ')


def strip_diacritics(s):
    return "".join(c for c in s if c not in DIACRITICS_STRIP())


def _align_with_ops(ref, hyp):
    """Levenshtein alignment with traceback.

    Returns a list of (ref_char, hyp_char) pairs aligned position by position,
    where either side may be None to represent a deletion/insertion gap.
    """
    import unicodedata  # noqa

    n, m = len(ref), len(hyp)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = i
    for j in range(1, m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j - 1] + cost,
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
            )

    pairs = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + (
            0 if ref[i - 1] == hyp[j - 1] else 1
        ):
            pairs.append((ref[i - 1], hyp[j - 1]))
            i -= 1
            j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            pairs.append((ref[i - 1], None))
            i -= 1
        elif j > 0 and dp[i][j] == dp[i][j - 1] + 1:
            pairs.append((None, hyp[j - 1]))
            j -= 1
        elif i > 0 and j > 0:
            pairs.append((ref[i - 1], hyp[j - 1]))
            i -= 1
            j -= 1
        elif i > 0:
            pairs.append((ref[i - 1], None))
            i -= 1
        else:
            pairs.append((None, hyp[j - 1]))
            j -= 1
    pairs.reverse()
    return pairs


def _is_diacritic(ch):
    import unicodedata

    if ch in DIACRITICS_STRIP():
        return True
    return unicodedata.category(ch).startswith("M")


def _is_feedback_char(ch):
    """True if a character in the reference can actually be verified by the model.

    Only tokens in the streaming vocab are reportable. The special token `۾`
    is excluded (never accuse the user on a control symbol); `ز/ض/ظ` are now
    reportable since the streaming model emits them (old 39-class model could
    not). Marks outside the vocab (sukun, shadda, tanween, maddah, waqf, and
    the retired `أ/ة/إ`) stay excluded.
    """
    if ch == "۾":
        return False
    return ch in TRAINED_PHONEMES


def _assign_letters(display_text, consonant_statuses):
    """Build the per-character {ch, status} list for a displayed word.

    consonant_statuses: list of 'ok'/'miss' aligned to the word's consonants
    (as the model sees them). Diacritics and out-of-vocabulary letters render
    as 'neutral'; remaining consonants without a known status default to 'miss'.
    """
    letters = []
    cidx = 0
    for ch in display_text:
        if ch == "۾":
            letters.append({"ch": ch, "status": "neutral"})
        elif _is_diacritic(ch):
            letters.append({"ch": ch, "status": "neutral"})
        elif ch in TRAINED_PHONEMES:
            status = consonant_statuses[cidx] if cidx < len(consonant_statuses) else "miss"
            letters.append({"ch": ch, "status": status})
            cidx += 1
        else:
            letters.append({"ch": ch, "status": "neutral"})
    return letters


def _build_mistakes(word_results, word_data, expected_str, diff, max_msgs=6):
    """Convert the full-ayah diff into plain-text mistake messages.

    Only emittable reference characters (_is_feedback_char) are reported, so
    the model never fabricates errors on marks it cannot output (sukun, shadda,
    tanween, etc.). Whole words that weren't read produce a single summary line
    and suppress noisy per-character messages.
    """
    if not diff or not expected_str or len(word_data) == 0:
        return []

    # Try to map diff characters to expected words via the full stream spacing.
    # Without word mapping we cannot name the ayah word, so skip char feedback.
    expected_words = expected_str.split()
    word_map_ok = len(expected_words) == len(word_data)
    if not word_map_ok:
        return []

    # Track words that were not recognized at all.
    missed = {i for i, res in enumerate(word_results) if not res["is_read"]}

    mistakes = []
    word_idx = 0
    for pair in diff:
        if len(mistakes) >= max_msgs:
            break
        e, h = pair.get("e"), pair.get("h")
        status = pair.get("status")

        if e is not None and e == " ":
            word_idx += 1
            continue

        if word_idx >= len(word_data):
            break

        if word_idx in missed:
            continue  # whole-word message already covers this ayah word

        if e is not None and not _is_feedback_char(e):
            continue  # unverifiable reference char (sukun, shadda, tanween, ...)

        word_label = word_data[word_idx]["text"]

        if status == "S" and e is not None and h is not None and h != " ":
            msg = f'In "\u00ab{word_label}\u00bb", you said "\u00ab{h}\u00bb" instead of "\u00ab{e}\u00bb".'
            mistakes.append(msg)
        elif status == "D" and e is not None:
            if e in DIACRITICS_STRIP():
                msg = f'In "\u00ab{word_label}\u00bb", you missed the harakah "\u00ab{e}\u00bb".'
            else:
                msg = f'In "\u00ab{word_label}\u00bb", you skipped the letter "\u00ab{e}\u00bb".'
            mistakes.append(msg)
        elif status == "I" and h is not None and h != " ":
            msg = f'You said an extra "\u00ab{h}\u00bb" in "\u00ab{word_label}\u00bb".'
            mistakes.append(msg)

    # Whole-word misses.
    for i, res in enumerate(word_results):
        if res["is_read"]:
            continue
        mistakes.append(
            f'You didn\'t recite the word "\u00ab{res["text"]}\u00bb" correctly.'
        )
        if len(mistakes) >= max_msgs:
            break

    return mistakes


def evaluate_audio(surah_id: int, ayah_id: int, audio_path: str):
    if (
        surah_id not in TAJWEED_ONLY_INDEX
        or ayah_id not in TAJWEED_ONLY_INDEX[surah_id]
    ):
        return []

    word_data = TAJWEED_ONLY_INDEX[surah_id][ayah_id]["words"]
    pred_str = _predict_phonemes(audio_path)

    word_results = []
    expected_parts = []

    # Helper function to extract valid matching letters from raw text if phonetizer fails
    def get_fallback_target(raw_text):
        # Strips all tashkeel/diacritics and keeps only characters inside TRAINED_PHONEMES
        diacritics = [
            "ِ",
            "ُ",
            "َ",
            "ْ",
            "ّ",
            "ً",
            "ٌ",
            "ٍ",
            "ٰ",
            "ٓ",
            "ۦ",
            "ۧ",
        ]
        clean_text = "".join([c for c in raw_text if c not in diacritics])
        # Only keep letters your model's vocabulary actually supports
        return "".join([c for c in clean_text if c in TRAINED_PHONEMES])

    # 7. Check which words match the speech detected
    pred_stripped = strip_diacritics(pred_str)

    for word_obj in word_data:
        # Filter out characters from target phonemes that don't exist in vocabulary (like ڇ)
        target_phonemes = [
            p for p in word_obj["phonemes"] if p in TRAINED_PHONEMES
        ]
        target_str = "".join(target_phonemes)

        # FIX: If phonetizer failed completely (Target: ''), use our vocabulary-matched plain text fallback
        if not target_str:
            target_str = get_fallback_target(word_obj["text"])
            print(
                f"⚠️ [FALLBACK] Phonetizer failed for '{word_obj['text']}'. Using plain target letters: '{target_str}'"
            )

        # If both attempts yield no trackable characters, skip safely
        if not target_str:
            word_results.append(
                {"text": word_obj["text"], "is_read": False, "letters": _assign_letters(word_obj["text"], [])}
            )
            continue

        expected_parts.append(target_str)

        target_stripped = strip_diacritics(target_str)

        win_len = len(target_stripped)
        has_been_read = False
        best_i = None
        best_dist = None

        # Exact substring match (compare consonant-only for robustness)
        if target_stripped in pred_stripped:
            has_been_read = True
            best_i = pred_stripped.index(target_stripped)
            best_dist = 0
        else:
            # Fallback sliding window Levenshtein matching
            if len(pred_stripped) >= win_len:
                for i in range(len(pred_stripped) - win_len + 1):
                    sub = pred_stripped[i : i + win_len]
                    dist = Levenshtein.distance(sub, target_stripped)
                    if best_i is None or dist < best_dist:
                        best_i, best_dist = i, dist
                    if dist <= max(1, int(win_len * 0.35)):
                        has_been_read = True
                        break
            else:
                dist = Levenshtein.distance(pred_stripped, target_stripped)
                best_i, best_dist = 0, dist
                has_been_read = (
                    (dist <= max(1, int(win_len * 0.4))) if pred_stripped else False
                )

        # Build per-letter statuses for this word.
        consonant_statuses = []
        if has_been_read and best_i is not None:
            window = pred_stripped[best_i : best_i + win_len]
            for rc, hc in _align_with_ops(target_stripped, window):
                if rc is None:
                    continue
                consonant_statuses.append("ok" if hc == rc else "miss")
        else:
            consonant_statuses = ["miss"] * win_len

        letters = _assign_letters(word_obj["text"], consonant_statuses)

        print(
            f"   -> Word: '{word_obj['text']}' | Target Check: '{target_stripped}' vs Predicted: '{pred_stripped}' -> Result: {has_been_read}"
        )
        word_results.append(
            {"text": word_obj["text"], "is_read": has_been_read, "letters": letters}
        )

    real_text = " ".join(w["text"] for w in word_data)
    expected_full = TAJWEED_ONLY_INDEX[surah_id][ayah_id].get("expected_full", "")
    expected_str = expected_full if expected_full else " ".join(expected_parts)
    dist = Levenshtein.distance(pred_str, expected_str) if expected_str else 0
    accuracy = max(0, 100 - (dist / len(expected_str) * 100)) if expected_str else 0.0

    # Phoneme Error Rate: edit distance over the full phoneme stream (harakat included)
    per = round(dist / len(expected_str) * 100, 2) if expected_str else 0.0

    # Full-ayah alignment diff for the colored transcription lines.
    diff = []
    for e, h in _align_with_ops(expected_str, pred_str):
        if e is None:
            status = "I"
        elif h is None:
            status = "D"
        elif e == h:
            status = "M"
        else:
            status = "S"
        diff.append({"e": e, "h": h, "status": status})

    # Plain-text mistake feedback (excludes model-unverifiable reference chars).
    mistakes = _build_mistakes(word_results, word_data, expected_str, diff)

    return {
        "words": word_results,
        "real_text": real_text,
        "expected": expected_str,
        "predicted": pred_str,
        "accuracy": round(accuracy, 2),
        "per": per,
        "diff": diff,
        "mistakes": mistakes,
    }


def get_surah_ayah_ids(surah_id: int) -> list[int]:
    """Return sorted ayah ids present in the index for a surah (sequential order)."""
    if surah_id not in TAJWEED_ONLY_INDEX:
        return []
    return sorted(TAJWEED_ONLY_INDEX[surah_id].keys())


def get_surah_words(surah_id: int) -> list[list[dict]]:
    """Return a list (one per ayah, in order) of {'text', 'phonemes'} word dicts."""
    ayahs = get_surah_ayah_ids(surah_id)
    result = []
    for a_id in ayahs:
        result.append(TAJWEED_ONLY_INDEX[surah_id][a_id]["words"])
    return result


def _get_fallback_target(raw_text):
    """Strips tashkeel/diacritics and keeps only characters inside TRAINED_PHONEMES."""
    diacritics = [
        "ِ", "ُ", "َ", "ْ", "ّ", "ً", "ٌ", "ٍ", "ٰ", "ٓ", "ۦ", "ۧ",
    ]
    clean_text = "".join([c for c in raw_text if c not in diacritics])
    return "".join([c for c in clean_text if c in TRAINED_PHONEMES])


class GuidedLiveTrie:
    """Ordered, monotonic live word-confirmation pointer over an ayah's words.

    The user's decoded phoneme stream is fed in small beats; `confirm()` tries
    to lock the *next* unread word against the expected target, in order, using
    the same exact-substring / sliding-window Levenshtein rule as evaluate_audio
    so interim results and the authoritative final stay consistent.
    """

    def __init__(self, word_data):
        self.words = word_data
        self.targets = []
        for w in word_data:
            target = "".join(p for p in w["phonemes"] if p in TRAINED_PHONEMES)
            if not target:
                target = _get_fallback_target(w["text"])
            self.targets.append(strip_diacritics(target))
        self._idx = 0
        self._tail = ""
        self.trie = PhoneticTrie()
        for i, t in enumerate(self.targets):
            if t:
                self.trie.insert(t, word_idx=i)

    @property
    def next_word_index(self):
        return self._idx

    @property
    def pending_tail(self):
        return self._tail

    @property
    def active_index(self):
        """Index of the word currently expected next (the live 'spotlight'
        pointer), or None once every word in the ayah has confirmed.
        Read-only: only confirm() ever advances _idx, so reading this never
        mutates trie state."""
        return self._idx if self._idx < len(self.words) else None

    def feed(self, pred_str):
        self._tail += strip_diacritics(pred_str)

    def confirm(self):
        newly = []
        while self._idx < len(self.words) and self._tail:
            target = self.targets[self._idx]
            if not target:
                self._idx += 1
                continue
            span = self._find_span(target)
            if span is None:
                # Follow-along tolerance: if the CURRENT word's phonemes are
                # missing from the decoded stream (e.g. clipped at the mic
                # start) but the NEXT word clearly matches, advance past the
                # gap instead of freezing the spotlight on the word the model
                # never saw. Never skips a word whose successor is absent.
                nxt = self._idx + 1
                if nxt < len(self.words) and self.targets[nxt]:
                    nxt_span = self._find_span(self.targets[nxt], budget_mult=0.6)
                    if nxt_span is not None:
                        self._idx += 1
                        continue
                break
            start, end = span
            window = self._tail[start:end]
            self._tail = self._tail[end:]

            win = len(target)
            consonant_statuses = []
            for rc, hc in _align_with_ops(target, window):
                if rc is None:
                    continue
                consonant_statuses.append("ok" if hc == rc else "miss")
            if not consonant_statuses:
                consonant_statuses = ["miss"] * win

            newly.append(
                {
                    "index": self._idx,
                    "text": self.words[self._idx]["text"],
                    "is_read": True,
                    "letters": _assign_letters(self.words[self._idx]["text"], consonant_statuses),
                }
            )
            self._idx += 1
        return newly

    def _find_span(self, target, budget_mult=1.0):
        """Return (start, end) of target in the tail (early bounded region only,
        so word order is respected), or None."""
        tail = self._tail
        win = len(target)
        if win == 0:
            return None
        bound = min(len(tail), max(len(target) * 4, len(target) + 40))

        if target in tail[:bound]:
            start = tail.index(target)
            return (start, start + win)

        if len(tail) >= win:
            best = None
            for i in range(min(bound, len(tail) - win + 1)):
                sub = tail[i : i + win]
                dist = Levenshtein.distance(sub, target)
                if best is None or dist < best[0]:
                    best = (dist, i)
                if dist <= max(1, int(win * 0.35 * budget_mult)):
                    break
            if best and best[0] <= max(1, int(win * 0.35 * budget_mult)):
                return (best[1], best[1] + win)
            return None

        if not tail:
            return None
        dist = Levenshtein.distance(tail, target)
        if dist <= max(1, int(win * 0.4 * budget_mult)):
            return (0, len(tail))
        return None


_GLOBAL_AYAH_TRIE = None


def _ensure_global_ayah_trie():
    global _GLOBAL_AYAH_TRIE
    if _GLOBAL_AYAH_TRIE is None:
        _GLOBAL_AYAH_TRIE = PhoneticTrie()
        for s_id in TAJWEED_ONLY_INDEX:
            for a_id in TAJWEED_ONLY_INDEX[s_id]:
                stream = TAJWEED_ONLY_INDEX[s_id][a_id]["expected_full"]
                _GLOBAL_AYAH_TRIE.insert(list(stream), surah_id=s_id, ayah_id=a_id)
    return _GLOBAL_AYAH_TRIE


def resolve_ayah(pred_str, surahs=None, err_rate=0.35, min_tokens=5):
    """Open-mic resolution: decide which ayah best explains the predicted stream.

    surahs (optional set) narrows the candidate pool. Returns
    (surah_id, ayah_id, cost, score, depth) or None when ambiguous/insufficient.
    """
    if not pred_str or len(strip_diacritics(pred_str)) < min_tokens:
        return None
    trie = _ensure_global_ayah_trie()
    return trie.search_fuzzy(
        list(pred_str),
        allowed_surahs=surahs,
        err_rate=err_rate,
        min_tokens=min_tokens,
    )


_ADVANCE_MIN_ANCHOR = 5
_ADVANCE_ERR_RATE = 0.35


def match_ayah_in_stream(stream, pred_str):
    """Best-fit alignment of an ayah's canonical phoneme stream anywhere in a
    (diacritics-stripped) predicted stream, using the same sliding-window
    Levenshtein rule as GuidedLiveTrie._find_span. Returns (start, end, dist)
    or None when no alignment within the error budget exists.

    Unlike the prefix trie, this can anchor a tail that starts mid-ayah, which
    is exactly the live-advance case: the recent window begins inside the
    previous ayah and only later covers the start of the next one.
    """
    target = strip_diacritics(stream)
    body = strip_diacritics(pred_str or "")
    win = len(target)
    if win == 0 or not body:
        return None
    bound = min(len(body), max(win * 4, win + 40))
    budget = max(1, int(win * _ADVANCE_ERR_RATE))
    if target in body[:bound]:
        start = body.index(target)
        return (start, start + win, 0)
    if len(body) >= win:
        best = None
        for i in range(min(bound, len(body) - win + 1)):
            sub = body[i : i + win]
            dist = Levenshtein.distance(sub, target)
            if best is None or dist < best[0]:
                best = (dist, i)
            if dist <= budget:
                break
        if best and best[0] <= budget:
            return (best[1], best[1] + win, best[0])
        return None
    dist = Levenshtein.distance(body, target)
    if dist <= budget:
        return (0, len(body), dist)
    return None


def advance_target(pred_str, cur_s, cur_a, allowed_surahs=None):
    """Find the FURTHEST later ayah of surah `cur_s` whose canonical stream is
    best-aligned anywhere within pred_str with enough anchor coverage. Returns
    (surah_id, ayah_id, score) or None. Walking later ayahs in order and keeping
    the last genuine match biases the answer to wherever the user has actually
    reached (their latest decoded tokens sit at the tail of the window)."""
    if allowed_surahs is not None and cur_s not in allowed_surahs:
        return None
    if not pred_str or len(strip_diacritics(pred_str)) < _ADVANCE_MIN_ANCHOR:
        return None
    best = None
    for a_id in get_surah_ayah_ids(cur_s):
        if a_id <= cur_a:
            continue
        stream = TAJWEED_ONLY_INDEX[cur_s][a_id]["expected_full"]
        span = match_ayah_in_stream(stream, pred_str)
        if span is None:
            continue
        start, end, dist = span
        if end - start < _ADVANCE_MIN_ANCHOR:
            continue
        win = len(strip_diacritics(stream))
        score = 1.0 - (dist / win if win else 0.0)
        best = (cur_s, a_id, round(score, 4))
    return best