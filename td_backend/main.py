import json
import os
import tempfile
import numpy as np
import torch
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from ml_engine import (
    TAJWEED_ONLY_INDEX,
    evaluate_audio,
    get_surah_ayah_ids,
)

app = FastAPI()

# 1. Load the Silero VAD model at startup
print("Loading Silero VAD model locally...")
model_path = "silero_vad.jit"  # Ensure the file is in the same folder as main.py

# Load the model directly
vad_model = torch.jit.load(model_path)
vad_model.eval()

# Number of consecutive silence frames before we consider a phrase/ayah finished.
# Each frame is ~0.03s (1024 samples @16kHz). A larger threshold helps distinguish
# ayah boundaries from short mid-ayah pauses during continuous surah recitation.
PAUSE_FRAME_THRESHOLD = 15  # ~0.45s of silence before segmenting
SILENCE_PROB_THRESHOLD = 0.5


@app.websocket("/ws/recite")
async def websocket_stream(websocket: WebSocket):
    await websocket.accept()
    print("Client connected for continuous streaming.")

    try:
        # Wait for the initial metadata payload
        metadata_str = await websocket.receive_text()
        metadata = json.loads(metadata_str)
        surah_id = int(metadata["surah_id"])

        # Mode detection:
        #  - single-ayah mode: metadata includes "ayah_id"
        #  - surah (sequential) mode: metadata has no "ayah_id"
        is_surah_mode = "ayah_id" not in metadata
        ayah_id = int(metadata["ayah_id"]) if not is_surah_mode else None

        if surah_id not in TAJWEED_ONLY_INDEX:
            print(f"Warning: Surah {surah_id} not found in index.")
            await websocket.close()
            return

        # Build session state
        if is_surah_mode:
            # Sequential: session holds read-state for every ayah in the surah.
            session_ayahs = get_surah_ayah_ids(surah_id)
            session_read = {
                a_id: [
                    {
                        "text": w["text"],
                        "is_read": False,
                        "letters": [],
                    }
                    for w in TAJWEED_ONLY_INDEX[surah_id][a_id]["words"]
                ]
                for a_id in session_ayahs
            }
            ayah_accuracies = {a_id: None for a_id in session_ayahs}
            ayah_wers = {a_id: None for a_id in session_ayahs}
            # Index into session_ayahs for sequential advancement
            ayah_index = 0
            print(f"→ Surah mode: {len(session_ayahs)} ayahs loaded for surah {surah_id}.")
        else:
            session_words = [
                {
                    "text": w["text"],
                    "is_read": False,
                    "letters": [],
                }
                for w in TAJWEED_ONLY_INDEX[surah_id][ayah_id]["words"]
            ] if ayah_id in TAJWEED_ONLY_INDEX[surah_id] else []
            ayah_index = None

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

                if speech_prob > SILENCE_PROB_THRESHOLD:
                    is_speaking = True
                    silence_frames = 0
                elif is_speaking:
                    silence_frames += 1

                # If they were speaking but have been silent long enough (pause threshold)
                if is_speaking and silence_frames > PAUSE_FRAME_THRESHOLD:
                    print("\n--- 🛑 Pause Detected. Processing Chunk ---")

                    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                        tmp.write(audio_buffer)
                        tmp_path = tmp.name

                    if is_surah_mode:
                        # The ayah just read is session_ayahs[ayah_index].  As soon as the
                        # pause is detected, tell the client which ayah to read next WITHOUT
                        # waiting for the (slow) inference result of the finished ayah.
                        is_last = ayah_index + 1 >= len(session_ayahs)
                        next_read = (
                            session_ayahs[ayah_index + 1] if not is_last else None
                        )
                        await websocket.send_json(
                            {
                                "mode": "surah",
                                "advance": True,
                                "read_now": next_read,
                                "finished": False,
                            }
                        )

                        # Evaluate the just-finished ayah and update its words/accuracy.
                        await _handle_surah_pause(
                            websocket, surah_id, session_read, ayah_accuracies,
                            session_ayahs, ayah_index, tmp_path,
                        )
                        ayah_index += 1  # advance to next ayah

                        # Surah complete: notify after the last ayah's result was sent.
                        if is_last:
                            print("🎉 Surah complete. Notifying client.")
                            await websocket.send_json({"mode": "surah", "finished": True})
                            break
                    else:
                        await _handle_single_pause(
                            websocket, surah_id, ayah_id, session_words, tmp_path,
                        )

                    os.remove(tmp_path)

                    # Clear buffer for next phrase block
                    audio_buffer.clear()
                    is_speaking = False
                    silence_frames = 0

    except WebSocketDisconnect:
        print("Client disconnected.")


