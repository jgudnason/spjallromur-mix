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

### Input (source corpus, read-only)

```
<corpus_root>/spjallromur/data/full_conversations/
    <session_id>/
        speaker_a_convo_<session_id>.wav
        speaker_b_convo_<session_id>.wav
        speaker_a_convo_<session_id>_transcript.json
        speaker_b_convo_<session_id>_transcript.json
```

### Output (mirrors source structure)

```
<output_root>/
    corpus_summary.json
    <session_id>/
        session_params.json                                      ← written by Stage 1
        <session_id>_mixed.wav                                   ← written by Stage 2
        speaker_a_convo_<session_id>_transcript_aligned.json     ← Stage 2
        speaker_b_convo_<session_id>_transcript_aligned.json     ← Stage 2
        <session_id>_transcript_merged.json                      ← Stage 2
```

---

## Drift Model and Session Classification

Stage 1 classifies each session into one of three tiers based on the measured drift
characteristics. Tier thresholds are not hard-coded: Stage 1 computes the
distribution of drift magnitudes across all sessions and proposes thresholds based
on that distribution (e.g. at natural breaks in the sorted `Tper` values). The
proposed thresholds are recorded in `corpus_summary.json` and can be overridden
manually before running Stage 2.

### Tier A — Simple resample

The two channels differ only in total length, with the start assumed to be aligned.
The shorter channel is resampled to match the length of the longer channel using a
single linear stretch factor.

**Correction parameter:** `resample_ratio = len_longer / len_shorter`

Applicable when drift is small and uniform (expected: the majority of sessions).

### Tier B — Shift + resample

The shorter channel appears to start at a slight offset relative to the longer, in
addition to a uniform drift. A zero-padding offset is applied at the start (or end)
before the linear resample.

**Correction parameters:** `start_offset_samples` (zero-padding at start of shorter
channel), then `resample_ratio` applied to the padded signal.

The start offset is estimated by cross-correlating the energy envelopes of the two
channels over the first 60 seconds of the recording.

### Tier C — Piecewise-linear warp

The drift is non-linear or shows evidence of discontinuities. A piecewise-linear
time map is estimated by dividing the recording into N equal segments, computing a
local shift estimate per segment using energy-envelope cross-correlation, and fitting
a monotone piecewise-linear function through the resulting anchor points.

**Correction parameters:** a list of `(t_in, t_out)` anchor pairs defining the
warp function. Audio resampling is applied segment-by-segment using the local
stretch ratio for each segment.

N (number of segments) is a configurable parameter, defaulting to 10.

---

## Per-Session Parameter File (`session_params.json`)

```json
{
  "session_id": "2a07b3a7",
  "duration_a_sec": 978.924,
  "duration_b_sec": 973.143,
  "delta_sec": 5.781,
  "drift_percent": 0.593,
  "reference_channel": "a",
  "tier": "A",
  "correction": {
    "resample_ratio": 1.00594
  },
  "pipeline_version": "1.0.0",
  "stage1_timestamp": "2026-05-10T12:00:00Z"
}
```

For Tier B, `correction` additionally contains `start_offset_samples`.
For Tier C, `correction` contains `anchor_points: [[t_in, t_out], ...]` and
`n_segments`.

The `reference_channel` field records which channel is treated as the time
reference (always the longer one). The other channel is warped to match it.

---

## Reference and Target Convention

The **longer channel is always the reference**; its audio and timestamps are
unchanged. The **shorter channel is always the target**; it is warped to match the
reference timeline.

This is a documented convention, not a physical necessity. It is chosen because it
avoids truncating any content and requires remapping only one transcript. If the
length difference is implausibly large (currently: > 5% of total duration), Stage 1
flags the session for manual inspection rather than classifying it automatically,
since a gap that large suggests a recording fault rather than clock drift.

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

### Per-speaker aligned transcripts

Both the segment-level and word-level `startTime` and `endTime` fields are remapped
in the target transcript. The reference transcript is copied unchanged except for
`metadata.audio_file`, which is updated to point to the mixed WAV in both cases.
`metadata.recordingDuration` is updated to the reference channel duration in both.

A `"speaker"` field is added to every **segment** and every **word** object in both
aligned transcripts, set to `"a"` or `"b"` according to the source channel. This is
added uniformly to both transcripts (not only the target) so that the per-speaker
files are consistent with the merged transcript.

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

The merged transcript interleaves all segments from both aligned transcripts, sorted
by `startTime`. Each segment and word retains its `"speaker"` field. The top-level
structure is:

```json
{
  "session_id": "2a07b3a7",
  "audio_file": "2a07b3a7_mixed.wav",
  "recordingDuration": 978.924,
  "languageCode": "is-IS",
  "segments": [
    {
      "speaker": "a",
      "startTime": 15.959,
      "endTime": 18.09,
      "words": [
        { "speaker": "a", "startTime": 15.959, "endTime": 16.199, "word": "Hver " },
        ...
      ]
    },
    {
      "speaker": "b",
      "startTime": 21.632,
      "endTime": 23.761,
      "words": [ ... ]
    },
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
| `--corpus-root` | (required) | Path to source corpus root |
| `--output-root` | (required) | Path to output directory |
| `--sessions` | all | Comma-separated list of session IDs to process |
| `--res-type` | `kaiser_best` | librosa resampling quality |

`analyse.py` additionally accepts:
| `--n-segments` | 10 | Number of segments for Tier C piecewise estimation |
| `--tier-a-max` | TBD | Override Tier A threshold (drift %) |
| `--tier-b-max` | TBD | Override Tier B threshold (drift %) |

---

## What Is Released

- `analyse.py` and `synthesise.py`
- `corpus_summary.json`
- Per-session `session_params.json` files
- Mixed stereo WAV files (`*_mixed.wav`)
- Per-speaker aligned transcript JSON files (`*_transcript_aligned.json`)
- Merged transcript JSON files (`*_transcript_merged.json`)
- This `DESIGN.md`

Original source WAV files and original transcript JSON files are not redistributed
(they form part of the separately released Spjallrómur corpus).

---

## Open Items

- [ ] Confirm sample rate of all Spjallrómur sessions (assumed 16 kHz)
- [ ] Validate Tier C on the worst-drift sessions before finalising anchor-point
      estimation method
- [ ] Assign a version string and DOI for the pipeline release
