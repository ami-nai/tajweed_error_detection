# Thesis Measurements — Streaming Tajweed Backend (Edge Performance)

> Accuracy/PER content is intentionally excluded here; it belongs to the
> model training/testing chapter. This file covers edge performance only:
> backend specification, memory, inference latency/RTF, and on-device
> round-trip. Every cell traces to a recorded run (commit `bbb64dc`).

## Session & device

| Item | Value |
|---|---|
| Device | Redmi 12C, Android 14 |
| Network | Home Wi-Fi via ngrok free relay (tunnel variance reported as range) |
| Backend commit | `bbb64dc` (branch `stream`) |
| App build | Current `stream` (with MIC SEND / INTERIM RECV logging + Measurements screen) |
| Trial | Open mic, Surah 107, 69 measured beats |

## T1 — Backend specification (Kaggle)

| Item | Value |
|---|---|
| Accelerator | Tesla T4, 15360 MiB VRAM |
| CPU / RAM | Intel Xeon 2.00 GHz / 32 GB |
| Software | Python 3.12, torch 2.10+cu128, transformers 5.0, torchaudio 2.10+cu128, CUDA 12.8 |
| Model | `jonatasgrosman/wav2vec2-large-xlsr-53-arabic` + phoneme head, 315,479,720 params (~1261.9 MB fp32) |
| Vocabulary | 40 classes (`<pad>`, `<blank>`, 38 sorted phoneme tokens) |
| Tick contract | 3 s trailing window (`LIVE_WINDOW_BYTES = 96000`), 0.5 s heartbeat (`EVAL_INTERVAL_SEC`), no trailing pad |

## T2 — Memory footprint

| Resource | Measured |
|---|---|
| Checkpoint on disk | 1.26 GB (`streaming_quran_wav2vec.pt`) + ~1.26 GB first-boot HF base cache |
| Host RSS | 992 MB (index only) → 1684 MB (model loaded) |
| GPU VRAM allocated | 1271.5 MB steady (tick and span forwards) |
| GPU VRAM reserved | 2.7–3.1 GB (allocator headroom — quote *allocated* as model cost) |
| Model load wall time | 7.02 s (GPU), 4.66 s (CPU) |

## T3 — Inference latency / RTF (warmed, GPU-synced, real recitation bytes)

| Case | Audio (s) | Mean (s) | p50 | p95 | Max | RTF |
|---|---|---|---|---|---|---|
| 3 s tick window, GPU (n=50) | 3.0 | 0.0476 | 0.0477 | 0.0490 | 0.0494 | 0.016 |
| Full file 111:1, GPU (7.68 s) | 7.68 | 0.096 | – | – | – | 0.013 |
| Span file 111:2–5, GPU (22.4 s) | 22.4 | 0.314 | – | – | – | 0.014 |
| 3 s tick window, CPU (n=10) | 3.0 | 0.777 | 0.778 | 0.792 | 0.806 | 0.259 |
| Tick beat all-in, GPU (n=6) | – | 0.0772 | – | 0.0982 | 0.145 | – |
| Tick beat all-in, CPU (n=6) | – | 0.7065 | – | 0.9105 | 1.4492 | – |

Reading: GPU holds ~60× real-time with flat length scaling; the CPU misses
the 0.5 s tick cadence on every beat (mean 0.71 s > 0.5 s, backlog diverges),
which is the quantitative justification for GPU hosting. Window-RTF < 1 is
necessary but not sufficient — beat-cost vs cadence is the binding constraint.

## T4 — Edge round-trip (Redmi 12C, Wi-Fi, n=69 beats)

Mic chunk send → interim received, same-device clock (no sync needed).
Includes 0.5 s tick quantization + decode + network relay + render.

| Statistic | ms |
|---|---|
| Last | 579 |
| Median | **609** |
| Min | 478 |
| Max | 751 |
| Beats | 69 |

Decomposition with bench anchors: server beat ≤ 98 ms (p95) ⇒ typical
remainder ≈ 530 ms = tick quantization (mean 250 of uniform 0–500) +
network relay + render, i.e. **network+render ≈ 280 ms typical** on this
path. Tunnel variance is reported as the measured 478–751 ms range, never
a point estimate. The server contributes under one-sixth of the median —
the round-trip is network/quantization-bound, not inference-bound.

## Method notes (reproducibility)

- `td_backend/bench_thesis.py` (`--wav`, `--span-wav`, `--out`; `--dry-run`
  for import-safety; CPU row via `CUDA_VISIBLE_DEVICES=""`), fixtures hashed
  (`my_recording.wav` 7.68 s, `111_2-last.wav` 22.4 s, both 48 kHz stereo).
- Round-trip: app-side `MIC SEND` (seq/ms/cum-bytes) × interim `audio_bytes`
  consumer offset, `T_recv − T_send` per beat, median over session; also
  shown live on the in-app Measurements screen (screenshot-able).
- Single voice, single room, single Wi-Fi network, ngrok free relay:
  generalization beyond this setup is explicitly out of claim.

## Known limitations (stated, not hidden)

- `mā`-type onset loss on abrupt file starts: model repeat-count bias
  (same family as training-profile madd/shadda drops), trie-tolerated.
- 28/104 ayahs had fused cross-word tokens suppressing mistake text;
  mapping now degrades gracefully (fixed, verified by suite).
- CPU RSS pair read inverted in one run (allocator/page-cache variance);
  GPU pair (992 → 1684 MB) is the quoted memory row.
