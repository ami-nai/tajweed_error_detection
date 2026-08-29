import json
import os
import tempfile
import numpy as np
import torch
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from ml_engine import TAJWEED_ONLY_INDEX, evaluate_audio

app = FastAPI()

# 1. Load the Silero VAD model at startup
print("Loading Silero VAD model locally...")
model_path = "silero_vad.jit"  # Ensure the file is in the same folder as main.py

# Load the model directly
vad_model = torch.jit.load(model_path)
vad_model.eval()


@app.websocket("/ws/recite")
async def websocket_stream(websocket: WebSocket):
    await websocket.accept()
    print("Client connected for continuous streaming.")

    try:
        # Wait for the initial metadata payload
        metadata_str = await websocket.receive_text()
        metadata = json.loads(metadata_str)
        surah_id = int(metadata["surah_id"])
        ayah_id = int(metadata["ayah_id"])

        # Initialize persistent session tracking for the lifecycle of this stream
        if (
            surah_id in TAJWEED_ONLY_INDEX
            and ayah_id in TAJWEED_ONLY_INDEX[surah_id]
        ):
            word_objects = TAJWEED_ONLY_INDEX[surah_id][ayah_id]["words"]
            session_words = [
                {"text": w["text"], "is_read": False} for w in word_objects
            ]
        else:
            session_words = []
            print(
                f"Warning: Surah {surah_id}, Ayah {ayah_id} not found in index."
            )

        audio_buffer = bytearray()
        silence_frames = 0
        is_speaking = False

        while True:
            # Receive continuous stream of bytes from Flutter
            chunk = await websocket.receive_bytes()
            audio_buffer.extend(chunk)

            # We need at least 512 samples (1024 bytes of 16-bit PCM) to run VAD
            if len(audio_buffer) >= 1024:
                # Convert raw 16-bit PCM bytes to a Float32 Tensor for PyTorch
                audio_np = (
                    np.frombuffer(audio_buffer[-1024:], dtype=np.int16).astype(
                        np.float32
                    )
                    / 32768.0
                )
                audio_tensor = torch.from_numpy(audio_np)

                # Check probability of speech
                speech_prob = vad_model(audio_tensor, 16000).item()

                if speech_prob > 0.5:
                    is_speaking = True
                    silence_frames = 0
                elif is_speaking:
                    silence_frames += 1

                # If they were speaking but have been silent for ~0.13s (approx 4 frames)
                if is_speaking and silence_frames > 4 :
                    print("\n--- 🛑 Pause Detected. Processing Chunk ---")

                    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                        tmp.write(audio_buffer)
                        tmp_path = tmp.name

                    # Evaluate what words were recognized within this single chunk
                    chunk_result = evaluate_audio(surah_id, ayah_id, tmp_path)
                    os.remove(tmp_path)

                    detected_chunk_words = chunk_result["words"]

                    # Merge chunk results into persistent session tracking status
                    for i, word in enumerate(detected_chunk_words):
                        if i < len(session_words) and word["is_read"]:
                            session_words[i]["is_read"] = True

                    print(f"📤 [SERVER] Sending updated states to Flutter: {session_words}")
                    await websocket.send_json(
                        {
                            "words": session_words,
                            "real_text": chunk_result.get("real_text", ""),
                            "expected": chunk_result.get("expected", ""),
                            "predicted": chunk_result.get("predicted", ""),
                            "accuracy": chunk_result.get("accuracy", 0.0),
                        }
                    )

                    # Clear buffer for next phrase block
                    audio_buffer.clear()
                    is_speaking = False
                    silence_frames = 0

    except WebSocketDisconnect:
        print("Client disconnected.")