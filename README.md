# spjallromur-mix

A two-stage pipeline for aligning and mixing the dual-channel recordings in the
[Spjallrómur](https://clarin.is/en/resources/spjallromur/) conversational Icelandic corpus.

Each conversation is recorded locally on both sides of the call, producing two
separate mono WAV files. Independent soundcard clocks cause the recordings to drift
out of sync over time. This pipeline corrects for that drift, mixes the two channels
into a stereo WAV (Speaker A = left, Speaker B = right), and produces time-aligned
transcript JSON files including a merged transcript with speaker labels.

## Pipeline

**Stage 1 — Analysis (`analyse.py`)**: scans all sessions, measures clock drift,
classifies each session by correction tier, and writes per-session parameter files
and a corpus-level summary. No audio is produced.

**Stage 2 — Synthesis (`synthesise.py`)**: reads the parameter files from Stage 1
and produces, for each session, a mixed stereo WAV and corrected transcript files.

See [DESIGN.md](DESIGN.md) for full design documentation.

## Dependencies

```
librosa
soundfile
numpy
scipy
```

Install with:
```bash
pip install -r requirements.txt
```

## Usage

```bash
python analyse.py --corpus-root /path/to/spjallromur --output-root /path/to/output
python synthesise.py --corpus-root /path/to/spjallromur --output-root /path/to/output
```

## Outputs

Each session folder in the output directory contains:
- `session_params.json` — drift measurements and correction parameters
- `<session_id>_mixed.wav` — stereo mixed audio
- `speaker_a_convo_<session_id>_transcript_aligned.json`
- `speaker_b_convo_<session_id>_transcript_aligned.json`
- `<session_id>_transcript_merged.json` — both speakers interleaved by time

## Licence

TBD
