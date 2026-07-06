from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import json
import torch
import numpy as np
from ml_engine import evaluate_audio
import tempfile
import os

app = FastAPI()

# 1. Load the Silero VAD model at startup
print("Loading Silero VAD model locally...")
model_path = "silero_vad.jit" # Ensure the file is in the same folder as main.py

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
        surah_id = int(metadata['surah_id'])
        ayah_id = int(metadata['ayah_id'])
        
        audio_buffer = bytearray()
        silence_frames = 0
        is_speaking = False
        
        while True:
            # 2. Receive continuous stream of bytes from Flutter
            chunk = await websocket.receive_bytes()
            audio_buffer.extend(chunk)
            
            # We need at least 512 samples (1024 bytes of 16-bit PCM) to run VAD
            if len(audio_buffer) >= 1024:
                # Convert raw 16-bit PCM bytes to a Float32 Tensor for PyTorch
                audio_np = np.frombuffer(audio_buffer[-1024:], dtype=np.int16).astype(np.float32) / 32768.0
                audio_tensor = torch.from_numpy(audio_np)
                
                # 3. Check probability of speech
                speech_prob = vad_model(audio_tensor, 16000).item()
                
                if speech_prob > 0.5:
                    is_speaking = True
                    silence_frames = 0
                elif is_speaking:
                    silence_frames += 1
                
                # 4. If they were speaking but have been silent for ~1 second (approx 30 frames at this chunk size)
                if is_speaking and silence_frames > 30:
                    print("Pause detected. Processing chunk...")
                    
                    # Save the accumulated buffer to evaluate
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                        tmp.write(audio_buffer)
                        tmp_path = tmp.name
                        
                    # Evaluate the chunk
                    raw_text, expected, predicted, accuracy_str = evaluate_audio(surah_id, ayah_id, tmp_path)
                    os.remove(tmp_path)
                    
                    # Determine status
                    acc_float = float(accuracy_str.strip('%'))
                    status = "advance" if acc_float >= 80.0 else ("retry" if acc_float >= 60.0 else "error")
                    
                    # Send result back to Flutter in real-time
                    await websocket.send_json({
                        "status": status,
                        "accuracy": accuracy_str,
                        "raw_text": raw_text,
                        "expected_phonemes": expected,
                        "predicted_phonemes": predicted
                    })
                    
                    # Reset variables for the next phrase
                    audio_buffer.clear()
                    is_speaking = False
                    silence_frames = 0
                    
    except WebSocketDisconnect:
        print("Client disconnected.")