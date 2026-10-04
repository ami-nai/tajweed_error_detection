"""Thesis benchmark: latency / RTF / memory / tick cost for the streaming backend.

NOT imported by the server (test-only rig, like test_ws_smoke.py).

Run on Kaggle GPU (established deploy drill, model files in place):
    python bench_thesis.py --wav /kaggle/input/datasets/ami0nai/my-recording/my_recording.wav \\
        --span-wav /kaggle/input/datasets/ami0nai/111-2-last-wav/111_2-last.wav \\
        --out bench_results.json

CPU reference row (same script, device switches at import):
    CUDA_VISIBLE_DEVICES="" python bench_thesis.py --wav <wav> --out bench_cpu.json --n 10

Local check without any model load:
    python bench_thesis.py --dry-run

Method (thesis honesty): warmed-up forwards (5), GPU-synced timing, real
recitation bytes (never noise), n=50 default; RTF = infer_time / audio_dur;
tick beats driven through a real Session.tick() harness; network RTT is NOT
in this script (measured app-side via the MIC SEND / INTERIM RECV log lines).
"""

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time

WINDOWS_BYTES = 96000  # 3 s @16 kHz mono int16 == LIVE_WINDOW_BYTES
WARMUP = 5


def _sync():
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.synchronize()
    except Exception:
        pass


def _stats(xs):
    s = sorted(xs)
    return {
        "n": len(s),
        "mean": round(statistics.mean(s), 4),
        "p50": round(statistics.median(s), 4),
        "p95": round(s[max(0, int(len(s) * 0.95) - 1)], 4),
        "max": round(max(s), 4),
        "min": round(min(s), 4),
    }


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _cpu_name():
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or "unknown"


def _ram_mb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    return round(int(line.split()[1]) / 1024)
    except Exception:
        pass
    return None


def _nvidia_smi():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return None


def _git_commit():
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def _fixture_info(path):
    info = {"path": path, "exists": os.path.isfile(path)}
    if not info["exists"]:
        return info
    info["bytes"] = os.path.getsize(path)
    info["sha256"] = _sha256(path)
    try:
        import soundfile as sf
        i = sf.info(path)
        info.update({"sr": i.samplerate, "channels": i.channels,
                     "dur_s": round(i.frames / i.samplerate, 2)})
    except Exception as exc:
        info["audio_error"] = str(exc)
    return info