async def _handle_single_pause(websocket, surah_id, ayah_id, session_words, tmp_path):
    """Process a pause-delimited chunk for single-ayah mode and send the updated state."""
    chunk_result = evaluate_audio(surah_id, ayah_id, tmp_path)

    detected_chunk_words = chunk_result["words"]

    # Merge chunk results into persistent session tracking status
    for i, word in enumerate(detected_chunk_words):
        if i < len(session_words) and word["is_read"]:
            session_words[i]["is_read"] = True
            if word.get("letters"):
                session_words[i]["letters"] = word["letters"]

    print(f"📤 [SERVER] Sending single-ayah states to Flutter: {session_words}")
    await websocket.send_json(
        {
            "mode": "single",
            "ayah_id": ayah_id,
            "words": session_words,
            "real_text": chunk_result.get("real_text", ""),
            "expected": chunk_result.get("expected", ""),
            "predicted": chunk_result.get("predicted", ""),
            "accuracy": chunk_result.get("accuracy", 0.0),
            "wer": chunk_result.get("wer", 0.0),
            "diff": chunk_result.get("diff", []),
        }
    )


async def _handle_surah_pause(websocket, surah_id, session_read, ayah_accuracies,
                        session_ayahs, ayah_index, tmp_path):
    """Process a pause-delimited chunk for surah (sequential) mode.

    The chunk is attributed to the next expected ayah (session_ayahs[ayah_index]).
    Marks its words read and sends the full surah state + running average.
    """
    if ayah_index >= len(session_ayahs):
        return

    target_ayah = session_ayahs[ayah_index]
    chunk_result = evaluate_audio(surah_id, target_ayah, tmp_path)

    detected_chunk_words = chunk_result["words"]
    ayah_words = session_read[target_ayah]
    for i, word in enumerate(detected_chunk_words):
        if i < len(ayah_words) and word["is_read"]:
            ayah_words[i]["is_read"] = True
            if word.get("letters"):
                ayah_words[i]["letters"] = word["letters"]

    ayah_accuracies[target_ayah] = chunk_result.get("accuracy", 0.0)
    ayah_wers[target_ayah] = chunk_result.get("wer", 0.0)

    # Running surah average over recognized ayahs only
    scored = [acc for acc in ayah_accuracies.values() if acc is not None]
    surah_average = round(sum(scored) / len(scored), 2) if scored else 0.0
    scored_wer = [w for w in ayah_wers.values() if w is not None]
    surah_wer = round(sum(scored_wer) / len(scored_wer), 2) if scored_wer else 0.0

    # Flatten words into a list of ayah word-lists in order for the client
    words_by_ayah = [
        session_read[a_id] for a_id in session_ayahs
    ]

    print(f"📤 [SERVER] Surah mode: ayah {target_ayah} updated. Avg={surah_average}%")
    await websocket.send_json(
        {
            "mode": "surah",
            "current_ayah": target_ayah,
            "next_ayah": session_ayahs[ayah_index + 1] if ayah_index + 1 < len(session_ayahs) else None,
            "ayah_order": session_ayahs,
            "words": words_by_ayah,
            "ayah_accuracies": ayah_accuracies,
            "surah_average": surah_average,
            "ayah_wers": ayah_wers,
            "surah_wer": surah_wer,
            "diff": chunk_result.get("diff", []),
            "finished": False,
        }
    )