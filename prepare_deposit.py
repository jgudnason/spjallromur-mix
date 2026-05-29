#!/usr/bin/env python3
"""
prepare_deposit.py — Assemble a complete CLARIN v2 deposit directory.

For each full_conversations session (48 total):
  - Copy CLARIN WAVs renamed to v2 stem (e.g. a_<sid>_20-29_m.wav)
  - Copy v2 forced-alignment JSON transcripts where available
  - Copy pipeline outputs: mixed WAV, aligned JSONs, merged transcript,
    session_params.json
  - For excluded session (198f2863): copy session_params.json only

For half_conversations, unaligned, splits: copy from transcript_root unchanged.

Top-level: copy README.md, LICENSE, evaluation_of_alignment.md.
Create metadata.tsv with per-speaker rows and 4 pipeline columns.
Create code/README.txt with pipeline archive instructions.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

EXCLUDED_SESSIONS = {"198f2863"}

# Directories under transcript_root copied to deposit unchanged.
PASSTHROUGH_DIRS = ("half_conversations", "unaligned", "splits", "combined", "segmented", "results", "src")

# Top-level files copied from transcript_root.
PASSTHROUGH_FILES = ("README.md", "LICENSE", "evaluation_of_alignment.md")

CODE_README_TEXT = (
    "Mixing pipeline: https://github.com/jgudnason/spjallromur-mix\n\n"
    "Archive this repository at release tag v1.0.0 and place the zip here before deposit.\n"
)

METADATA_COLUMNS = [
    "session_id",
    "speaker",
    "age",
    "gender",
    "duration_sec",
    "conversation_type",
    "has_mixed_audio",
    "transcript_source",
    "above_1pct_threshold",
    "audio_excluded",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_clarin_wav_pairs(corpus_root: Path, conv_type: str) -> dict[str, dict[str, Path]]:
    """Return {session_id: {speaker: wav_path}} for CLARIN sessions of the given type."""
    sessions: dict[str, dict[str, Path]] = {}
    for wav in corpus_root.rglob("speaker_*_convo_*.wav"):
        if conv_type not in wav.parts:
            continue
        parts = wav.name.split("_convo_")
        if len(parts) != 2:
            continue
        speaker = parts[0].replace("speaker_", "")
        sid = parts[1][:-4]
        if speaker in {"a", "b"}:
            sessions.setdefault(sid, {})[speaker] = wav
    return sessions


def find_v2_stems(transcript_root: Path, session_id: str) -> dict[str, str]:
    """Return {speaker: stem} for this session, searching all subdirs of transcript_root."""
    result: dict[str, str] = {}
    for p in transcript_root.rglob(f"*_{session_id}_*.json"):
        stem = p.stem  # e.g. "a_2a07b3a7_20-29_m"
        parts = stem.split("_")
        if len(parts) >= 4 and parts[0] in {"a", "b"} and parts[1] == session_id:
            result[parts[0]] = stem
    return result


def stem_age_gender(stem: str) -> tuple[str, str]:
    """Parse age and gender from a v2 stem like 'a_2a07b3a7_20-29_m'."""
    parts = stem.split("_")
    # parts: [speaker, session_id, age_range, gender_code]
    age = parts[2] if len(parts) >= 3 else ""
    gender = parts[3] if len(parts) >= 4 else ""
    return age, gender


def read_clarin_demographics(wav_path: Path, session_id: str, speaker: str) -> dict:
    demo_path = wav_path.parent / f"speaker_{speaker}_convo_{session_id}_demographics.json"
    if demo_path.exists():
        with demo_path.open(encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def read_session_params(output_root: Path, session_id: str) -> dict | None:
    p = output_root / session_id / "session_params.json"
    if p.exists():
        with p.open(encoding="utf-8") as fh:
            return json.load(fh)
    return None


def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dst))


def copy_dir_unchanged(src: Path, dst: Path) -> int:
    count = 0
    for f in src.rglob("*"):
        if f.is_file():
            rel = f.relative_to(src)
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(f), str(target))
            count += 1
    return count


def write_metadata_tsv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        fh.write("\t".join(METADATA_COLUMNS) + "\n")
        for row in rows:
            fh.write("\t".join(str(row.get(col, "")) for col in METADATA_COLUMNS) + "\n")


# ---------------------------------------------------------------------------
# Full conversations processing
# ---------------------------------------------------------------------------

def process_full_conversations(
    corpus_root: Path,
    transcript_root: Path,
    output_root: Path,
    deposit_root: Path,
) -> tuple[list[dict], dict]:
    """
    Process all CLARIN full_conversations sessions into the deposit.
    Returns (metadata_rows, stats).
    """
    clarin_pairs = find_clarin_wav_pairs(corpus_root, "full_conversations")
    metadata_rows: list[dict] = []
    stats = {
        "total": len(clarin_pairs),
        "with_mixed": 0,
        "without_mixed": 0,
        "excluded": 0,
        "warnings": [],
    }

    for session_id in sorted(clarin_pairs):
        session_dir = deposit_root / "full_conversations" / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        params = read_session_params(output_root, session_id)
        is_excluded = (
            session_id in EXCLUDED_SESSIONS
            or (params is not None and params.get("status") == "excluded")
        )

        # Always copy session_params.json if it exists.
        src_params = output_root / session_id / "session_params.json"
        if src_params.exists():
            copy_file(src_params, session_dir / "session_params.json")
        elif params is None:
            stats["warnings"].append(f"[{session_id}] no session_params.json found")

        if is_excluded:
            stats["excluded"] += 1
            _append_metadata_rows(
                metadata_rows, session_id, "full",
                clarin_pairs[session_id], transcript_root, params,
                has_mixed=False, audio_excluded=True,
            )
            continue

        # Discover v2 stems for WAV renaming and metadata.
        v2_stems = find_v2_stems(transcript_root, session_id)

        # Copy CLARIN WAVs renamed to v2 stem pattern.
        for spk, wav_path in sorted(clarin_pairs[session_id].items()):
            stem = v2_stems.get(spk)
            if stem:
                dst_name = f"{stem}.wav"
            else:
                dst_name = wav_path.name
                stats["warnings"].append(
                    f"[{session_id}/{spk}] no v2 stem found; kept CLARIN filename"
                )
            copy_file(wav_path, session_dir / dst_name)

        # Copy v2 JSON transcripts (full_conversations only).
        v2_fc_dir = transcript_root / "full_conversations" / session_id
        if v2_fc_dir.exists():
            for jf in sorted(v2_fc_dir.glob("*.json")):
                copy_file(jf, session_dir / jf.name)

        # Copy pipeline outputs (skip old CLARIN-style aligned filenames).
        out_dir = output_root / session_id
        if out_dir.exists():
            for f in sorted(out_dir.iterdir()):
                if not f.is_file():
                    continue
                name = f.name
                # Skip old CLARIN-format aligned files produced before the v2 rewrite.
                if name.startswith("speaker_") and name.endswith("_aligned.json"):
                    continue
                # session_params.json already copied above.
                if name == "session_params.json":
                    continue
                copy_file(f, session_dir / name)
        else:
            stats["warnings"].append(f"[{session_id}] output directory not found")

        has_mixed = (session_dir / f"{session_id}_mixed.wav").exists()
        if has_mixed:
            stats["with_mixed"] += 1
        else:
            stats["without_mixed"] += 1
            stats["warnings"].append(f"[{session_id}] no mixed WAV in deposit")

        _append_metadata_rows(
            metadata_rows, session_id, "full",
            clarin_pairs[session_id], transcript_root, params,
            has_mixed=has_mixed, audio_excluded=False,
        )

    return metadata_rows, stats


def _append_metadata_rows(
    rows: list[dict],
    session_id: str,
    conv_type: str,
    wav_map: dict[str, Path],
    transcript_root: Path,
    params: dict | None,
    has_mixed: bool,
    audio_excluded: bool,
) -> None:
    v2_stems = find_v2_stems(transcript_root, session_id)
    transcript_source = (params.get("transcript_source") or "") if params else ""
    above_1pct = params.get("above_1pct_threshold", False) if params else ""

    for spk in sorted(wav_map):
        stem = v2_stems.get(spk, "")
        age, gender = stem_age_gender(stem) if stem else ("", "")

        demo = read_clarin_demographics(wav_map[spk], session_id, spk)
        duration = demo.get("duration_seconds", "")

        rows.append({
            "session_id": session_id,
            "speaker": spk,
            "age": age,
            "gender": gender,
            "duration_sec": duration,
            "conversation_type": conv_type,
            "has_mixed_audio": has_mixed,
            "transcript_source": transcript_source,
            "above_1pct_threshold": above_1pct,
            "audio_excluded": audio_excluded,
        })


# ---------------------------------------------------------------------------
# Half conversations metadata
# ---------------------------------------------------------------------------

def half_conversations_metadata(
    corpus_root: Path,
    transcript_root: Path,
) -> list[dict]:
    """Build metadata rows for CLARIN half_conversations sessions."""
    clarin_pairs = find_clarin_wav_pairs(corpus_root, "half_conversations")
    rows: list[dict] = []
    for session_id in sorted(clarin_pairs):
        _append_metadata_rows(
            rows, session_id, "half",
            clarin_pairs[session_id], transcript_root,
            params=None, has_mixed=False, audio_excluded=False,
        )
    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Assemble a complete CLARIN v2 deposit directory."
    )
    parser.add_argument("--deposit-root", required=True, help="Where to write the deposit.")
    parser.add_argument("--corpus-root", required=True, help="CLARIN corpus root (WAV files).")
    parser.add_argument("--transcript-root", required=True, help="v2 transcript root (JSON files).")
    parser.add_argument("--output-root", required=True, help="Pipeline output root (from analyse + synthesise).")
    args = parser.parse_args()

    deposit_root = Path(args.deposit_root).expanduser().resolve()
    corpus_root = Path(args.corpus_root).expanduser().resolve()
    transcript_root = Path(args.transcript_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()

    for label, p in [
        ("deposit-root", deposit_root),
        ("corpus-root", corpus_root),
        ("transcript-root", transcript_root),
        ("output-root", output_root),
    ]:
        if label == "deposit-root":
            deposit_root.mkdir(parents=True, exist_ok=True)
        elif not p.exists() or not p.is_dir():
            print(f"Error: {label} not found or not a directory: {p}", file=sys.stderr)
            sys.exit(1)

    # 1. full_conversations
    print("Processing full_conversations...")
    fc_metadata, stats = process_full_conversations(
        corpus_root, transcript_root, output_root, deposit_root
    )

    # 2. Passthrough directories from transcript_root
    for dirname in PASSTHROUGH_DIRS:
        src = transcript_root / dirname
        if src.exists():
            dst = deposit_root / dirname
            n = copy_dir_unchanged(src, dst)
            print(f"Copied {dirname}/: {n} files")
        else:
            print(f"  (skipped {dirname}/: not found in transcript_root)")

    # 3. Top-level files from transcript_root
    for fname in PASSTHROUGH_FILES:
        src = transcript_root / fname
        if src.exists():
            copy_file(src, deposit_root / fname)
            print(f"Copied {fname}")
        else:
            print(f"  (skipped {fname}: not found)")

    # 4. Metadata TSV
    half_metadata = half_conversations_metadata(corpus_root, transcript_root)
    all_metadata = fc_metadata + half_metadata
    metadata_path = deposit_root / "metadata.tsv"
    write_metadata_tsv(metadata_path, all_metadata)
    print(f"Written metadata.tsv ({len(all_metadata)} rows)")

    # 5. code/README.txt
    code_dir = deposit_root / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    (code_dir / "README.txt").write_text(CODE_README_TEXT, encoding="utf-8")
    print("Written code/README.txt")

    # 6. Summary
    print()
    print(f"Summary:")
    print(f"  Total full_conversations sessions: {stats['total']}")
    print(f"  With mixed audio:                  {stats['with_mixed']}")
    print(f"  Without mixed audio:               {stats['without_mixed']}")
    print(f"  Excluded (no audio output):        {stats['excluded']}")
    if stats["warnings"]:
        print(f"  Warnings ({len(stats['warnings'])}):")
        for w in stats["warnings"]:
            print(f"    {w}")
    else:
        print("  No warnings.")


if __name__ == "__main__":
    main()
