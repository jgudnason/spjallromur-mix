#!/usr/bin/env python3
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

import soundfile as sf

# Sessions where the WAV header reports an incorrect sample rate.
# The pipeline does NOT correct for this — audio is read and mixed at face value.
# The discrepancy is documented in the quality_warning field of session_params.json.
SAMPLE_RATE_OVERRIDES: dict = {}

# Sessions flagged for known audio quality issues.  Processing continues normally;
# the warning is written into session_params.json for downstream pipelines.
QUALITY_WARNINGS = {
    "198f2863": (
        "WAV header reports 16000 Hz but audio was likely recorded at ~12000 Hz. "
        "The mixed audio will play back approximately 33% too fast and high-pitched. "
        "Recording also contains multiple voices and audio artefacts."
    ),
}


def propose_thresholds(drifts):
    """Propose tier boundaries from the drift distribution using the largest-gap method."""
    inspection_max = 5.0
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

    breaks = sorted(round((lo + hi) / 2, 3) for _, lo, hi in gaps[:2])
    tier_a_max = breaks[0]
    tier_b_max = breaks[1] if len(breaks) > 1 else inspection_max

    top_gap = gaps[0]
    second_gap = gaps[1] if len(gaps) > 1 else None
    rationale = (
        f"Tier A / Tier B boundaries at natural gaps in sorted drift distribution "
        f"(largest gap: {round(top_gap[0], 3)}% at {top_gap[1]:.3f}→{top_gap[2]:.3f}"
        + (
            f", second gap: {round(second_gap[0], 3)}% at {second_gap[1]:.3f}→{second_gap[2]:.3f}"
            if second_gap else ""
        )
        + f"). Informational only — all sessions are processed regardless of drift magnitude."
    )
    return {
        "tier_a_max": tier_a_max,
        "tier_b_max": tier_b_max,
        "inspection_max": inspection_max,
        "rationale": rationale,
    }


def find_session_pairs(corpus_root: Path) -> dict:
    """Find sessions with both WAV files under full_conversations/ in corpus_root."""
    sessions = {}
    for wav_path in corpus_root.rglob("speaker_*_convo_*.wav"):
        if "full_conversations" not in wav_path.parts:
            continue
        name = wav_path.name
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


def duration_seconds(wav_path: Path, session_id: str = None) -> float:
    """Return duration in seconds, applying sample-rate override if needed."""
    info = sf.info(str(wav_path))
    if info.samplerate <= 0:
        raise ValueError(f"Invalid samplerate in {wav_path}")
    actual_sr = SAMPLE_RATE_OVERRIDES.get(session_id, info.samplerate)
    if actual_sr != info.samplerate:
        print(
            f"  [{session_id}] Sample rate override: header={info.samplerate} Hz, "
            f"actual={actual_sr} Hz — duration corrected from "
            f"{info.frames/info.samplerate:.3f}s to {info.frames/actual_sr:.3f}s",
            file=sys.stderr,
        )
    return float(info.frames) / float(actual_sr)


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
    headers = ["session_id", "dur_a(s)", "dur_b(s)", "delta(s)", "drift(%)", "ref", ">1%"]
    widths = [max(len(str(row[i])) for row in rows + [headers]) for i in range(len(headers))]
    line = "  ".join(header.ljust(widths[i]) for i, header in enumerate(headers))
    print(line)
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        print("  ".join(str(row[i]).ljust(widths[i]) for i in range(len(headers))))


def main():
    parser = argparse.ArgumentParser(
        description="Stage 1 analysis: measure session durations and create parameter stubs."
    )
    parser.add_argument("--corpus-root", required=True, help="Path to the CLARIN corpus root (contains WAV files).")
    parser.add_argument("--transcript-root", default=None, help="Path to the v2 transcript root (for documentation; not used for filtering).")
    parser.add_argument("--output-root", required=True, help="Path to the output root where session parameter files are written.")
    args = parser.parse_args()

    corpus_root = Path(args.corpus_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()

    if not corpus_root.exists() or not corpus_root.is_dir():
        print(f"Error: corpus root not found or not a directory: {corpus_root}", file=sys.stderr)
        sys.exit(1)

    if args.transcript_root:
        transcript_root = Path(args.transcript_root).expanduser().resolve()
        if not transcript_root.exists() or not transcript_root.is_dir():
            print(f"Error: transcript root not found or not a directory: {transcript_root}", file=sys.stderr)
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
        duration_a = duration_seconds(path_a, session_id)
        duration_b = duration_seconds(path_b, session_id)
        delta = abs(duration_a - duration_b)
        reference_channel = "a" if duration_a >= duration_b else "b"
        reference_duration = max(duration_a, duration_b)
        drift_percent = (delta / reference_duration) * 100 if reference_duration > 0 else 0.0
        above_1pct = drift_percent > 1.0

        params = {
            "session_id": session_id,
            "duration_a_sec": round(duration_a, 6),
            "duration_b_sec": round(duration_b, 6),
            "delta_sec": round(delta, 6),
            "drift_percent": round(drift_percent, 6),
            "above_1pct_threshold": above_1pct,
            "reference_channel": reference_channel,
            "tier": "A",
            "resample_ratio": None,
            "transcript_source": None,
            "pipeline_version": "1.0.0",
            "stage1_timestamp": timestamp,
        }
        if session_id in QUALITY_WARNINGS:
            params["quality_warning"] = QUALITY_WARNINGS[session_id]

        output_dir = output_root / session_id
        write_session_params(output_dir, session_id, params)

        session_records.append({
            "session_id": session_id,
            "duration_a_sec": params["duration_a_sec"],
            "duration_b_sec": params["duration_b_sec"],
            "delta_sec": params["delta_sec"],
            "drift_percent": params["drift_percent"],
            "above_1pct_threshold": above_1pct,
            "tier": params["tier"],
        })

        summary_rows.append([
            session_id,
            format_seconds(duration_a),
            format_seconds(duration_b),
            format_seconds(delta),
            f"{drift_percent:.3f}",
            reference_channel,
            "YES" if above_1pct else "",
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

    corpus_summary = {
        "pipeline_version": "1.0.0",
        "timestamp": timestamp,
        "sessions": session_records,
        "aggregate_stats": {
            "count": n,
            "mean_drift_percent": round(mean_drift, 6),
            "std_drift_percent": round(std_drift, 6),
            "max_drift_percent": round(max(drifts), 6),
            "sessions_above_1pct": sum(1 for r in session_records if r["above_1pct_threshold"]),
        },
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
