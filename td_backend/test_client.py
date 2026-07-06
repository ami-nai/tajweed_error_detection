import asyncio
import websockets
import json

async def test_backend():
    uri = "ws://127.0.0.1:8000/ws/recite"
    
    # Use a Surah/Ayah you know is in your Tajweed index (e.g., Al-Ikhlas: Surah 112, Ayah 3)
    metadata = {
        "surah_id": 112,
        "ayah_id": 3
    }
    
    # Path to a real .wav file on your computer for testing
    audio_file_path = "sample_recitation.wav" 
    
    async with websockets.connect(uri) as websocket:
        print("Connected to server.")
        
        # 1. Send Metadata
        await websocket.send(json.dumps(metadata))
        print(f"Sent metadata: {metadata}")
        
        # 2. Send Audio Bytes
        with open(audio_file_path, "rb") as f:
            audio_bytes = f.read()
            await websocket.send(audio_bytes)
        print("Sent audio bytes.")
        
        # 3. Receive Result
        response = await websocket.recv()
        print("\n--- Server Response ---")
        print(json.dumps(json.loads(response), indent=2, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(test_backend())