#!/usr/bin/env python3
"""
corpus_stats_table.py — Compute Spjallrómur corpus statistics and emit a LaTeX table.

Usage:
    python stats/corpus_stats_table.py \
        --corpus-root /path/to/clarin/spjallromur \
        --transcript-root /path/to/spjallromur-v2 \
        --output-root /path/to/pipeline/output

The script computes everything it can from the data files.  The transcription
coverage block (manual annotation counts) cannot be derived from the corpus
files; edit the MANUAL_ANNOTATION constants near the top of this file before
generating the final table.
"""

import argparse
import json
from pathlib import Path
from collections import Counter

# ---------------------------------------------------------------------------
# Manual annotation constants — edit these before generating the final table.
# ---------------------------------------------------------------------------
MANUALLY_TRANSCRIBED_SESSIONS = None        # e.g. 21
TRIPLE_ANNOTATOR_SESSIONS     = None        # e.g. 7
DUAL_ANNOTATOR_SESSIONS       = None        # e.g. 14
TOTAL_MANUAL_TRANSCRIPTION_H  = None        # e.g. 21.0  (hours)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clarin_session_dirs(corpus_root: Path, conv_type: str) -> list[Path]:
    """Return sorted list of session directories for a CLARIN conversation type."""
    base = corpus_root / "spjallromur" / "data" / conv_type
    if not base.exists():
        # Try to find it via rglob
        hits = [p for p in corpus_root.rglob(conv_type) if p.is_dir()]
        base = hits[0] if hits else None
    if base is None or not base.exists():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir())


def clarin_speaker_count(corpus_root: Path, conv_type: str) -> int:
    """Count individual speaker recordings (demographics files) of the given type."""
    return sum(
        1 for _ in corpus_root.rglob("speaker_*_convo_*_demographics.json")
        if conv_type in _.parts
    )


def v2_session_dirs(transcript_root: Path, conv_type: str) -> list[Path]:
    base = transcript_root / conv_type
    if not base.exists():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir())


def v2_speaker_file_count(transcript_root: Path, conv_type: str) -> int:
    """Count JSON files in a v2 directory, matching only {a|b}_{session_id}_* pattern."""
    base = transcript_root / conv_type
    if not base.exists():
        return 0
    count = 0
    for session_dir in base.iterdir():
        if not session_dir.is_dir():
            continue
        sid = session_dir.name
        for jf in session_dir.glob("*.json"):
            parts = jf.stem.split("_")
            if len(parts) >= 2 and parts[0] in {"a", "b"} and parts[1] == sid:
                count += 1
    return count


def find_v2_stem(transcript_root: Path, session_id: str, speaker: str) -> str | None:
    """Find the v2 stem for a given speaker/session anywhere in transcript_root."""
    for p in transcript_root.rglob(f"{speaker}_{session_id}_*.json"):
        parts = p.stem.split("_")
        if len(parts) >= 4 and parts[0] == speaker and parts[1] == session_id:
            return p.stem
    return None


def age_gender_from_stem(stem: str) -> tuple[str, str]:
    """Parse (age_range, gender_code) from e.g. 'a_2a07b3a7_20-29_m'."""
    parts = stem.split("_")
    age    = parts[2] if len(parts) >= 3 else ""
    gender = parts[3] if len(parts) >= 4 else ""
    return age, gender


def read_session_params(output_root: Path, session_id: str) -> dict | None:
    p = output_root / session_id / "session_params.json"
    return json.loads(p.read_text()) if p.exists() else None


def fmt_num(value, *, precision: int = 1, unit: str = "") -> str:
    if value is None:
        return r"\NUM{?}"
    s = f"{value:.{precision}f}"
    return f"{s}{unit}" if unit else s


def pct(n: int, total: int) -> str:
    return f"{round(n / total * 100)}\\%"


def latex_num(value) -> str:
    return r"\NUM{?}" if value is None else str(value)


# ---------------------------------------------------------------------------
# Main computation
# ---------------------------------------------------------------------------

