# Work-Flow — From Recitation to Highlighting

How the Tajweed app works today, end to end. Terms in **bold** are footnoted at the bottom of this file.

---

## 1. Overview

```
 Phone mic → PCM audio bytes → WebSocket → Server Session (audio buffer)
                                                    │
                                       heartbeat every 0.5 s
                                                    │
                                      Energy gate: speech or silence?
                                                    │
                         ┌──────────────────────────┴──────────────────────────┐
                         │ speech beat                                            │ silence beat
                         │ 1. strip decode (fast, low quality)                    │ decode skipped
                         │ 2. burst start tracking / phrase decode (slow, high    │
                         │    quality) every ~3 s and at burst end                │
                         └──────────────────────────┬──────────────────────────────┘
                                                    │
                                       raw phoneme stream → live display
                                       clean phrase go to the guided trie
                                                    │
                         open-mic only: ayah detection & advance (fresh decode)
                                                    │
                                       word confirmation (ordered, monotonic)
                                                    │
                                     interim WebSocket messages every beat
                                                    │
                              Flutter UI highlights words/letters live
                                                    │
                                             user presses Stop
                                                    │
                           authoritative final decode (evaluate_audio) + meters
                                                    │
                                        final WebSocket message → result screen
```

The whole loop is driven by the phone's microphone: there is **no recording file** during a live session (files are only written temporarily at the very end for scoring).

---

## 2. Step-by-step

### Step 1 — The app connects and streams the mic

1. The Flutter app opens a **WebSocket**[^ws] connection to `/ws/recite` and sends a small JSON metadata message: the mode (`single`, `surah`, or `open_mic`) plus `surah_id` (and `ayah_id` for single-ayah mode).
2. The phone's microphone is started at **16 kHz**, mono, **PCM16**[^pcm] — i.e. 16,000 samples/second, each sample is a signed 16-bit integer (2 bytes). The phone sends these raw bytes straight over the socket in small chunks, as fast as they are produced.
3. The server keeps everything the phone sent in a growing `audio` byte buffer for that session.

### Step 2 — The heartbeat

- The server runs a **heartbeat/tick**[^hb] every **0.5 s**: *"did a meaningful amount of new audio arrive?"* (defined as ≥ 8000 bytes ≈ 0.5 s of speech).
- If yes, it sees the new chunk as one "beat" and processes it. If not, it just waits for the next beat.

### Step 3 — Speaking or silence? (energy gate)

- The server measures the **RMS**[^rms] amplitude of the new chunk — a measure of how loud it is.
- If the chunk is quiet, it is treated as **silence**:
  - The main decoder is *skipped* (quiet microphone noise would otherwise be decoded into hallucinated "phonemes" / garbage letters).
  - The streaming highlight and bookkeeping keep running with an empty token batch.
- A **hysteresis**[^hyst] rule (two thresholds: drop into silence below RMS≈100, only resume speech above RMS≈200) stops the system from flapping between speech/silence on every beat when the mic level hovers near the border.
- The speech→silence transition also marks "a phrase just finished", which triggers the **burst**-phrase decode (Step 5).

### Step 4 — The fast strip decode (per speech beat)

- On each speech beat, the server decodes the **trailing 3 s** of audio with the neural network.
- Raw model output is a long list of **CTC**[^ctc] frames — one **frame**[^frame] ≈ every 320 samples (**stride**[^stride]) ≈ 20 ms of audio; the input is padded forward by 15 **pad frames**[^pad].
- The server only keeps the frames that represent *newly spoken* audio, with a **lookahead**[^look] of 16 frames (~0.16 s) of right-context so a **phoneme**[^phoneme] at the boundary has some evidence behind it.
- The surviving frames are collapsed into characters — collapsing repeated frames (blank-aware collapse) — producing a short **strip decode**[^strip] string (e.g. `لَببَت يَ`).
- This text is appended to the session's `stream_pred_raw` (used as the "live" preview) and `feed_tail`.

> Current limitation (known): because each strip is decoded from a *window* and only a thin slice is emitted, boundary phonemes get locked in with little right-context, and consecutive strips are independent opinions stitched together. This is why the live phoneme text looks bad (`نَاا عَلَهُاا…`). The fix in progress makes the visible transcript come from step 5's clean decode instead.

### Step 5 — The clean phrase decode (the good signal)

- The server remembers where the current speech **burst**[^burst] started (`_burst_start`).
- Every ~3 s of continuous speech *and* when the burst ends (silence), it runs the network **once over the whole burst** (a **one-shot / full-context decode**[^oneshot]). Offline experiments show this signal is dramatically better than the strip.
- The clean decode is fed to the **guided trie** (the word highlighter) and also used for open-mic detection/advance decisions.

### Step 6 — Ayah detection (open-mic mode only)

- Before any ayah is known, the server accumulates phoneme text and tries to **match it to a known ayah**.
- Using `resolve_ayah`, a prefix **trie**[^trie] over all ayahs of the allowed surah(s), it performs a fuzzy search with an **edit budget**[^lev] (≈35% Levenshtein error allowed).
- Once a match is confident enough, the server *commits* the ayah: it seeds the word list for that ayah, builds the guided trie, and sends the app a `detected` message so the highlight can start.
- If the user keeps reciting past that ayah (stall detection + fresh-decode matching via `advance_target`, forward-only), the server commits the **later** ayah and the highlight moves.

