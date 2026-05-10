#!/usr/bin/env python3
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

import soundfile as sf


def find_session_pairs(corpus_root: Path):
    sessions = {}
    for wav_path in corpus_root.rglob("speaker_*_convo_*.wav"):
        name = wav_path.name
        if not name.startswith("speaker_") or not name.endswith(".wav"):
            continue
        parts = name.split("_convo_")
        if len(parts) != 2:
            continue
        speaker_prefix, session_part = parts
        speaker_id = speaker_prefix.replace("speaker_", "")
        session_id = session_part[:-4]
        if speaker_id not in {"a", "b"}:
            continue
        sessions.setdefault(session_id, {})[speaker_id] = wav_path
    return sessions


def duration_seconds(wav_path: Path) -> float:
    info = sf.info(str(wav_path))
    if info.samplerate <= 0:
        raise ValueError(f"Invalid samplerate in {wav_path}")
    return float(info.frames) / float(info.samplerate)


def write_session_params(output_dir: Path, session_id: str, params: dict):
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "session_params.json"
    with path.open("w", encoding="utf-8") as fh:
        json.dump(params, fh, indent=2)
        fh.write("\n")
    return path


def format_seconds(value: float) -> str:
    return f"{value:.3f}"


def print_summary(rows):
    headers = ["session_id", "dur_a(s)", "dur_b(s)", "delta(s)", "drift(%)", "ref"]
    widths = [max(len(str(row[i])) for row in rows + [headers]) for i in range(len(headers))]
    line = "  ".join(header.ljust(widths[i]) for i, header in enumerate(headers))
    print(line)
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        print("  ".join(str(row[i]).ljust(widths[i]) for i in range(len(headers))))


def main():
    parser = argparse.ArgumentParser(description="Stage 1 analysis: measure session durations and create parameter stubs.")
    parser.add_argument("--corpus-root", required=True, help="Path to the corpus root containing session directories.")
    parser.add_argument("--output-root", required=True, help="Path to the output root where session parameter files are written.")
    args = parser.parse_args()

    corpus_root = Path(args.corpus_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()

    if not corpus_root.exists() or not corpus_root.is_dir():
        print(f"Error: corpus root not found or not a directory: {corpus_root}", file=sys.stderr)
        sys.exit(1)

    sessions = find_session_pairs(corpus_root)
    if not sessions:
        print(f"No valid session WAV pairs found under {corpus_root}", file=sys.stderr)
        sys.exit(1)

    summary_rows = []
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for session_id, wavs in sorted(sessions.items()):
        if "a" not in wavs or "b" not in wavs:
            print(f"Skipping session {session_id}: missing speaker_a or speaker_b WAV", file=sys.stderr)
            continue

        path_a = wavs["a"]
        path_b = wavs["b"]
        duration_a = duration_seconds(path_a)
        duration_b = duration_seconds(path_b)
        delta = abs(duration_a - duration_b)
        reference_channel = "a" if duration_a >= duration_b else "b"
        reference_duration = max(duration_a, duration_b)
        drift_percent = (delta / reference_duration) * 100 if reference_duration > 0 else 0.0

        params = {
            "session_id": session_id,
            "duration_a_sec": round(duration_a, 6),
            "duration_b_sec": round(duration_b, 6),
            "delta_sec": round(delta, 6),
            "drift_percent": round(drift_percent, 6),
            "reference_channel": reference_channel,
            "tier": None,
            "correction": None,
            "pipeline_version": "1.0.0",
            "stage1_timestamp": timestamp,
        }

        output_dir = output_root / session_id
        write_session_params(output_dir, session_id, params)

        summary_rows.append([
            session_id,
            format_seconds(duration_a),
            format_seconds(duration_b),
            format_seconds(delta),
            f"{drift_percent:.3f}",
            reference_channel,
        ])

    if summary_rows:
        print_summary(summary_rows)
    else:
        print("No sessions processed.")


if __name__ == "__main__":
    main()
