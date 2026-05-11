#!/usr/bin/env python3
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

import soundfile as sf


def propose_thresholds(drifts):
    """Propose tier boundaries from the drift distribution using the largest-gap method."""
    inspection_max = 5.0  # per DESIGN.md: > 5% flagged for manual inspection
    below = sorted(d for d in drifts if 0 < d < inspection_max)

    if len(below) < 3:
        return {
            "tier_a_max": 1.0,
            "tier_b_max": 3.0,
            "inspection_max": inspection_max,
            "rationale": "Insufficient data for gap analysis; defaults used.",
        }

    gaps = sorted(
        ((below[i + 1] - below[i], below[i], below[i + 1]) for i in range(len(below) - 1)),
        reverse=True,
    )

    # Mid-points of the two largest gaps give natural tier boundaries.
    breaks = sorted(round((lo + hi) / 2, 3) for _, lo, hi in gaps[:2])
    tier_a_max = breaks[0]
    tier_b_max = breaks[1] if len(breaks) > 1 else inspection_max

    top_gap = gaps[0]
    second_gap = gaps[1] if len(gaps) > 1 else None
    rationale = (
        f"Tier A / Tier B boundaries proposed at natural gaps in sorted drift distribution "
        f"(largest gap: {round(top_gap[0], 3)}% at {top_gap[1]:.3f}→{top_gap[2]:.3f}"
        + (
            f", second gap: {round(second_gap[0], 3)}% at {second_gap[1]:.3f}→{second_gap[2]:.3f}"
            if second_gap else ""
        )
        + f"). Sessions with drift > {inspection_max}% are flagged for manual inspection."
    )
    return {
        "tier_a_max": tier_a_max,
        "tier_b_max": tier_b_max,
        "inspection_max": inspection_max,
        "rationale": rationale,
    }


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
    session_records = []
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

        session_records.append({
            "session_id": session_id,
            "duration_a_sec": params["duration_a_sec"],
            "duration_b_sec": params["duration_b_sec"],
            "delta_sec": params["delta_sec"],
            "drift_percent": params["drift_percent"],
            "tier": params["tier"],
        })

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
        return

    drifts = [r["drift_percent"] for r in session_records]
    n = len(drifts)
    mean_drift = sum(drifts) / n
    variance = sum((d - mean_drift) ** 2 for d in drifts) / n
    std_drift = variance ** 0.5

    tier_counts: dict[str, int] = {}
    for r in session_records:
        key = r["tier"] if r["tier"] is not None else "null"
        tier_counts[key] = tier_counts.get(key, 0) + 1

    corpus_summary = {
        "pipeline_version": "1.0.0",
        "timestamp": timestamp,
        "sessions": session_records,
        "aggregate_stats": {
            "count": n,
            "mean_drift_percent": round(mean_drift, 6),
            "std_drift_percent": round(std_drift, 6),
            "max_drift_percent": round(max(drifts), 6),
        },
        "tier_counts": tier_counts,
        "proposed_thresholds": propose_thresholds(drifts),
    }

    output_root.mkdir(parents=True, exist_ok=True)
    summary_path = output_root / "corpus_summary.json"
    with summary_path.open("w", encoding="utf-8") as fh:
        json.dump(corpus_summary, fh, indent=2)
        fh.write("\n")
    print(f"\nCorpus summary written to {summary_path}")


if __name__ == "__main__":
    main()
