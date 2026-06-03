# spjallromur-mix

A pipeline for aligning and mixing the dual-channel recordings in the
[Spjallrómur](https://clarin.is/en/resources/spjallromur/) conversational Icelandic corpus,
and assembling the CLARIN v2 deposit directory.

Each conversation is recorded locally on both sides of the call, producing two
separate mono WAV files. Independent soundcard clocks cause the recordings to drift
out of sync over time. This pipeline corrects for that drift, mixes the two channels
into a stereo WAV (Speaker A = left, Speaker B = right), and produces time-aligned
transcript JSON files including a merged transcript with speaker labels.

## Pipeline

**Stage 1 — Analysis (`analyse.py`)**: scans all sessions, measures clock drift,
and writes per-session parameter files and a corpus-level summary. No audio is produced.

**Stage 2 — Synthesis (`synthesise.py`)**: reads the parameter files from Stage 1
and produces, for each session, a mixed stereo WAV and corrected transcript files.
One session (`198f2863`) is excluded due to bad audio quality.

**Stage 3 — Deposit (`prepare_deposit.py`)**: assembles the complete CLARIN v2
deposit directory from the CLARIN source, the v2 transcript root, and the pipeline
outputs.

See [DESIGN.md](DESIGN.md) for full design documentation.

## Dependencies

```
librosa, resampy, soundfile, numpy, scipy
```

Install with:
```bash
pip install -r requirements.txt
```

## Usage

The pipeline uses two separate read-only input roots:
- `--corpus-root`: the original CLARIN release (WAV files)
- `--transcript-root`: the v2 GitHub release (forced-alignment JSON files)

```bash
# Stage 1: measure drift and write parameter files
python analyse.py \
    --corpus-root /path/to/clarin/spjallromur \
    --transcript-root /path/to/spjallromur-v2 \
    --output-root /path/to/output

# Stage 2: produce mixed WAV and aligned transcripts
python synthesise.py \
    --corpus-root /path/to/clarin/spjallromur \
    --transcript-root /path/to/spjallromur-v2 \
    --output-root /path/to/output

# Process a specific subset of sessions
python synthesise.py \
    --corpus-root /path/to/clarin/spjallromur \
    --transcript-root /path/to/spjallromur-v2 \
    --output-root /path/to/output \
    --sessions 2a07b3a7,01119679

# Stage 3: assemble the CLARIN v2 deposit directory
python prepare_deposit.py \
    --corpus-root /path/to/clarin/spjallromur \
    --transcript-root /path/to/spjallromur-v2 \
    --output-root /path/to/output \
    --deposit-root /path/to/deposit
```

## Outputs

Each session folder in the pipeline output directory contains:
- `session_params.json` — drift measurements and correction parameters
- `<session_id>_mixed.wav` — stereo mixed audio
- `a_<session_id>_<age>_<gender>_aligned.json` — aligned transcript, Speaker A
- `b_<session_id>_<age>_<gender>_aligned.json` — aligned transcript, Speaker B
- `<session_id>_transcript_merged.json` — both speakers interleaved by time

## Corpus statistics

```bash
python stats/corpus_stats_table.py \
    --corpus-root /path/to/clarin/spjallromur \
    --transcript-root /path/to/spjallromur-v2 \
    --output-root /path/to/output
```

Prints computed statistics and emits a LaTeX table for the resource paper.

## Licence

The pipeline code in this repository is licensed under the
[Apache License 2.0](LICENSE).

The Spjallrómur corpus data (available separately via CLARIN) is licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
