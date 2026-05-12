#!/usr/bin/env python3
import argparse
import copy
import json
from pathlib import Path
import sys

import librosa
import numpy as np
import soundfile as sf


def find_wav(corpus_root: Path, session_id: str, speaker: str) -> Path | None:
    pattern = f"speaker_{speaker}_convo_{session_id}.wav"
    matches = list(corpus_root.rglob(pattern))
    return matches[0] if matches else None


def find_transcript(corpus_root: Path, session_id: str, speaker: str) -> Path | None:
    pattern = f"speaker_{speaker}_convo_{session_id}_transcript.json"
    matches = list(corpus_root.rglob(pattern))
    return matches[0] if matches else None


def _remap(t, ratio: float):
    return round(t * ratio, 6) if t is not None else None


def remap_tier_a(transcript: dict, resample_ratio: float) -> dict:
    result = copy.deepcopy(transcript)
    for seg in result.get("segments", []):
        seg["startTime"] = _remap(seg["startTime"], resample_ratio)
        seg["endTime"] = _remap(seg["endTime"], resample_ratio)
        for word in seg.get("words", []):
            word["startTime"] = _remap(word["startTime"], resample_ratio)
            word["endTime"] = _remap(word["endTime"], resample_ratio)
    return result


def tag_speaker(transcript: dict, speaker: str) -> None:
    for seg in transcript.get("segments", []):
        seg["speaker"] = speaker
        for word in seg.get("words", []):
            word["speaker"] = speaker


