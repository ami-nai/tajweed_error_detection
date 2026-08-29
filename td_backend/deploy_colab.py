"""
Deploy the tajweed backend on a free Google Colab session and expose it via a
free cloudflared tunnel so that a Flutter release APK on any device can reach it.

HOW TO USE (in a Colab notebook cell):
  1. Upload/sync model assets so the script can find them. Two options:
       A) Put them in your Google Drive, e.g.
            /content/drive/MyDrive/tajweed_models/
                quran_model_final-alif-hamja_correction.pt
                silero_vad.jit
       B) Point the cell at a public/private URL via cloud storage.
  2. Clone this repo (or upload td_backend) into /content.
  3. Run this script:
       !pip install -q cloudflared  # or the cloudflared binary below
       %run deploy_colab.py \
           --repo "https://github.com/ami-nai/tajweed_error_detection.git" \
           --models "/content/drive/MyDrive/tajweed_models" \
           --port 8000
  4. Copy the wss:// URL printed at the end. Build the APK with:
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


def install_cloudflared():
    """Install the cloudflared binary (works on both CPU & any Colab container)."""
    print("Installing cloudflared...", flush=True)
    if shutil.which("cloudflared"):
        return
    cmd = (
        "wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/"
        "cloudflared-linux-amd64 -O /usr/local/bin/cloudflared && "
        "chmod +x /usr/local/bin/cloudflared"
    )
    run(cmd)


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
    print("== Starting cloudflared tunnel (free wss:// URL) ==", flush=True)
    cmd = (
        "nohup cloudflared tunnel --url http://localhost:%d "
        "--no-autoupdate > tunnel.log 2>&1 &" % port
    )
    run(cmd)
    url = None
    for _ in range(120):
        time.sleep(2)
        if os.path.isfile("tunnel.log"):
            text = open("tunnel.log").read()
            if "trycloudflare.com" in text:
                url = text.split("https://")[1].split(".trycloudflare.com")[0]
                url = f"wss://{url}.trycloudflare.com"
                break
            if "ERR" in text and "error" in text.lower():
                print("Tunnel error:", text[-500:], flush=True)
                break
    if url:
        print("\n" + "=" * 70, flush=True)
        print("PUBLIC BACKEND URL:", flush=True)
        print(f"  wss://{url[6:]}/ws/recite", flush=True)
        print("Use for flutter build:", flush=True)
        print(f"  --dart-define=BACKEND_URL={url}", flush=True)
        print("=" * 70, flush=True)
    else:
        print("Could not read tunnel URL. Inspect tunnel.log:", flush=True)
        run("cat tunnel.log", check=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--models", required=True,
                    help="local dir containing the .pt and silero_vad.jit")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    install_cloudflared()
    prepare(args.models)
    serve(args.port)
    tunnel(args.port)


if __name__ == "__main__":
    main()
