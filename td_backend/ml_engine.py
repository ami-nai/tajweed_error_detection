import json
import torch
import torch.nn as nn
import os
import torchaudio
import torchaudio.functional as F
import numpy as np
import Levenshtein
import quran_transcript
import warnings
from transformers import AutoFeatureExtractor, AutoModel

warnings.filterwarnings('ignore')
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

vocab = QPSVocabulary()
moshaf_rules = quran_transcript.MoshafAttributes(
    rewaya="hafs", ghunnah=4, madd_monfasel_len=4,
    madd_mottasel_len=5, madd_mottasel_waqf=5, madd_aared_len=2
)

# 3. Build Tajweed Index and populate Vocabulary
TAJWEED_ONLY_INDEX = {}
print("Loading Tajweed index...")
with open("quran_metadata.json", 'r', encoding='utf-8') as f:
    data = json.load(f)
    for row in data:
        s_id, a_id = int(row['surah_id']), int(row['ayah_id'])
        if s_id not in TAJWEED_ONLY_INDEX: TAJWEED_ONLY_INDEX[s_id] = {}

        if a_id not in TAJWEED_ONLY_INDEX[s_id]:
            raw_text = row['ayah_ar']
            try:
                clean_text = raw_text.replace("ٰ", "ا").replace("ٱ", "ا")
                p_obj = quran_transcript.quran_phonetizer(clean_text, moshaf_rules)
                phoneme_list = list(p_obj.phonemes)
                TAJWEED_ONLY_INDEX[s_id][a_id] = {'text': raw_text, 'phonemes': phoneme_list}
                for p in phoneme_list: vocab.add(p)
            except:
                continue

# Remove empty surahs
TAJWEED_ONLY_INDEX = {k: v for k, v in TAJWEED_ONLY_INDEX.items() if v}

# 4. Load Model Weights
print(f"Loading Model... Vocabulary size: {vocab.idx}")
model = W2V2Light(vocab.idx).to(DEVICE)
model.load_state_dict(torch.load("quran_model_final.pt", map_location=DEVICE, weights_only=True), strict=False)
model.eval()

extractor = AutoFeatureExtractor.from_pretrained("facebook/wav2vec2-base")

# 5. Core Evaluation Function
def evaluate_audio(surah_id: int, ayah_id: int, audio_path: str):
    if surah_id not in TAJWEED_ONLY_INDEX or ayah_id not in TAJWEED_ONLY_INDEX[surah_id]:
        return None, "Error: Ayah not found in Tajweed index", "", "0.00%"

    data = TAJWEED_ONLY_INDEX[surah_id][ayah_id]
    raw_text = data['text']
    true_phonemes = data['phonemes']
    expected_str = " ".join(true_phonemes)

    # 1. Load the raw bytes directly
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()
    
    # 2. Convert raw bytes to numpy array (16-bit PCM)
    audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
    
    # 3. Normalize
    audio_np /= 32768.0
    
    # 4. Convert to Tensor
    waveform = torch.from_numpy(audio_np).unsqueeze(0) # Shape: (1, samples)
    
    # 5. Handle Padding (Matching your previous logic)
    # Instead of wf, we now use our waveform tensor
    waveform_padded = torch.nn.functional.pad(waveform, (0, 4800), mode='constant')

    # 6. Extract features
    # Extractor handles the conversion from the padded tensor to model input
    inputs = extractor(waveform_padded.squeeze().numpy(), sampling_rate=16000, return_tensors="pt").input_values.to(DEVICE)

    # Inference
    with torch.no_grad():
        logits = model(inputs)
        pred_ids = torch.argmax(logits[0], dim=-1)

    pred_phonemes = [vocab.id2phoneme.get(p.item(), "") for p in pred_ids if p.item() not in [0, 1]]

    # CTC Deduplication
    final_pred = []
    prev = None
    for p in pred_phonemes:
        if p != prev: 
            final_pred.append(p)
            prev = p

    predicted_str = " ".join(final_pred)
    dist = Levenshtein.distance("".join(final_pred), "".join(true_phonemes))
    acc = max(0, 100 - (dist / len(true_phonemes) * 100))

    return raw_text, expected_str, predicted_str, f"{acc:.2f}%"