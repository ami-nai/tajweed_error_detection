# Deployment: sharing the app with teammates (free hosting)

The app has two parts and a live WebSocket connection between them:

```
Android app (Flutter)  --wss://-->  FastAPI backend (uvicorn)
   records mic + streams PCM          VAD + Wav2Vec2 CTC model
   renders words / accuracy           returns JSON results
```

The backend runs the heavy ML model (torch + 361MB `.pt`), so it cannot run on a
phone. We host it for free on **Google Colab** and expose it with a free
**ngrok tunnel** to get a public `wss://` URL. Teammates just install a
release APK that points at that URL.

## 0. Set up ngrok once (no credit card)

1. Sign up free at https://ngrok.com (email only).
2. Copy your **auth token** from the ngrok dashboard (looks like `2abcDEFghI...`).
3. In Colab, open the left sidebar, click the **key icon (Secrets)**, add:
   - Name: `NGROK_AUTHTOKEN`
   - Value: `<your token>`
   - Toggle "Notebook access" on.

## 1. Host the backend on Colab (by you)

1. Create a new Colab notebook (https://colab.research.google.com).
2. Runtime menu → *Change runtime type* → **CPU** (free). No GPU needed.
3. Mount Drive and put the two model files in it:
   ```
   /content/drive/MyDrive/tajweed_models/
       quran_model_final-alif-hamja_correction.pt
       silero_vad.jit
   ```
   (These are gitignored and too big for GitHub — you have them locally in
   `td_backend/`.)
4. In a cell run:
   ```python
   from google.colab import drive
   drive.mount('/content/drive')
   ```
5. Run the deploy script (clones the repo, installs CPU deps, starts the
   backend, starts the ngrok tunnel):
   ```python
   !wget -q https://raw.githubusercontent.com/ami-nai/tajweed_error_detection/realtime/td_backend/deploy_colab.py -O deploy_colab.py
   %run deploy_colab.py --models "/content/drive/MyDrive/tajweed_models"
   ```
6. Wait 2-4 minutes (model load). At the end the cell prints a
   **PUBLIC BACKEND URL** like `wss://something.ngrok-free.app`.
7. Keep this tab open while teammates use the app.

## 2. Build the APK pointing at that URL (by you)

From `td_frontend/`:

```bash
flutter build apk --release --dart-define=BACKEND_URL=wss://YOUR_TUNNEL_URL
```

Output: `build/app/outputs/flutter-apk/app-release.apk`.

Share that `.apk` with teammates (Drive/Firebase link). They install it and
allow the microphone.

## 3. Verify it works before sharing

Quick WebSocket smoke test from anywhere (your machine or Colab):

```python
import asyncio, json, websockets
async def t():
    async with websockets.connect("wss://YOUR_TUNNEL_URL/ws/recite") as ws:
        await ws.send(json.dumps({"surah_id": 112, "ayah_id": 1}))
        await ws.send(b"\x00" * 2048)   # a short audio chunk
        print(await ws.recv())
asyncio.run(t())
```
You should get back a JSON result (not a disconnect / 1012 / 403).

## Caveats (tell teammates)

- Free Colab **sleeps after ~90 min idle** and closes after a few hours. When
  it does, the tunnel URL dies. Whoever needs it re-runs the notebook and shares
  the new URL (or you rebuild the APK).
- CPU inference is **slower** than your local machine — results come back a
  second or two later.
- Only **surah 111-114** are supported (the model index).

## Changing the backend URL without rebuilding

The URL is read from `--dart-define=BACKEND_URL`. Default (dev) is
`ws://192.168.1.113:8000` (see `recitation_provider.dart`). To point at a new
host, rebuild with the new `--dart-define`. If the backend moves to a new
tunnel, only the APK build command changes.