def write_json(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def process_session(
    session_id: str,
    params: dict,
    corpus_root: Path,
    output_root: Path,
    res_type: str,
) -> bool:
    output_dir = output_root / session_id
    reference_channel = params["reference_channel"]
    target_channel = "b" if reference_channel == "a" else "a"

    wav_ref = find_wav(corpus_root, session_id, reference_channel)
    wav_tgt = find_wav(corpus_root, session_id, target_channel)
    if wav_ref is None or wav_tgt is None:
        print(f"  [{session_id}] SKIP: WAV files not found", file=sys.stderr)
        return False

    tx_ref_path = find_transcript(corpus_root, session_id, reference_channel)
    tx_tgt_path = find_transcript(corpus_root, session_id, target_channel)
    if tx_ref_path is None or tx_tgt_path is None:
        print(f"  [{session_id}] SKIP: transcript files not found", file=sys.stderr)
        return False

    print(f"  [{session_id}] Loading audio...")
    y_ref, sr_ref = librosa.load(str(wav_ref), sr=None, mono=True)
    y_tgt, sr_tgt = librosa.load(str(wav_tgt), sr=None, mono=True)

    if sr_ref != sr_tgt:
        print(
            f"  [{session_id}] WARNING: sample rate mismatch ({sr_ref} vs {sr_tgt})",
            file=sys.stderr,
        )

    n_ref = len(y_ref)
    n_tgt = len(y_tgt)
    resample_ratio = n_ref / n_tgt

    # Resample target to match reference length exactly.
    # Use orig_sr=sr_tgt and a scaled target_sr to encode the stretch ratio as
    # integers, then trim/pad by at most one sample to guarantee exact length.
    if n_tgt != n_ref:
        print(f"  [{session_id}] Resampling channel {target_channel} (ratio {resample_ratio:.6f})...")
        target_sr = round(sr_tgt * resample_ratio)
        y_tgt_aligned = librosa.resample(y_tgt, orig_sr=sr_tgt, target_sr=target_sr, res_type=res_type)
        if len(y_tgt_aligned) > n_ref:
            y_tgt_aligned = y_tgt_aligned[:n_ref]
        elif len(y_tgt_aligned) < n_ref:
            y_tgt_aligned = np.pad(y_tgt_aligned, (0, n_ref - len(y_tgt_aligned)))
    else:
        y_tgt_aligned = y_tgt

    # Mix: Speaker A = left, Speaker B = right.
    y_a = y_ref if reference_channel == "a" else y_tgt_aligned
    y_b = y_ref if reference_channel == "b" else y_tgt_aligned
    stereo = np.stack([y_a, y_b], axis=1)

    mixed_name = f"{session_id}_mixed.wav"
    mixed_path = output_dir / mixed_name
    sf.write(str(mixed_path), stereo, sr_ref, subtype="PCM_16")
    print(f"  [{session_id}] Written: {mixed_name}")

    # Transcripts.
    with tx_ref_path.open(encoding="utf-8") as fh:
        tx_ref = json.load(fh)
    with tx_tgt_path.open(encoding="utf-8") as fh:
        tx_tgt = json.load(fh)

    tx_ref_aligned = copy.deepcopy(tx_ref)
    tx_tgt_aligned = remap_tier_a(tx_tgt, resample_ratio)

    reference_duration = n_ref / sr_ref
    for tx in (tx_ref_aligned, tx_tgt_aligned):
        meta = tx.setdefault("metadata", {})
        meta["audio_file"] = mixed_name
        meta["recordingDuration"] = round(reference_duration, 6)

    tag_speaker(tx_ref_aligned, reference_channel)
    tag_speaker(tx_tgt_aligned, target_channel)

    ref_tx_out = output_dir / f"speaker_{reference_channel}_convo_{session_id}_transcript_aligned.json"
    tgt_tx_out = output_dir / f"speaker_{target_channel}_convo_{session_id}_transcript_aligned.json"
    write_json(ref_tx_out, tx_ref_aligned)
    write_json(tgt_tx_out, tx_tgt_aligned)
    print(f"  [{session_id}] Written: {ref_tx_out.name}, {tgt_tx_out.name}")

    # Merged transcript.
    all_segments = (
        list(tx_ref_aligned.get("segments", []))
        + list(tx_tgt_aligned.get("segments", []))
    )
    all_segments.sort(key=lambda s: s["startTime"] if s["startTime"] is not None else float("inf"))

    lang = tx_ref.get("metadata", {}).get("languageCode", "is-IS")
    merged = {
        "session_id": session_id,
        "audio_file": mixed_name,
        "recordingDuration": round(reference_duration, 6),
        "languageCode": lang,
        "segments": all_segments,
    }
    merged_path = output_dir / f"{session_id}_transcript_merged.json"
    write_json(merged_path, merged)
    print(f"  [{session_id}] Written: {merged_path.name}")

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Stage 2 synthesis: align and mix Tier A sessions."
    )
    parser.add_argument("--corpus-root", required=True, help="Path to source corpus root.")
    parser.add_argument("--output-root", required=True, help="Path to output root.")
    parser.add_argument("--sessions", default=None, help="Comma-separated session IDs (default: all).")
    parser.add_argument("--res-type", default="kaiser_best", help="librosa resampling quality.")
    args = parser.parse_args()

    corpus_root = Path(args.corpus_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()

    if not corpus_root.exists() or not corpus_root.is_dir():
        print(f"Error: corpus root not found or not a directory: {corpus_root}", file=sys.stderr)
        sys.exit(1)

    # Read proposed tier threshold from corpus summary when available.
    tier_a_max = None
    corpus_summary_path = output_root / "corpus_summary.json"
    if corpus_summary_path.exists():
        with corpus_summary_path.open(encoding="utf-8") as fh:
            cs = json.load(fh)
        tier_a_max = cs.get("proposed_thresholds", {}).get("tier_a_max")

    # Collect param files.
    if args.sessions:
        session_ids = [s.strip() for s in args.sessions.split(",")]
        param_files = [output_root / sid / "session_params.json" for sid in session_ids]
    else:
        param_files = sorted(output_root.rglob("session_params.json"))

    processed = skipped = 0
    for param_path in param_files:
        if not param_path.exists():
            print(f"Missing: {param_path}", file=sys.stderr)
            skipped += 1
            continue

        with param_path.open(encoding="utf-8") as fh:
            params = json.load(fh)

        session_id = params["session_id"]
        drift = params["drift_percent"]
        tier = params.get("tier")

        # Resolve effective tier.
        if tier == "A":
            effective_tier = "A"
        elif tier is None:
            if tier_a_max is not None:
                effective_tier = "A" if drift <= tier_a_max else "skip"
            else:
                # No summary available: treat all non-inspection sessions as Tier A.
                effective_tier = "A" if drift < 5.0 else "skip"
        else:
            effective_tier = tier  # "B", "C", or anything else → skip

        if effective_tier != "A":
            print(f"[{session_id}] Skipping (tier={tier}, drift={drift:.3f}%)")
            skipped += 1
            continue

        print(f"[{session_id}] Processing (drift={drift:.3f}%)...")
        ok = process_session(session_id, params, corpus_root, output_root, args.res_type)
        if ok:
            processed += 1
        else:
            skipped += 1

    print(f"\nDone: {processed} processed, {skipped} skipped.")


if __name__ == "__main__":
    main()
