import json
import os
import warnings
import Levenshtein
import numpy as np
import quran_transcript
import torch
import torch.nn as nn
import torchaudio
import torchaudio.functional as F
from transformers import AutoFeatureExtractor, AutoModel

warnings.filterwarnings("ignore")
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


# 1. Model Definition
class W2V2Light(nn.Module):

    def __init__(self, v_size):
        super().__init__()
        self.base = AutoModel.from_pretrained("facebook/wav2vec2-base")
        self.phoneme_head = nn.Linear(768, v_size)

    def forward(self, x):
        x = self.base.feature_extractor(x).transpose(1, 2)
        x = self.base.feature_projection(x)[0]
        x = self.base.encoder(x).last_hidden_state
        return self.phoneme_head(x)


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
moshaf_rules = quran_transcript.MoshafAttributes(
    rewaya="hafs",
    ghunnah=4,
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

# 4. Determine the model output layer size from the saved checkpoint.
checkpoint = torch.load("quran_model_final-alif-hamja_correction.pt", map_location="cpu", weights_only=True)
V_SIZE = checkpoint["phoneme_head.weight"].shape[0]
print(f"Checkpoint output layer size: {V_SIZE}")

# 4. Build Tajweed Index and populate Vocabulary
TAJWEED_ONLY_INDEX = {}
print("Loading Tajweed index...")
with open("quran_metadata.json", "r", encoding="utf-8") as f:
    data = json.load(f)

    # 1. Full training vocabulary in exact order.
    # CRITICAL: This is the EXACT order the checkpoint was trained with, extracted
    # by replaying the training notebook's dataset build (uthmani_script +
    # moshaf_rules above + audio-presence filter) on quran_full_data_collection_new.
    # 2 base classes <pad>,<blank> + 37 phoneme tokens = 39 classes = checkpoint head.
    TRAINED_PHONEMES = ['ت', 'َ', 'ب', ' ', 'ي', 'د', 'ا', 'ء', 'ِ', 'ۦ', 'ل', 'ه', 'ن', 'و', 'ڇ', 'م', 'غ', 'ع', 'ُ', 'ك', 'س', 'ص', 'ر', 'ذ', 'أ', 'ح', 'ة', 'ط', 'ف', 'ج', 'ق', 'ۥ', 'ں', 'ش', 'خ', 'ث', 'إ']

    # 2. Reset vocab and keep only the classes the checkpoint actually supports
    vocab.clear()
    for token in TRAINED_PHONEMES[: V_SIZE - 2]:
        vocab.add(token)

    # 3. Safety Padding to forcefully protect model dimensions against runtime array variance
    while len(vocab) < V_SIZE:
        vocab.add(f"<dummy_pad_{len(vocab)}>")

    # 3. Run the startup indexer using full-ayah phonetization (same as training)
    for row in data:
        s_id, a_id = int(row["surah_id"]), int(row["ayah_id"])
        if s_id not in TAJWEED_ONLY_INDEX:
            TAJWEED_ONLY_INDEX[s_id] = {}

        raw_text = row["ayah_ar"]
        words = raw_text.split()

        # Full-ayah phonetization (in-context cross-word rules preserved)
        full_phonemes = safe_phonetizer(raw_text, moshaf_rules)
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

# 5. Load Model Weights
print(f"Loading Model... Target architectural output layer size: {V_SIZE}")
# Fixed parameter layout error by passing positional argument matching W2V2Light constructor definition
model = W2V2Light(V_SIZE).to(DEVICE)
model.load_state_dict(checkpoint, strict=False)
model.eval()

extractor = AutoFeatureExtractor.from_pretrained("facebook/wav2vec2-base")


def evaluate_audio(surah_id: int, ayah_id: int, audio_path: str):
    if (
        surah_id not in TAJWEED_ONLY_INDEX
        or ayah_id not in TAJWEED_ONLY_INDEX[surah_id]
    ):
        return []

    word_data = TAJWEED_ONLY_INDEX[surah_id][ayah_id]["words"]

    # 1. Load the raw bytes directly
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    # 2. Convert raw bytes to numpy array (16-bit PCM)
    audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
    audio_np /= 32768.0

    waveform = torch.from_numpy(audio_np).unsqueeze(0)  # Shape: (1, samples)
    waveform_padded = torch.nn.functional.pad(
        waveform, (0, 4800), mode="constant"
    )

    inputs = (
        extractor(
            waveform_padded.squeeze().numpy(),
            sampling_rate=16000,
            return_tensors="pt",
        )
        .input_values.to(DEVICE)
    )

    # Inference
    with torch.no_grad():
        logits = model(inputs)
        pred_ids = torch.argmax(logits[0], dim=-1)

    # CTC Decode: collapse adjacent duplicates FIRST (preserves madd/long vowels),
    # THEN remove blanks/pads. Matches notebook training decode (cell 13).
    final_pred = []
    prev = None
    for p in pred_ids:
        token_id = p.item()
        if token_id in [0, 1]:  # skip <pad>, <blank> during collapse
            prev = None  # break adjacency across blanks
            continue
        token_str = vocab.id2phoneme.get(token_id, "")
        if token_str and not token_str.startswith("<dummy_pad"):
            if token_str != prev:
                final_pred.append(token_str)
            prev = token_str

    pred_str = "".join(final_pred)
    print(f"🔮 [ML ENGINE] Cleaned AI Prediction String: '{pred_str}'")

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

    # Diacritics and Madd markers to strip for consonant-only matching
    # (model may not predict these for unseen verses)
    DIACRITICS = set('ًٌٍَُِّْٰٓۥۦۖۗۚۛۜ')

    def strip_diacritics(s):
        return "".join(c for c in s if c not in DIACRITICS)

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
            word_results.append({"text": word_obj["text"], "is_read": False})
            continue

        expected_parts.append(target_str)

        target_stripped = strip_diacritics(target_str)

        has_been_read = False
        # Exact substring match (compare consonant-only for robustness)
        if target_stripped in pred_stripped:
            has_been_read = True
        else:
            # Fallback sliding window Levenshtein matching
            win_len = len(target_stripped)
            if len(pred_stripped) >= win_len:
                for i in range(len(pred_stripped) - win_len + 1):
                    sub = pred_stripped[i : i + win_len]
                    dist = Levenshtein.distance(sub, target_stripped)
                    if dist <= max(1, int(win_len * 0.35)):
                        has_been_read = True
                        break
            else:
                dist = Levenshtein.distance(pred_stripped, target_stripped)
                has_been_read = (
                    (dist <= max(1, int(win_len * 0.4))) if pred_stripped else False
                )

        print(
            f"   -> Word: '{word_obj['text']}' | Target Check: '{target_stripped}' vs Predicted: '{pred_stripped}' -> Result: {has_been_read}"
        )
        word_results.append({"text": word_obj["text"], "is_read": has_been_read})

    real_text = " ".join(w["text"] for w in word_data)
    expected_full = TAJWEED_ONLY_INDEX[surah_id][ayah_id].get("expected_full", "")
    expected_str = expected_full if expected_full else " ".join(expected_parts)
    dist = Levenshtein.distance(pred_str, expected_str) if expected_str else 0
    accuracy = max(0, 100 - (dist / len(expected_str) * 100)) if expected_str else 0.0

    return {
        "words": word_results,
        "real_text": real_text,
        "expected": expected_str,
        "predicted": pred_str,
        "accuracy": round(accuracy, 2),
    }