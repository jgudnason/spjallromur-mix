#!/usr/bin/env python3
# stats/conversational_analysis.py
#
# Conversational turn-taking analysis of the Spjallrómur corpus.
# All statistics are computed from forced-alignment transcripts with 94.6%
# segment-level accuracy; small timing errors may affect overlap and gap estimates.
#
# Usage (from repo root):
#   python stats/conversational_analysis.py --output-root output

import argparse
import csv
import json
import math
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Turn extraction
# ---------------------------------------------------------------------------

def words_to_turns(words):
    """Group consecutive same-speaker words (sorted by start time) into turns.

    Returns list of dicts: {speaker, start, end, duration, word_count}.
    Words with null timestamps are skipped.
    """
    valid = [w for w in words if w.get("start") is not None and w.get("end") is not None]
    if not valid:
        return []

    turns = []
    spk = valid[0]["speaker"]
    t_start = valid[0]["start"]
    t_end = valid[0]["end"]
    count = 1

    for w in valid[1:]:
        if w["speaker"] == spk:
            t_end = max(t_end, w["end"])
            count += 1
        else:
            turns.append({"speaker": spk, "start": t_start, "end": t_end,
                          "duration": t_end - t_start, "word_count": count})
            spk = w["speaker"]
            t_start = w["start"]
            t_end = w["end"]
            count = 1

    turns.append({"speaker": spk, "start": t_start, "end": t_end,
                  "duration": t_end - t_start, "word_count": count})
    return turns


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------

def _mean(vals):
    return sum(vals) / len(vals) if vals else 0.0


def _std(vals):
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))


