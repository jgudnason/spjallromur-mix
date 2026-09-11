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
    valid = [
        w for w in words if w.get("start") is not None and w.get("end") is not None
    ]
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
            turns.append(
                {
                    "speaker": spk,
                    "start": t_start,
                    "end": t_end,
                    "duration": t_end - t_start,
                    "word_count": count,
                }
            )
            spk = w["speaker"]
            t_start = w["start"]
            t_end = w["end"]
            count = 1

    turns.append(
        {
            "speaker": spk,
            "start": t_start,
            "end": t_end,
            "duration": t_end - t_start,
            "word_count": count,
        }
    )
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
            total += max(
                0.0,
                min(a["end"], b_turns[k]["end"]) - max(a["start"], b_turns[k]["start"]),
            )
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
    overlap_pct = (
        100.0 * overlap_sec / recording_duration if recording_duration > 0 else 0.0
    )

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

    # Floor share: total speaking time per speaker as % of session duration.
    # Does not sum to 100%: overlap intervals count for both speakers and
    # silence intervals count for neither — this is correct and expected.
    floor_pct_a = (
        100.0 * sum(t["duration"] for t in a_turns) / recording_duration
        if recording_duration > 0
        else 0.0
    )
    floor_pct_b = (
        100.0 * sum(t["duration"] for t in b_turns) / recording_duration
        if recording_duration > 0
        else 0.0
    )
    floor_balance = abs(floor_pct_a - floor_pct_b)

    turns_per_min = (
        n_turns / (recording_duration / 60.0) if recording_duration > 0 else 0.0
    )

    return {
        "session_id": session_id,
        "recording_duration": recording_duration,
        "n_turns": n_turns,
        "n_turns_a": len(a_turns),
        "n_turns_b": len(b_turns),
        "turns_per_min": turns_per_min,
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
        "floor_pct_a": floor_pct_a,
        "floor_pct_b": floor_pct_b,
        "floor_balance": floor_balance,
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


def print_summary(agg, n_sessions, outlier_note=None):
    print(
        f"\nSpjallrómur Conversational Turn-Taking Analysis  ({n_sessions} sessions)\n"
    )
    print(f"{'Statistic':<38} {'Mean ± Std':>22}  {'Min':>10}  {'Max':>10}")
    print("-" * 86)

    rows = [
        ("Recording duration (s)", "recording_duration", ".1f"),
        ("Turns per session", "n_turns", ".1f"),
        ("  Speaker A", "n_turns_a", ".1f"),
        ("  Speaker B", "n_turns_b", ".1f"),
        ("Turns per minute", "turns_per_min", ".1f"),
        ("Mean turn duration (s)", "mean_turn_duration_s", ".2f"),
        ("Median turn duration (s)", "median_turn_duration_s", ".2f"),
        ("Max turn duration (s)", "max_turn_duration_s", ".1f"),
        ("Speaker overlap (s)", "overlap_sec", ".1f"),
        ("Speaker overlap (%)", "overlap_pct", ".1f"),
        ("Mean inter-turn gap (s)", "mean_inter_turn_gap_s", ".2f"),
        ("Median inter-turn gap (s)", "median_inter_turn_gap_s", ".2f"),
        ("Overlapping transitions", "n_overlapping_transitions", ".1f"),
        ("Backchannel proxy (count)", "backchannel_count", ".1f"),
        ("Backchannel proxy (%)", "backchannel_pct", ".1f"),
        ("Longest monologue (s)", "longest_monologue_s", ".1f"),
        ("Floor share — Speaker A (%)", "floor_pct_a", ".1f"),
        ("Floor share — Speaker B (%)", "floor_pct_b", ".1f"),
        ("Floor balance |A−B| (%)", "floor_balance", ".1f"),
    ]

    for label, key, fmt in rows:
        a = agg[key]
        mean_std = f"{a['mean']:{fmt}} ± {a['std']:{fmt}}"
        lo = f"{a['min']:{fmt}}"
        hi = f"{a['max']:{fmt}}"
        print(f"{label:<38} {mean_std:>22}  {lo:>10}  {hi:>10}")

    print()

    if outlier_note:
        n = outlier_note
        dur_min = n["dur_s"] / 60
        print(
            f"NOTE: session {n['session_id']} contains a {n['dur_s']:.1f} s ({dur_min:.1f} min) turn —\n"
            f"      the longest in the corpus in absolute terms, but {n['dur_pct']:.1f}% of session\n"
            f"      duration. turns_per_min for this session: {n['turns_per_min']:.1f}"
            f" (corpus mean: {n['corpus_mean_tpm']:.1f}).\n"
            f"      Excluding {n['session_id']}: longest_turn mean={n['excl_mean']:.1f} s,"
            f" std={n['excl_std']:.1f} s."
        )
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
            writer.writerow(
                {k: (f"{v:.4f}" if isinstance(v, float) else v) for k, v in row.items()}
            )
    print(f"Per-session CSV written: {out_path}")


# ---------------------------------------------------------------------------
# Output: LaTeX table
# ---------------------------------------------------------------------------


def _latex_row(label, a, unit=""):
    """Format one data row: label & mean ± std unit & min–max unit."""

    def fmt(v):
        return f"{v:.1f}"

    mean_std = f"${fmt(a['mean'])} \\pm {fmt(a['std'])}${unit}"
    rng = f"${fmt(a['min'])}$--${fmt(a['max'])}${unit}"
    return f"    {label} & {mean_std} & {rng} \\\\"


def write_latex(agg, n_sessions, out_path):
    s = "~s"
    pct = r"~\%"

    data_rows = [
        _latex_row("Turns per minute", agg["turns_per_min"]),
        _latex_row("Mean turn duration", agg["mean_turn_duration_s"], s),
        _latex_row("Median turn duration", agg["median_turn_duration_s"], s),
        _latex_row("Speaker overlap", agg["overlap_pct"], pct),
        _latex_row("Mean inter-turn gap", agg["mean_inter_turn_gap_s"], s),
        _latex_row(
            "Backchannel-length turns (\\% of all turns)", agg["backchannel_pct"], pct
        ),
        _latex_row("Longest turn", agg["longest_monologue_s"], s),
        _latex_row("Floor share speaker~A", agg["floor_pct_a"], pct),
        _latex_row("Floor share speaker~B", agg["floor_pct_b"], pct),
        _latex_row("Floor balance $|A - B|$", agg["floor_balance"], pct),
    ]

    lines = (
        [
            "% Spjallrómur conversational turn-taking statistics",
            f"% {n_sessions} sessions (198f2863 excluded: quality_warning flag)",
            "% Generated by stats/conversational_analysis.py",
            r"\begin{table}[t]",
            r"  \caption{Conversational dynamics of Spjallr\'{o}mur ("
            + str(n_sessions)
            + r" sessions).",
            r"           Statistics computed from forced-alignment transcripts",
            r"           (94.6\% segment-level accuracy). Backchannel-length turns",
            r"           are defined as turns under 1.0~s duration.}",
            r"  \label{tab:conv-stats}",
            r"  \centering",
            r"  \begin{tabular}{lcc}",
            r"    \toprule",
            r"    Statistic & Mean $\pm$ Std & Range \\",
            r"    \midrule",
        ]
        + data_rows
        + [
            r"    \bottomrule",
            r"  \end{tabular}",
            r"\end{table}",
            "",
        ]
    )

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
        "--output-root",
        default="output",
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

    # Outlier note for deb42548 (longest turn in corpus)
    OUTLIER_SESSION = "deb42548"
    outlier_stats = next(
        (s for s in all_stats if s["session_id"] == OUTLIER_SESSION), None
    )
    outlier_note = None
    if outlier_stats is not None:
        excl_vals = [
            s["longest_monologue_s"]
            for s in all_stats
            if s["session_id"] != OUTLIER_SESSION
        ]
        outlier_note = {
            "session_id": OUTLIER_SESSION,
            "dur_s": outlier_stats["longest_monologue_s"],
            "dur_pct": (
                outlier_stats["longest_monologue_s"]
                / outlier_stats["recording_duration"]
                * 100
                if outlier_stats["recording_duration"] > 0
                else 0.0
            ),
            "turns_per_min": outlier_stats["turns_per_min"],
            "corpus_mean_tpm": agg["turns_per_min"]["mean"],
            "excl_mean": _mean(excl_vals),
            "excl_std": _std(excl_vals),
        }

    print_summary(agg, len(all_stats), outlier_note=outlier_note)
    write_csv(all_stats, stats_dir / "conversational_stats.csv")
    write_latex(agg, len(all_stats), stats_dir / "conversational_stats_table.tex")


if __name__ == "__main__":
    main()
