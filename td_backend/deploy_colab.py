"""
Deploy the tajweed backend on a free Google Colab session and expose it via a
free ngrok tunnel so that a Flutter release APK on any device can reach it.

SETUP (do this once, ~2 min, no credit card):
  1. Create a free ngrok account at https://ngrok.com (email only).
  2. Copy your auth token from the ngrok dashboard (it looks like
     "2abcDEFghI...").
  3. In Colab add it as a secret (key icon in the left sidebar):
       Secret name:  NGROK_AUTHTOKEN
       Value:        <paste your token>

HOW TO USE (in a Colab notebook cell):
  1. Put the model files in Google Drive, e.g.
       /content/drive/MyDrive/tajweed_models/
           quran_model_final-alif-hamja_correction.pt
           silero_vad.jit
  2. Mount Drive:
       from google.colab import drive; drive.mount('/content/drive')
  3. Run this script:
       !wget -q https://raw.githubusercontent.com/ami-nai/tajweed_error_detection/realtime/td_backend/deploy_colab.py -O deploy_colab.py
       %run deploy_colab.py --models "/content/drive/MyDrive/tajweed_models"
  4. The notebook prints a clean PUBLIC URL. Build the APK locally with:
       flutter build apk --release --dart-define=BACKEND_URL=<wss-url>

NOTES / CAVEATS
  * Colab free sessions sleep after ~90 min of inactivity or close after a
    few hours; when that happens the tunnel dies and the URL changes. Re-run
    the notebook to bring it back.
  * This backend runs on CPU (2 vCPUs) -> slower than your local machine.
  * Only surah 111-114 are in the model index.
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

REPO = "https://github.com/ami-nai/tajweed_error_detection.git"

# Files that are gitignored and MUST be supplied by the user (from Drive/cloud)
NEEDED_MODELS = [
    "quran_model_final-alif-hamja_correction.pt",
    "silero_vad.jit",
]


def run(cmd, check=True):
    print(f"\n$ {cmd}", flush=True)
    r = subprocess.run(cmd, shell=True, check=check)
    if r.returncode != 0 and check:
        sys.exit(r.returncode)
    return r


def prepare(models_dir):
    print("== Preparing repository & models ==", flush=True)
    if not os.path.isdir("td_backend"):
        run(f"git clone --depth 1 --branch realtime {REPO} _dep_repo")
        shutil.move(os.path.join("_dep_repo", "td_backend"), "td_backend")
        shutil.rmtree("_dep_repo", ignore_errors=True)
    os.chdir("td_backend")

    missing = [m for m in NEEDED_MODELS if not os.path.isfile(m)]
    if missing:
        print(f"Missing model files: {missing}", flush=True)
        print(f"Looking in models_dir: {models_dir}", flush=True)
        for m in list(missing):
            src = os.path.join(models_dir, m)
            if os.path.isfile(src):
                shutil.copy(src, m)
                print(f"  copied {m}", flush=True)
                missing.remove(m)
    if missing:
        print(
            "ERROR: could not locate the model files. Put them in --models and "
            "re-run.",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)
    print("Models OK.", flush=True)


def serve(port):
    print(f"== Installing Python deps (CPU) ==", flush=True)
    run("pip install -q -r requirements-colab.txt")

    print(f"== Starting uvicorn on port {port} ==", flush=True)
    cmd = ("nohup uvicorn main:app --host 0.0.0.0 --port %d "
           "> server.log 2>&1 &") % port
    run(cmd)
    print("Waiting for server to start (model load takes 1-3 min)...", flush=True)
    for _ in range(180):
        time.sleep(2)
        if "Uvicorn running" in open("server.log").read():
            print("Server is UP.", flush=True)
            return
    print("WARNING: server may still be loading. Check server.log", flush=True)


def tunnel(port):
    print("== Starting ngrok tunnel (free wss:// URL) ==", flush=True)

    token = os.environ.get("NGROK_AUTHTOKEN") or os.environ.get("NGROK_TOKEN")
    if not token:
        print(
            "\nERROR: NGROK_AUTHTOKEN secret is not set.\n"
            "  1. Sign up free at https://ngrok.com (no credit card).\n"
            "  2. Copy your auth token from the dashboard.\n"
            "  3. In Colab: left sidebar -> key icon (Secrets) ->\n"
            "     name `NGROK_AUTHTOKEN`, paste the token, enable\n"
            "     'Notebook access'.\n"
            "  4. Re-run this cell.",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)

    ngrok = None
    try:
        from pyngrok import ngrok as _ngrok
        ngrok = _ngrok
    except Exception:
        run("pip install -q pyngrok")
        from pyngrok import ngrok as _ngrok
        ngrok = _ngrok

    ngrok.set_auth_token(token)
    tunnel_obj = ngrok.connect(port, bind_tls=True)
    url = tunnel_obj.public_url  # e.g. https://<host>.ngrok-free.app

    if not url:
        print("Could not read ngrok URL.", flush=True)
        ngrok.get_tunnels()
        sys.exit(1)

    host = url.replace("https://", "", 1)
    ws_url = f"wss://{host}"
    print("\n" + "=" * 70, flush=True)
    print("PUBLIC BACKEND URL:", flush=True)
    print(f"  {ws_url}/ws/recite", flush=True)
    print("Use for flutter build (on your machine):", flush=True)
    print(f"  flutter build apk --release --dart-define=BACKEND_URL={ws_url}", flush=True)
    print("=" * 70, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--models", required=True,
                    help="local dir containing the .pt and silero_vad.jit")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    run("pip install -q pyngrok")
    prepare(args.models)
    serve(args.port)
    tunnel(args.port)


if __name__ == "__main__":
    main()