def collect_specs(wav, span_wav):
    import torch
    import ml_engine
    specs = {
        "device": str(ml_engine.DEVICE),
        "commit": _git_commit(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu": _cpu_name(),
        "ram_mb": _ram_mb(),
        "nvidia_smi": _nvidia_smi(),
        "cuda_available": torch.cuda.is_available(),
    }
    try:
        import transformers, torchaudio
        specs["torch"] = torch.__version__
        specs["transformers"] = transformers.__version__
        specs["torchaudio"] = torchaudio.__version__
        specs["cuda_compiled"] = torch.version.cuda
    except Exception as exc:
        specs["versions_error"] = str(exc)
    specs["model_id"] = ml_engine.MODEL_ID
    specs["vocab_classes"] = ml_engine.V_SIZE
    specs["checkpoint"] = {"path": ml_engine.MODEL_PATH,
                           "bytes": (os.path.getsize(ml_engine.MODEL_PATH)
                                     if os.path.isfile(ml_engine.MODEL_PATH) else None)}
    specs["fixture_wav"] = _fixture_info(wav)
    specs["fixture_span_wav"] = _fixture_info(span_wav)
    return specs


def load_model_timed():
    import time as _t
    import torch
    import ml_engine
    t0 = _t.time()
    model, _ext = ml_engine._ensure_model()
    _sync()
    dt = _t.time() - t0
    n_params = sum(p.numel() for p in model.parameters())
    return {
        "load_wall_s": round(dt, 2),
        "params": n_params,
        "params_MB_fp32": round(n_params * 4 / 1e6, 1),
    }, model


def _gpu_mem_mb():
    try:
        import torch
        if torch.cuda.is_available():
            return {"allocated": round(torch.cuda.memory_allocated() / 1e6, 1),
                    "reserved": round(torch.cuda.memory_reserved() / 1e6, 1)}
    except Exception:
        pass
    return None


def _rss_mb():
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS"):
                    return round(int(line.split()[1]) / 1024)
    except Exception:
        pass
    return None


def slice_windows(pcm_bytes, n_win=8):
    """Evenly spaced non-overlapping 3 s windows; fixture must be >= 3 s."""
    if len(pcm_bytes) < WINDOWS_BYTES:
        raise ValueError(f"fixture too short: {len(pcm_bytes)} bytes")
    step = max(1, (len(pcm_bytes) - WINDOWS_BYTES) // max(1, n_win))
    wins, off = [], 0
    while len(wins) < n_win and off + WINDOWS_BYTES <= len(pcm_bytes):
        wins.append(pcm_bytes[off:off + WINDOWS_BYTES])
        off += step
    return wins or [pcm_bytes[:WINDOWS_BYTES]]


def bench_forward(windows, n, label):
    import ml_engine
    for w in windows[:WARMUP]:
        ml_engine._infer_pred_ids(w)
    _sync()
    ts = []
    k = 0
    for _ in range(n):
        w = windows[k % len(windows)]
        k += 1
        t0 = time.time()
        ml_engine._infer_pred_ids(w)
        _sync()
        ts.append(time.time() - t0)
    audio_dur = len(windows[0]) / 2 / 16000
    out = _stats(ts)
    out.update({"case": label, "audio_s": round(audio_dur, 2),
                "rtf": round(statistics.mean(ts) / audio_dur, 4)})
    return out


def bench_full_file(pcm_bytes, label):
    import ml_engine
    ml_engine._infer_pred_ids(pcm_bytes[:WINDOWS_BYTES])
    _sync()
    t0 = time.time()
    ml_engine._infer_pred_ids(pcm_bytes)
    _sync()
    dt = time.time() - t0
    audio_dur = len(pcm_bytes) / 2 / 16000
    return {"case": label, "audio_s": round(audio_dur, 2),
            "infer_s": round(dt, 3), "rtf": round(dt / audio_dur, 4)}


class _FakeWs:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


def bench_tick_beats(pcm_bytes, n_beats=6):
    """Real Session.tick() cost per beat (gate + window decode + trie +
    interim) on live-shaped audio deltas. Resolves 111:1 first so the trie
    path is exercised like production."""
    import asyncio
    import main
    import ml_engine
    s = main.Session(_FakeWs(), "open_mic", 111, None, None, {}, None)
    s.resolved = (111, 1)
    s.surah_id, s.ayah_id = 111, 1
    s._init_guided()
    chunk = 16000  # 0.5 s deltas, speech-like real bytes
    off, ts = 0, []
    for _ in range(n_beats):
        s.audio.extend(pcm_bytes[off:off + chunk])
        off = (off + chunk) % max(1, len(pcm_bytes) - chunk)
        t0 = time.time()
        asyncio.run(s.tick())
        ts.append(time.time() - t0)
    out = _stats(ts)
    out.update({"case": f"tick-beat ({n_beats} beats, resolved 111:1)",
                "note": "includes 0.5 s-tick quantization effects upstream; "
                        "network RTT measured app-side, not here"})
    return out


def render_markdown(res):
    L = ["# Thesis benchmark", ""]
    sp = res["specs"]
    L += ["## System", "",
          f"- device: `{sp['device']}` | commit: `{sp.get('commit')}`",
          f"- platform: {sp.get('platform')} | python {sp.get('python')}",
          f"- cpu: {sp.get('cpu')} | ram: {sp.get('ram_mb')} MB",
          f"- nvidia-smi: `{sp.get('nvidia_smi')}`",
          f"- torch {sp.get('torch')} / transformers {sp.get('transformers')} / "
          f"torchaudio {sp.get('torchaudio')} (cuda {sp.get('cuda_compiled')})",
          f"- model: `{sp.get('model_id')}` | vocab {sp.get('vocab_classes')} classes",
          ""]
    if "model" in res:
        m, mem = res["model"], res.get("memory", {})
        L += ["## Model & memory", "",
              f"- checkpoint: `{sp['checkpoint']['path']}` "
              f"({(sp['checkpoint']['bytes'] or 0) / 1e9:.2f} GB on disk)",
              f"- params: {m['params']} (~{m['params_MB_fp32']} MB fp32) | "
              f"load wall: {m['load_wall_s']} s",
              f"- host RSS MB: idle {mem.get('rss_idle')} / loaded {mem.get('rss_loaded')}",
              f"- GPU VRAM MB (alloc/reserved): tick {mem.get('vram_tick')} / "
              f"span {mem.get('vram_span')}",
              ""]
    if "latency" in res:
        L += ["## Latency / RTF (warmed, GPU-synced)", "",
              "| case | audio (s) | mean (s) | p50 | p95 | max | RTF |",
              "|---|---|---|---|---|---|---|"]
        for c in res["latency"]:
            L.append(f"| {c['case']} | {c.get('audio_s', '-')} | {c.get('mean', c.get('infer_s'))} "
                     f"| {c.get('p50', '-')} | {c.get('p95', '-')} | {c.get('max', '-')} | {c['rtf']} |")
        L += [""]
    if "tick" in res:
        t = res["tick"]
        L += [f"## Tick beat: mean {t['mean']} s, p95 {t['p95']} s, max {t['max']} s "
              f"(n={t['n']})", "", f"_{t['note']}_", ""]
    fx = [sp.get("fixture_wav", {}), sp.get("fixture_span_wav", {})]
    L += ["## Fixtures", ""]
    for f in fx:
        if not f.get("exists"):
            L.append(f"- `{f.get('path')}`: not present (dry-run or missing file)")
            continue
        L.append(f"- `{f.get('path')}`: {f.get('bytes')} B, sha256 `{str(f.get('sha256'))[:16]}…"
                 + (f", {f.get('sr')} Hz x{f.get('channels')}, {f.get('dur_s')} s"
                    if f.get('sr') else f" ({f.get('audio_error', 'unreadable')})"))
    return "\n".join(L)


def main_cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", default=None)
    ap.add_argument("--span-wav", default=None)
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--out", default="bench_results.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    span_wav = args.span_wav or args.wav
    if not args.dry_run and not args.wav:
        ap.error("--wav is required (unless --dry-run)")
    if args.wav and not os.path.isfile(args.wav):
        ap.error(f"wav not found: {args.wav}")
    if span_wav and not os.path.isfile(span_wav):
        ap.error(f"span-wav not found: {span_wav}")

    res = {"specs": collect_specs(args.wav or "n/a", span_wav or "n/a")}
    if args.dry_run:
        res["mode"] = "dry-run (no model load, no forwards)"
        print(render_markdown(res))
        with open(args.out, "w") as f:
            json.dump(res, f, indent=2)
        print(f"\nwrote {args.out}")
        return

    import ml_engine
    model_info, _model = load_model_timed()
    res["model"] = model_info
    mem = {"rss_idle": _rss_mb()}
    mem["vram_idle"] = _gpu_mem_mb()

    pcm = ml_engine._load_audio_bytes(args.wav)
    windows = slice_windows(pcm)
    lat = [bench_forward(windows, args.n, "3 s tick window (n=%d)" % args.n)]
    lat.append(bench_full_file(pcm, "full file 111:1 (%.1f s)" % (len(pcm) / 32000)))
    mem["vram_tick"] = _gpu_mem_mb()
    if span_wav != args.wav:
        span_pcm = ml_engine._load_audio_bytes(span_wav)
        lat.append(bench_full_file(span_pcm, "span file 111:2-5 (%.1f s)" % (len(span_pcm) / 32000)))
        mem["vram_span"] = _gpu_mem_mb()
    else:
        mem["vram_span"] = _gpu_mem_mb()
    res["latency"] = lat
    res["tick"] = bench_tick_beats(pcm)
    mem["rss_loaded"] = _rss_mb()
    res["memory"] = mem

    print(render_markdown(res))
    with open(args.out, "w") as f:
        json.dump(res, f, indent=2)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main_cli()
