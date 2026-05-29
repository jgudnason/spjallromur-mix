# DESIGN.md — Spjallrómur Channel Alignment and Mixing Pipeline

## Purpose

Spjallrómur conversations are recorded with each side of the call captured locally,
producing two separate mono WAV files (`speaker_a_convo_<session>.wav`,
`speaker_b_convo_<session>.wav`). Because the two recording devices use independent
clocks, their sample rates drift relative to each other over time. This drift is
assumed to be small and approximately linear within any single session, but
accumulates toward the end of longer recordings.

This pipeline aligns the two channels in time, mixes them into a single stereo WAV
file (A=left, B=right), and produces corrected transcript JSON files with all
timestamps remapped to the new shared timeline.

The pipeline is intended to be fully transparent and reproducible. All intermediate
parameters are written to disk so that the mixing step can be re-run independently
of the analysis step. All outputs — mixed audio, corrected transcripts, and
per-session parameter files — are released alongside the code.

---

## Architecture

The pipeline is divided into two independent stages.

### Stage 1 — Analysis (`analyse.py`)

Iterates over all sessions in the source corpus. For each session, it measures the
lengths of both channels, characterises the drift, classifies the session into one
of three tiers, and writes a per-session parameter file. It also writes a corpus-
level summary.

**No audio output is produced in Stage 1.**

### Stage 2 — Synthesis (`synthesise.py`)

Reads per-session parameter files produced by Stage 1. For each session, it applies
the correction specified in the parameter file to align the two channels, writes the
mixed stereo WAV, and writes corrected transcript JSON files. Stage 2 can be run
blindly on any session for which a valid parameter file exists.

---

## Directory Structure

### Audio input (`--corpus-root`, CLARIN release, read-only)

WAV files only. Both `full_conversations` and `half_conversations` exist under
this root; the pipeline processes `full_conversations` only.

```
<corpus_root>/spjallromur/data/full_conversations/
    <session_id>/
        speaker_a_convo_<session_id>.wav
        speaker_b_convo_<session_id>.wav
```

### Transcript input (`--transcript-root`, v2 aligned release, read-only)

Word-level forced-alignment JSON files. Age and gender suffixes vary per speaker;
files are discovered by globbing `a_<session_id>_*.json` and `b_<session_id>_*.json`
within each session folder.

```
<transcript_root>/full_conversations/
    <session_id>/
        a_<session_id>_<age>_<gender>.json
        b_<session_id>_<age>_<gender>.json
```

### Output

```
<output_root>/
    corpus_summary.json                                          ← written by Stage 1
    <session_id>/
        session_params.json                                      ← Stage 1
        <session_id>_mixed.wav                                   ← Stage 2
        a_<session_id>_<age>_<gender>_aligned.json               ← Stage 2
        b_<session_id>_<age>_<gender>_aligned.json               ← Stage 2
        <session_id>_transcript_merged.json                      ← Stage 2
```

---

## Drift Model

All sessions are processed using a single linear resample (Tier A), regardless of
drift magnitude. The shorter channel is resampled to match the length of the longer
channel using a single linear stretch factor:

**Correction parameter:** `resample_ratio = len_longer_samples / len_shorter_samples`

Stage 1 computes natural gap thresholds in the drift distribution and records them
in `corpus_summary.json` as `proposed_thresholds`. These are **informational only**
and do not gate processing. The `above_1pct_threshold` flag in each
`session_params.json` marks sessions where drift exceeds 1% so that reviewers can
inspect those sessions without any being silently dropped.

---

## Per-Session Parameter File (`session_params.json`)

Written by Stage 1; updated by Stage 2 with `resample_ratio` and `transcript_source`.

```json
{
  "session_id": "2a07b3a7",
  "duration_a_sec": 985.868,
  "duration_b_sec": 984.331,
  "delta_sec": 1.537,
  "drift_percent": 0.156,
  "above_1pct_threshold": false,
  "reference_channel": "b",
  "tier": "A",
  "resample_ratio": 1.00156138,
  "transcript_source": "v2",
  "corrected_sample_rate": false,
  "original_sample_rate": null,
  "pipeline_version": "1.0.0",
  "stage1_timestamp": "2026-05-13T00:00:00Z"
}
```

`reference_channel` is always the longer channel; the other is the target and is
resampled. `above_1pct_threshold` is informational only — all sessions are processed
regardless. `corrected_sample_rate` and `original_sample_rate` are set only for
sessions with a known WAV header anomaly (see Known Session Anomalies below).

---

## Reference and Target Convention

The **longer channel is always the reference**; its audio and timestamps are
unchanged. The **shorter channel is always the target**; it is warped to match the
reference timeline. This avoids truncating any content and requires remapping only
one transcript.

Both corrected transcripts share the same absolute timeline as the mixed stereo WAV.

---

## Stage 2 Synthesis Pipeline

For each session, Stage 2 executes the following steps in order:

1. **Determine alignment function** — read `session_params.json` to obtain the
   correction parameters for this session's tier.

2. **Apply alignment function to the target signal** — warp the shorter channel's
   audio to match the reference channel's duration, producing an aligned signal.

3. **Apply alignment function to the target transcript** — remap all `startTime` and
   `endTime` values in the target transcript through the same function, producing an
   aligned transcript. Steps 2 and 3 are independent and may run in either order.

4. **Mix aligned signal with reference signal** — interleave the two mono signals as
   left (channel A) and right (channel B) of a stereo WAV, regardless of which is
   reference and which is target.

5. **Merge reference transcript and aligned transcript** — combine all segments from
   both transcripts, sorted by `startTime`, into a single merged transcript for the
   mixed signal.

---

## Transcript Remapping and Speaker Tagging

### Transcript sources and fallback