### Step 7 — Word-by-word highlighting (guided trie)

- Each ayah's reference text is preprocessed into an ordered list of per-word **phoneme targets** (via a phonetizer + tashkeel-stripping) at startup.
- The **GuidedLiveTrie** walks those words **in order** (**monotonic**[^mono] — never backward):
  - New phoneme text (strip + clean phrase) is appended to a tail buffer.
  - On each beat it checks whether the *next expected word*'s phonemes appear in the tail, using exact substring or a sliding-window **Levenshtein**[^lev] distance within a small budget.
  - On a match it locks that word (marks letters ok/miss), advances the pointer, and (skip-tolerance) may skip a word that never showed up if the *following* word clearly matches — so a clipped first word doesn't freeze the spotlight.
- Locked words are sent to the app every beat as an **interim**[^interim] message: full word list + per-letter `ok`/`miss`/`neutral` statuses + the current `active_index` (the spotlight).

### Step 8 — App-side rendering

- The app receives interim messages ~2×/second and **OR-merges** them into its local word state (`is_read` accumulates; letter statuses come from the server) — so highlights only ever light up more, never un-light.
- The active word is spotlighted; letters inside a word get colored per their status; the whole detected ayah gets a border.
- In open-mic mode, the app also shows the raw `live` phoneme preview and re-highlights when the server advances to a later ayah.

### Step 9 — The authoritative final

- When the user presses Stop, the app sends a `stop` control message but keeps the socket open.
- The server runs `evaluate_audio` on the whole recorded segment (one final full-context decode through the authoritative matcher) and produces:
  - `real_text` / `expected` / `predicted`
  - per-word and per-letter `is_read` + statuses
  - **accuracy**[^acc] and **PER**[^per] (phoneme error rate), plus a letter/phoneme difference list
- This is sent as the **final**[^final] message; the server then closes the socket and the app shows the result screen.

### Other modes (not open-mic)

- **Single ayah:** user picks one ayah; Step 7 runs against that ayah's words directly.
- **Surah mode:** the ayahs are walked sequentially using a Next button (`next`) — the server scores the current ayah (a mini final), advances to the next, and repeats until the surah is complete.

---

## Glossary (footnotes)

[^ws]: **WebSocket** — a persistent two-way message channel over TCP. The phone keeps one socket open: audio bytes flow up, result messages flow down. Unlike plain HTTP, both sides can push at any time, repeatedly.

[^pcm]: **PCM16 / PCM** — Pulse-Code Modulation, i.e. raw digital audio. "16" = each sample is a 16-bit signed integer; at 16 kHz and mono this is 32,000 bytes per second.

[^hb]: **Heartbeat / tick** — the server wakes 2×/second (every 0.5 s) to check for and process newly arrived audio.

[^rms]: **RMS amplitude** — Root Mean Square of the sample values; a single number summarizing how loud a chunk of audio is.

[^hyst]: **Hysteresis** — two thresholds (enter silence, leave silence) instead of one, so a slightly noisy mic doesn't cause rapid on/off flapping near a single threshold.

[^ctc]: **CTC** — Connectionist Temporal Classification: a training/output scheme where the model emits a classification per time step (including blank frames) and these are collapsed into characters, so speech can be recognized without per-character alignment labels.

[^frame]: **Frame** — the minimal unit of model output. Here every ~320 samples produces one frame, ≈20 ms of audio; a 3 s window ≈ 150 frames.

[^stride]: **Stride** — how many samples the model advances per frame (320 samples/frame in this app).

[^pad]: **Pad frames** — extra silent frames appended before inference so the very first audio doesn't start at the network input edge.

[^look]: **Lookahead (lead frames)** — future context kept when slicing output. The app always emits a slice ending 16 frames short of the newest audio, so a character at the slice edge "sees" ~0.16 s of what comes next.

[^phoneme]: **Phoneme** — a unit of sound. The model's alphabet is Arabic characters with simplified diacritics (live strips look like `لَببَت يَ`).

[^oneshot]: **One-shot / full-context decode** — running the model over a whole phrase at once so every character is decided with full left AND right context. Offline-measured to be much more accurate than the strip.

[^strip]: **Strip decode** — the fast per-beat decode of a short trailing slice, used purely for immediacy. It is the weakest quality signal class (offline experiment measured ~43–62 edit distance vs ~24 for a one-shot decode).

[^trie]: **Trie** — a prefix-tree data structure for fast lookup among many strings that share prefixes (here, the phoneme streams of all ayahs).

[^lev]: **Levenshtein distance / edit budget** — the number of single-character insertions/deletions/substitutions needed to turn one string into another. A prediction matches if its distance is within a fraction (≈35%) of the reference length.

[^mono]: **Monotonic highlighting** — the word pointer only moves forward; it never re-marks an earlier word.

[^interim]: **Interim message** — a live, non-final update streamed each heartbeat showing current word/letter status + the active word index.

[^acc]: **accuracy** — fraction of target phonemes matched (higher = better); computed per word and overall in this app.

[^per]: **PER** — Phoneme Error Rate: fraction of phonemes differing between reference and prediction (lower = better).

[^final]: **Final message** — the authoritative, one-time result sent at Stop (includes accuracy, PER, and per-letter difference lists).

[^burst]: **Burst** — one continuous stretch of speech between silences. The system tracks its start sample so the whole phrase can be re-decoded cleanly at burst end.