def _median(vals):
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    return (s[n // 2 - 1] + s[n // 2]) / 2.0 if n % 2 == 0 else float(s[n // 2])


# ---------------------------------------------------------------------------
# Overlap calculation
# ---------------------------------------------------------------------------

def compute_overlap(a_turns, b_turns):
    """Total seconds where speaker A and speaker B are simultaneously active.

    Uses interval intersection: overlap([a_s, a_e], [b_s, b_e]) =
    max(0, min(a_e, b_e) - max(a_s, b_s)).
    Both input lists must be sorted by start time.
    """
    total = 0.0
    j = 0
    for a in a_turns:
        # Advance j past turns that end before a starts
        while j < len(b_turns) and b_turns[j]["end"] <= a["start"]:
            j += 1
        k = j
        while k < len(b_turns) and b_turns[k]["start"] < a["end"]:
            total += max(0.0, min(a["end"], b_turns[k]["end"]) - max(a["start"], b_turns[k]["start"]))
            k += 1
    return total


# ---------------------------------------------------------------------------
# Per-session analysis
# ---------------------------------------------------------------------------

def analyse_session(session_id, merged):
    """Compute turn-taking statistics for one session.

    Returns a flat dict of statistics, or None if the session has no valid turns.
    """
    words = merged.get("words", [])
    turns = words_to_turns(words)
    recording_duration = merged.get("recording_duration", 0.0)

    if not turns:
        return None

    a_turns = [t for t in turns if t["speaker"] == "a"]
    b_turns = [t for t in turns if t["speaker"] == "b"]

    n_turns = len(turns)
    durations = [t["duration"] for t in turns]

    # Speaker overlap
    overlap_sec = compute_overlap(a_turns, b_turns)
    overlap_pct = 100.0 * overlap_sec / recording_duration if recording_duration > 0 else 0.0

    # Inter-turn gaps: gap = next_turn.start - prev_turn.end
    # Positive = silence; negative = overlap between adjacent turns
    positive_gaps = []
    negative_gaps = []
    for i in range(1, len(turns)):
        gap = turns[i]["start"] - turns[i - 1]["end"]
        (positive_gaps if gap >= 0.0 else negative_gaps).append(gap)

    # Backchannel proxy: turns under 1.0 s
    # Note: turns shorter than 0.1 s may include alignment artefacts that
    # slightly inflate this count.
    backchannel_count = sum(1 for d in durations if d < 1.0)

    return {
        "session_id": session_id,
        "recording_duration": recording_duration,
        "n_turns": n_turns,
        "n_turns_a": len(a_turns),
        "n_turns_b": len(b_turns),
        "mean_turn_duration_s": _mean(durations),
        "median_turn_duration_s": _median(durations),
        "max_turn_duration_s": max(durations),
        "overlap_sec": overlap_sec,
        "overlap_pct": overlap_pct,
        "mean_inter_turn_gap_s": _mean(positive_gaps),
        "median_inter_turn_gap_s": _median(positive_gaps),
        "n_overlapping_transitions": len(negative_gaps),
        "backchannel_count": backchannel_count,
        "backchannel_pct": 100.0 * backchannel_count / n_turns if n_turns > 0 else 0.0,
        "longest_monologue_s": max(durations),
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate(all_stats):
    agg = {}
    numeric_keys = [k for k in all_stats[0] if k != "session_id"]
    for key in numeric_keys:
        vals = [s[key] for s in all_stats]
        agg[key] = {
            "mean": _mean(vals),
            "std": _std(vals),
            "min": min(vals),
            "max": max(vals),
            "median": _median(vals),
        }
    return agg


# ---------------------------------------------------------------------------
# Output: stdout summary
# ---------------------------------------------------------------------------

def print_summary(agg, n_sessions):
    print(f"\nSpjallrómur Conversational Turn-Taking Analysis  ({n_sessions} sessions)\n")
    print(f"{'Statistic':<38} {'Mean ± Std':>22}  {'Min':>10}  {'Max':>10}")
    print("-" * 86)

    rows = [
        ("Recording duration (s)",        "recording_duration",        ".1f"),
        ("Turns per session",              "n_turns",                   ".1f"),
        ("  Speaker A",                    "n_turns_a",                 ".1f"),
        ("  Speaker B",                    "n_turns_b",                 ".1f"),
        ("Mean turn duration (s)",         "mean_turn_duration_s",      ".2f"),
        ("Median turn duration (s)",       "median_turn_duration_s",    ".2f"),
        ("Max turn duration (s)",          "max_turn_duration_s",       ".1f"),
        ("Speaker overlap (s)",            "overlap_sec",               ".1f"),
        ("Speaker overlap (%)",            "overlap_pct",               ".1f"),
        ("Mean inter-turn gap (s)",        "mean_inter_turn_gap_s",     ".2f"),
        ("Median inter-turn gap (s)",      "median_inter_turn_gap_s",   ".2f"),
        ("Overlapping transitions",        "n_overlapping_transitions", ".1f"),
        ("Backchannel proxy (count)",      "backchannel_count",         ".1f"),
        ("Backchannel proxy (%)",          "backchannel_pct",           ".1f"),
        ("Longest monologue (s)",          "longest_monologue_s",       ".1f"),
    ]

    for label, key, fmt in rows:
        a = agg[key]
        mean_std = f"{a['mean']:{fmt}} ± {a['std']:{fmt}}"
        lo = f"{a['min']:{fmt}}"
        hi = f"{a['max']:{fmt}}"
        print(f"{label:<38} {mean_std:>22}  {lo:>10}  {hi:>10}")

    print()


# ---------------------------------------------------------------------------
# Output: per-session CSV
# ---------------------------------------------------------------------------

def write_csv(all_stats, out_path):
    fieldnames = list(all_stats[0].keys())
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in all_stats:
            writer.writerow({
                k: (f"{v:.4f}" if isinstance(v, float) else v)
                for k, v in row.items()
            })
    print(f"Per-session CSV written: {out_path}")


# ---------------------------------------------------------------------------
# Output: LaTeX table
# ---------------------------------------------------------------------------

def write_latex(agg, n_sessions, out_path):
    rows = [
        ("Turns per session",            "n_turns",                   "0"),
        ("\\quad Speaker A",             "n_turns_a",                 "0"),
        ("\\quad Speaker B",             "n_turns_b",                 "0"),
        ("Mean turn duration (s)",       "mean_turn_duration_s",      "2"),
        ("Median turn duration (s)",     "median_turn_duration_s",    "2"),
        ("Speaker overlap (s)",          "overlap_sec",               "1"),
        ("Speaker overlap (\\%)",        "overlap_pct",               "1"),
        ("Mean inter-turn gap (s)",      "mean_inter_turn_gap_s",     "2"),
        ("Median inter-turn gap (s)",    "median_inter_turn_gap_s",   "2"),
        ("Backchannel proxy (\\%)",      "backchannel_pct",           "1"),
        ("Longest monologue (s)",        "longest_monologue_s",       "1"),
    ]

    lines = [
        "% Spjallrómur conversational turn-taking statistics",
        f"% {n_sessions} sessions (198f2863 excluded: quality_warning flag)",
        "% Generated by stats/conversational_analysis.py",
        r"\begin{tabular}{lcc}",
        r"\hline",
        r"Statistic & Mean $\pm$ Std & Range \\",
        r"\hline",
    ]

    for label, key, fmt in rows:
        a = agg[key]
        if fmt == "0":
            mean_std = f"${a['mean']:.0f} \\pm {a['std']:.0f}$"
            rng = f"${a['min']:.0f}$--${a['max']:.0f}$"
        else:
            p = int(fmt)
            mean_std = f"${a['mean']:.{p}f} \\pm {a['std']:.{p}f}$"
            rng = f"${a['min']:.{p}f}$--${a['max']:.{p}f}$"
        lines.append(f"{label} & {mean_std} & {rng} \\\\")

    lines += [r"\hline", r"\end{tabular}", ""]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"LaTeX table written:     {out_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Conversational turn-taking analysis of Spjallrómur."
    )
    parser.add_argument(
        "--output-root", default="output",
        help="Pipeline output root (default: output).",
    )
    args = parser.parse_args()

    output_root = Path(args.output_root).expanduser().resolve()
    stats_dir = Path(__file__).parent.resolve()

    if not output_root.is_dir():
        print(f"Error: output-root not found: {output_root}", file=sys.stderr)
        sys.exit(1)

    session_dirs = sorted(p for p in output_root.iterdir() if p.is_dir())

    all_stats = []
    skipped = []

    for session_dir in session_dirs:
        session_id = session_dir.name

        params_path = session_dir / "session_params.json"
        if params_path.exists():
            with params_path.open(encoding="utf-8") as fh:
                params = json.load(fh)
            if params.get("quality_warning"):
                skipped.append(f"{session_id} (quality_warning)")
                continue

        merged_path = session_dir / f"{session_id}_transcript_merged.json"
        if not merged_path.exists():
            skipped.append(f"{session_id} (no merged transcript)")
            continue

        with merged_path.open(encoding="utf-8") as fh:
            merged = json.load(fh)

        stats = analyse_session(session_id, merged)
        if stats is None:
            skipped.append(f"{session_id} (no valid turns)")
            continue

        all_stats.append(stats)

    if skipped:
        print(f"Skipped ({len(skipped)}): {', '.join(skipped)}", file=sys.stderr)

    if not all_stats:
        print("No sessions to analyse.", file=sys.stderr)
        sys.exit(1)

    agg = aggregate(all_stats)

    print_summary(agg, len(all_stats))
    write_csv(all_stats, stats_dir / "conversational_stats.csv")
    write_latex(agg, len(all_stats), stats_dir / "conversational_stats_table.tex")


if __name__ == "__main__":
    main()