Stage 2 tries to load the v2 forced-alignment transcript for each speaker from
`--transcript-root/full_conversations/<session_id>/`. If no v2 file is found (e.g.
sessions not included in the v2 release), it falls back to the CLARIN transcript at
`--corpus-root/.../full_conversations/<session_id>/speaker_{a|b}_convo_<session_id>_transcript.json`.

The CLARIN format uses a `segments` → `words` hierarchy with `startTime`/`endTime`
keys. The fallback reader flattens this into the same word-list format used by v2
(with `start`/`end` keys), so the rest of the pipeline is format-agnostic.
`norm_word` is set to `null` for CLARIN-sourced words (no normalised form available).

The `transcript_source` field in `session_params.json` records which source was used
(`"v2"` or `"clarin"`). Output aligned transcript filenames follow the v2 convention
(`a_<session_id>_<age>_<gender>_aligned.json`) when source is v2, and
`a_<session_id>_aligned.json` when source is CLARIN.

### Transcript format (v2)

The v2 transcript JSON files contain a flat word list with forced-alignment
timestamps, with no segment grouping:

```json
{
  "metadata": {
    "age": "40-49",
    "gender": "female",
    "audio_duration": 664.704,
    "speaker": "a"
  },
  "words": [
    { "word": "Þá", "norm_word": "þá", "start": 2.1, "end": 2.37 },
    ...
  ]
}
```

### Per-speaker aligned transcripts

All `start` and `end` timestamps in the target transcript are remapped. The
reference transcript is copied unchanged except that both receive two additional
metadata fields: `audio_file` (pointing to the mixed WAV) and `audio_duration`
(updated to the reference channel duration). A `"speaker"` field (`"a"` or `"b"`)
is added to every word in both transcripts.

For **Tier A**, remapping is a scalar multiplication:
```
t_new = t_old * resample_ratio
```

For **Tier B**, remapping applies the offset first, then the ratio:
```
t_new = (t_old + offset_sec) * resample_ratio
```

For **Tier C**, remapping uses linear interpolation through the anchor points.

Original transcript files are never modified.

### Merged transcript (`<session_id>_transcript_merged.json`)

The merged transcript interleaves all words from both aligned transcripts, sorted
by `start` time. Each word retains its `"speaker"` field. The top-level structure is:

```json
{
  "session_id": "2a07b3a7",
  "audio_file": "2a07b3a7_mixed.wav",
  "recording_duration": 985.868,
  "words": [
    { "speaker": "a", "word": "Já,", "norm_word": "já", "start": 15.959, "end": 16.199 },
    { "speaker": "b", "word": "Ókei,", "norm_word": "ókei", "start": 21.632, "end": 21.722 },
    ...
  ]
}
```

---

## Corpus-Level Summary (`corpus_summary.json`)

Written by Stage 1 after processing all sessions. Contains:

- Per-session: `session_id`, `duration_a`, `duration_b`, `delta_sec`,
  `drift_percent`, `tier`
- Aggregate statistics: mean/std/max drift across all sessions
- Proposed tier thresholds with rationale
- Count of sessions per tier

---

## Audio Output

- Format: WAV, stereo (Speaker A = left channel, Speaker B = right channel)
- Sample rate: inherited from source files (assumed 16 kHz; a warning is raised if
  A and B differ)
- Bit depth: 16-bit PCM
- Filename: `<session_id>_mixed.wav`

Resampling uses `librosa.resample` with `res_type='kaiser_best'` by default,
configurable to `'kaiser_fast'` for batch processing.

---

## Dependencies

```
librosa
soundfile
numpy
scipy
```

No other dependencies. Python 3.10+.

---

## Configurables (command-line arguments)

Both scripts accept:

| Argument | Default | Description |
|---|---|---|
| `--corpus-root` | (required) | Path to CLARIN corpus root (WAV files) |
| `--transcript-root` | (required\*) | Path to v2 transcript root; CLARIN used as fallback |
| `--output-root` | (required) | Path to output directory |
| `--sessions` | all | Comma-separated list of session IDs to process |
| `--res-type` | `kaiser_best` | librosa resampling quality |

\* Optional in `analyse.py` (not used for filtering); required in `synthesise.py`.

---

## What Is Released

- `analyse.py` and `synthesise.py`
- `corpus_summary.json`
- Per-session `session_params.json` files
- Mixed stereo WAV files (`*_mixed.wav`)
- Per-speaker aligned transcript JSON files (`*_aligned.json`)
- Merged transcript JSON files (`*_transcript_merged.json`)
- This `DESIGN.md`

Original source WAV files and original transcript JSON files are not redistributed
(they form part of the separately released Spjallrómur corpus).

---

## Known Session Anomalies

### `198f2863` — excluded (bad audio quality)

Session `198f2863` is excluded from pipeline output entirely. The recordings
contain multiple voices, severe audio artefacts, and a probable sample rate
mismatch (WAV header claims 16000 Hz; audio was likely recorded at 12000 Hz).
The artefacts are too severe to produce a usable stereo mix.

Stage 1 (`analyse.py`) still writes a `session_params.json` for this session, but
with `"status": "excluded"` and an `"exclusion_reason"` field rather than the
normal measurement fields. Stage 2 (`synthesise.py`) skips it with no audio or
transcript output.

### `2a139f9b` — no v2 transcript

This session is present in the CLARIN release but was not included in the v2
forced-alignment release. Stage 2 falls back to the CLARIN transcript and records
`transcript_source: "clarin"` in `session_params.json`. The timestamps are the
original, unverified CLARIN ones.

---

## Open Items

- [ ] Confirm sample rate of all Spjallrómur sessions (assumed 16 kHz)
- [ ] Validate Tier C on the worst-drift sessions before finalising anchor-point
      estimation method
- [ ] Assign a version string and DOI for the pipeline release