def compute_stats(corpus_root: Path, transcript_root: Path, output_root: Path) -> dict:
    stats = {}

    # --- Full corpus (CLARIN) ---
    clarin_full_dirs = clarin_session_dirs(corpus_root, "full_conversations")
    clarin_half_dirs = clarin_session_dirs(corpus_root, "half_conversations")
    stats["clarin_full_sessions"]  = len(clarin_full_dirs)
    stats["clarin_half_sessions"]  = len(clarin_half_dirs)
    stats["clarin_total_sessions"] = len(clarin_full_dirs) + len(clarin_half_dirs)

    stats["clarin_spk_full"] = clarin_speaker_count(corpus_root, "full_conversations")
    stats["clarin_spk_half"] = clarin_speaker_count(corpus_root, "half_conversations")
    stats["clarin_total_spk"] = stats["clarin_spk_full"] + stats["clarin_spk_half"]

    # --- v2 release categories ---
    stats["v2_full_sessions"]      = len(v2_session_dirs(transcript_root, "full_conversations"))
    stats["v2_half_sessions"]      = len(v2_session_dirs(transcript_root, "half_conversations"))
    stats["v2_unaligned_sessions"] = len(v2_session_dirs(transcript_root, "unaligned"))
    # Unaligned speaker transcripts (3 JSON files across 2 session dirs)
    stats["v2_unaligned_spk_files"] = v2_speaker_file_count(transcript_root, "unaligned")

    # Excluded = sessions with status=excluded in session_params
    excluded_ids = set()
    for params_path in output_root.rglob("session_params.json"):
        params = json.loads(params_path.read_text())
        if params.get("status") == "excluded":
            excluded_ids.add(params["session_id"])
    stats["excluded_sessions"] = len(excluded_ids)

    # --- Processed (mixed) subset ---
    processed_sessions = []
    for params_path in sorted(output_root.rglob("session_params.json")):
        params = json.loads(params_path.read_text())
        if params.get("status") != "excluded" and "duration_a_sec" in params:
            processed_sessions.append(params)

    stats["processed_sessions"] = len(processed_sessions)

    durations = [max(p["duration_a_sec"], p["duration_b_sec"]) for p in processed_sessions]
    stats["total_duration_h"]  = sum(durations) / 3600
    stats["mean_duration_min"] = (sum(durations) / len(durations)) / 60 if durations else 0
    stats["min_duration_min"]  = min(durations) / 60 if durations else 0
    stats["max_duration_min"]  = max(durations) / 60 if durations else 0

    # --- Speaker demographics from processed sessions ---
    # Use find_v2_stem across the full transcript_root to handle CLARIN-fallback
    # sessions (e.g. 2a139f9b whose stems are in half_conversations/ and unaligned/).
    # Search each session_id once per speaker to avoid counting stray misplaced files.
    age_counter    = Counter()
    gender_counter = Counter()
    missing_stems  = []
    total_spk      = 0

    clarin_full_ids = {d.name for d in clarin_full_dirs}
    processed_ids   = {p["session_id"] for p in processed_sessions}

    for session_id in sorted(processed_ids & clarin_full_ids):
        for spk in ("a", "b"):
            total_spk += 1
            stem = find_v2_stem(transcript_root, session_id, spk)
            if stem:
                age, gender = age_gender_from_stem(stem)
                age_counter[age]       += 1
                gender_counter[gender] += 1
            else:
                missing_stems.append(f"{session_id}/{spk}")

    stats["total_spk_processed"] = total_spk
    stats["missing_stems"]       = missing_stems
    stats["gender"] = dict(gender_counter)
    stats["age"]    = dict(age_counter)

    # Age buckets for the table.  18-19 is merged into the 18-29 bucket.
    def age_lower(age_str: str) -> int:
        return int(age_str.split("-")[0].rstrip("+"))

    stats["age_18_29"] = sum(n for a, n in age_counter.items() if age and age_lower(a) < 30)
    stats["age_30_39"] = sum(n for a, n in age_counter.items() if age and 30 <= age_lower(a) <= 39)
    stats["age_40_49"] = sum(n for a, n in age_counter.items() if age and 40 <= age_lower(a) <= 49)
    stats["age_50plus"] = sum(n for a, n in age_counter.items() if age and age_lower(a) >= 50)

    return stats


# ---------------------------------------------------------------------------
# LaTeX table generation
# ---------------------------------------------------------------------------

