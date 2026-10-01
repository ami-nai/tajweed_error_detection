"""
Deploy the streaming tajweed backend on Kaggle (GPU) and expose it via a
free ngrok tunnel so that a Flutter release APK on any device can reach it.

KAGGLE SETUP (do once):
  1. New Kaggle Notebook with GPU ON (Settings -> Accelerator -> GPU T4),
     Internet ON.
  2. Add secret: Add-ons -> Secrets -> key `NGROK_TOKEN`, value = your
     ngrok authtoken (free at https://ngrok.com, email only). Enable
     'Notebook access'.
  3. Attach model: Add Input -> Models -> `ami0nai/quran-model` (has
     `streaming_quran_wav2vec (1).pt` + `vocab (2).json`).
  4. In a cell:
        !git clone --depth 1 --branch stream https://github.com/ami-nai/tajweed_error_detection.git _repo
        %run _repo/tajweed_detect_app/td_backend/deploy_colab.py --kaggle-model
     Or with Drive files:
        %run deploy_colab.py --models "/content/drive/MyDrive/tajweed_models"
  5. The notebook prints a PUBLIC wss URL. Build the APK locally with:
        flutter build apk --release --dart-define=BACKEND_URL=<wss-url>
     Stop ngrok on the laptop first (free accounts allow one agent session).

NOTES / CAVEATS
  * Kaggle sessions die after ~9-12h or 30 min idle; the URL changes on re-run.
  * Streaming model needs GPU: ~0.05 s / 3 s window on T4 vs ~0.78 s on CPU
    (too slow for the 0.5 s tick). Do not run inference on CPU-only Colab.
  * Base HF weights `jonatasgrosman/wav2vec2-large-xlsr-53-arabic` download
    once (~1.2 GB) and are cached; keep Internet ON for the first run.
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

REPO = "https://github.com/ami-nai/tajweed_error_detection.git"
BRANCH = "stream"

# Files that are gitignored and MUST be supplied at runtime (Kaggle model
# attachment or --models dir). Spaced Kaggle names are accepted and renamed
# to the canonical names ml_engine.py expects.
NEEDED_MODELS = [
    "streaming_quran_wav2vec.pt",
    "vocab.json",
]
# Aliases found in the wild (Kaggle upload kept the "(1)"/"(2)" suffixes).
MODEL_ALIASES = {
    "streaming_quran_wav2vec.pt": [
        "streaming_quran_wav2vec (1).pt",
        "streaming_quran_wav2vec.pt",
    ],
    "vocab.json": ["vocab (2).json", "vocab.json"],
}


def run(cmd, check=True):
    print(f"\n$ {cmd}", flush=True)
    r = subprocess.run(cmd, shell=True, check=check)
    if r.returncode != 0 and check:
        sys.exit(r.returncode)
    return r


def _find_candidate(dirs, names):
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for root, _, files in os.walk(d):
            for n in names:
                if n in files:
                    return os.path.join(root, n)
    return None


def prepare(models_dir=None, use_kaggle_model=False):
    print("== Preparing repository & models ==", flush=True)
    if not os.path.isdir("td_backend"):
        run(f"git clone --depth 1 --branch {BRANCH} {REPO} _dep_repo")
        # Repo root contains tajweed_detect_app/; support both layouts.
        inner = os.path.join("_dep_repo", "tajweed_detect_app", "td_backend")
        outer = os.path.join("_dep_repo", "td_backend")
        src = inner if os.path.isdir(inner) else outer
        shutil.move(src, "td_backend")
        shutil.rmtree("_dep_repo", ignore_errors=True)
    os.chdir("td_backend")

    search_dirs = []
    if models_dir:
        search_dirs.append(models_dir)
    if use_kaggle_model:
        # Kaggle model attachment lands under /kaggle/input/models/...
        search_dirs += ["/kaggle/input", "/kaggle/working"]
        try:
            import kagglehub
            try:
                kagglehub.login()
            except Exception:
                pass
            try:
                p = kagglehub.model_download("ami0nai/quran-model/pytorch/default/1")
                search_dirs.append(p)
                print(f"kagglehub model dir: {p}", flush=True)
            except Exception as exc:
                print(f"kagglehub download skipped ({exc}); using /kaggle/input scan", flush=True)
        except Exception as exc:
            print(f"kagglehub unavailable ({exc}); using /kaggle/input scan", flush=True)

    missing = [m for m in NEEDED_MODELS if not os.path.isfile(m)]
    if missing:
        print(f"Missing model files: {missing}", flush=True)
        for m in list(missing):
            src = _find_candidate(search_dirs, MODEL_ALIASES.get(m, [m]))
            if src:
                shutil.copy(src, m)
                print(f"  copied {src} -> {m}", flush=True)
                missing.remove(m)
    if missing:
        print(
            "ERROR: could not locate the model files. Attach ami0nai/quran-model "
            "and re-run with --kaggle-model, or put streaming_quran_wav2vec.pt + "
            "vocab.json in --models and re-run.",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)
    print("Models OK.", flush=True)


def serve(port):
    print(f"== Installing Python deps (GPU Kaggle) ==", flush=True)
    run("pip install -q -r requirements-colab.txt")
    # Prefetch the HF base once so the first tick doesn't pay the download.
    run("python -q -c \"from transformers import AutoFeatureExtractor, AutoModel; "
        "AutoFeatureExtractor.from_pretrained('jonatasgrosman/wav2vec2-large-xlsr-53-arabic'); "
        "AutoModel.from_pretrained('jonatasgrosman/wav2vec2-large-xlsr-53-arabic')\"")

    print(f"== Starting uvicorn on port {port} ==", flush=True)
    cmd = ("nohup uvicorn main:app --host 0.0.0.0 --port %d "
           "> server.log 2>&1 &") % port
    run(cmd)
    print("Waiting for server to start (GPU load takes 1-3 min)...", flush=True)
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
    ap.add_argument("--models", default=None,
                    help="local dir containing streaming_quran_wav2vec.pt + vocab.json")
    ap.add_argument("--kaggle-model", action="store_true",
                    help="pull ami0nai/quran-model from the Kaggle attachment / kagglehub")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    run("pip install -q pyngrok")
    prepare(args.models, use_kaggle_model=args.kaggle_model)
    serve(args.port)
    tunnel(args.port)


if __name__ == "__main__":
    main()