def generate_table(s: dict) -> str:
    n_spk = s["total_spk_processed"]

    # Gender
    n_m = s["gender"].get("m", 0)
    n_f = s["gender"].get("f", 0)
    n_o = s["gender"].get("o", 0)

    # Age
    n_18 = s["age_18_29"]
    n_30 = s["age_30_39"]
    n_40 = s["age_40_49"]
    n_50 = s["age_50plus"]

    # Duration
    total_h   = fmt_num(s["total_duration_h"],  precision=1, unit=" h")
    mean_min  = fmt_num(s["mean_duration_min"],  precision=1, unit=" min")
    range_min = (
        f"{fmt_num(s['min_duration_min'], precision=1)}"
        f"--{fmt_num(s['max_duration_min'], precision=1)} min"
    )

    # Transcription coverage
    mt_sess  = latex_num(MANUALLY_TRANSCRIBED_SESSIONS)
    triple   = latex_num(TRIPLE_ANNOTATOR_SESSIONS)
    dual     = latex_num(DUAL_ANNOTATOR_SESSIONS)
    mt_hours = latex_num(TOTAL_MANUAL_TRANSCRIPTION_H)

    lines = [
        r"\begin{table}[t]",
        r"  \caption{Spjallr\'{o}mur corpus statistics.}",
        r"  \label{tab:corpus-stats}",
        r"  \centering",
        r"  \begin{tabular}{lr}",
        r"    \toprule",
        r"    Property & Value \\",
        r"    \midrule",
        r"    \multicolumn{2}{l}{\textit{Full corpus (CLARIN v2 release)}} \\",
        f"    Total recorded conversations      & {s['clarin_total_sessions']} \\\\",
        f"    \\quad Full conversations (both channels) & {s['clarin_full_sessions']} \\\\",
        f"    \\quad Half conversations (one channel)   & {s['clarin_half_sessions']} \\\\",
        f"    \\quad Excluded (bad audio)               & {s['excluded_sessions']} \\\\",
        f"    Total speaker recordings          & {s['clarin_total_spk']} \\\\",
        r"    \midrule",
        r"    \multicolumn{2}{l}{\textit{Dual-channel mixed subset (this work)}} \\",
        f"    Sessions processed                & {s['processed_sessions']} \\\\",
        f"    Total duration                    & {total_h} \\\\",
        f"    Session duration: mean            & {mean_min} \\\\",
        f"    Session duration: range           & {range_min} \\\\",
        f"    Speakers (full conversations)     & {n_spk} \\\\",
        f"    \\quad Male                        & {n_m} ({pct(n_m, n_spk)}) \\\\",
        f"    \\quad Female                      & {n_f} ({pct(n_f, n_spk)}) \\\\",
        f"    \\quad Other                       & {n_o} ({pct(n_o, n_spk)}) \\\\",
        f"    Age 18--29                        & {n_18} ({pct(n_18, n_spk)}) \\\\",
        f"    Age 30--39                        & {n_30} ({pct(n_30, n_spk)}) \\\\",
        f"    Age 40--49                        & {n_40} ({pct(n_40, n_spk)}) \\\\",
        f"    Age 50+                           & {n_50} ({pct(n_50, n_spk)}) \\\\",
        r"    \midrule",
        r"    \multicolumn{2}{l}{\textit{Transcription coverage}} \\",
        f"    Tiro semi-automatic transcripts   & All {s['clarin_total_sessions']} sessions \\\\",
        f"    Manually transcribed sessions     & {mt_sess} \\\\",
        f"    \\quad Triple annotator coverage   & {triple} \\\\",
        f"    \\quad Dual annotator coverage     & {dual} \\\\",
        f"    Total manual transcription        & {mt_hours} h \\\\",
        r"    \bottomrule",
        r"  \end{tabular}",
        r"\end{table}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate LaTeX corpus statistics table.")
    parser.add_argument("--corpus-root",     required=True, help="CLARIN corpus root.")
    parser.add_argument("--transcript-root", required=True, help="v2 transcript root.")
    parser.add_argument("--output-root",     required=True, help="Pipeline output root.")
    args = parser.parse_args()

    corpus_root     = Path(args.corpus_root).expanduser().resolve()
    transcript_root = Path(args.transcript_root).expanduser().resolve()
    output_root     = Path(args.output_root).expanduser().resolve()

    s = compute_stats(corpus_root, transcript_root, output_root)

    # Diagnostics
    print("=== Computed values ===")
    for k, v in s.items():
        if k not in ("gender", "age", "missing_stems"):
            print(f"  {k}: {v}")
    print(f"  gender: {s['gender']}")
    print(f"  age:    {s['age']}")
    if s["missing_stems"]:
        print(f"  WARNING — no v2 stem found for: {s['missing_stems']}")

    print("\n=== LaTeX table ===\n")
    print(generate_table(s))


if __name__ == "__main__":
    main()
