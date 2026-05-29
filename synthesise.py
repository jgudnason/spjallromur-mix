#!/usr/bin/env python3
import argparse
import copy
import json
from pathlib import Path
import sys

import librosa
import numpy as np
import soundfile as sf

# Sessions where the WAV header reports an incorrect sample rate.
# Both channels are resampled from the true rate to 16000 Hz before drift correction.
SAMPLE_RATE_OVERRIDES = {
    "198f2863": 12000,  # header says 16000 Hz; audio was recorded at 12000 Hz
}

# Sessions skipped entirely — no audio or transcript output is produced.
EXCLUDED_SESSIONS = {
    "198f2863": (
        "audio recording quality too poor to mix — "
        "multiple voices, severe audio artefacts, and probable sample rate mismatch"
    ),
}


def find_wav(corpus_root: Path, session_id: str, speaker: str) -> Path | None:
    """Find speaker WAV under full_conversations/ in the CLARIN corpus root."""
    pattern = f"speaker_{speaker}_convo_{session_id}.wav"
    for p in corpus_root.rglob(pattern):
        if "full_conversations" in p.parts:
            return p
    return None


def find_transcript_v2(transcript_root: Path, session_id: str, speaker: str) -> Path | None:
    session_dir = transcript_root / "full_conversations" / session_id
    matches = list(session_dir.glob(f"{speaker}_{session_id}_*.json"))
    return matches[0] if matches else None


def find_transcript_clarin(corpus_root: Path, session_id: str, speaker: str) -> Path | None:
    pattern = f"speaker_{speaker}_convo_{session_id}_transcript.json"
    for p in corpus_root.rglob(pattern):
        if "full_conversations" in p.parts:
            return p
    return None


def clarin_to_v2(transcript: dict, speaker: str) -> dict:
    """Normalise a CLARIN segment/word transcript to the v2 flat word-list format."""
    words = []
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []):
            words.append({
                "word": w.get("word", ""),
                "norm_word": None,
                "start": w.get("startTime"),
                "end": w.get("endTime"),
            })
    meta = transcript.get("metadata", {})
    return {
        "metadata": {
            "audio_duration": meta.get("recordingDuration"),
            "speaker": speaker,
        },
        "words": words,
    }


def load_transcript(
    transcript_root: Path,
    corpus_root: Path,
    session_id: str,
    speaker: str,
) -> tuple[Path | None, dict | None, str | None]:
    """Return (source_path, normalised_transcript, source_label).

    Tries v2 first; falls back to CLARIN if not found.
    Returns (None, None, None) if neither is found.
    """
    v2_path = find_transcript_v2(transcript_root, session_id, speaker)
    if v2_path is not None:
        with v2_path.open(encoding="utf-8") as fh:
            return v2_path, json.load(fh), "v2"

    clarin_path = find_transcript_clarin(corpus_root, session_id, speaker)
    if clarin_path is not None:
        with clarin_path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
        return clarin_path, clarin_to_v2(raw, speaker), "clarin"

    return None, None, None


def _remap(t, ratio: float):
    return round(t * ratio, 6) if t is not None else None


def remap_tier_a(transcript: dict, resample_ratio: float) -> dict:
    """Return a deep copy of transcript with all word start/end times multiplied by resample_ratio."""
    result = copy.deepcopy(transcript)
    for word in result.get("words", []):
        word["start"] = _remap(word["start"], resample_ratio)
        word["end"] = _remap(word["end"], resample_ratio)
    return result


def tag_speaker(transcript: dict, speaker: str) -> None:
    for word in transcript.get("words", []):
        word["speaker"] = speaker


def write_json(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def update_session_params(output_dir: Path, updates: dict) -> None:
    """Merge Stage 2 fields into the existing session_params.json."""
    params_path = output_dir / "session_params.json"
    if not params_path.exists():
        return
    with params_path.open(encoding="utf-8") as fh:
        params = json.load(fh)
    params.update(updates)
    with params_path.open("w", encoding="utf-8") as fh:
        json.dump(params, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def aligned_output_name(tx_path: Path, session_id: str, speaker: str, source: str) -> str:
    if source == "v2":
        return f"{tx_path.stem}_aligned.json"
    return f"{speaker}_{session_id}_aligned.json"


def process_session(
    session_id: str,
    params: dict,
    corpus_root: Path,
    transcript_root: Path,
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

    tx_ref_path, tx_ref, tx_ref_source = load_transcript(
        transcript_root, corpus_root, session_id, reference_channel
    )
    tx_tgt_path, tx_tgt, tx_tgt_source = load_transcript(
        transcript_root, corpus_root, session_id, target_channel
    )
    if tx_ref is None or tx_tgt is None:
        print(f"  [{session_id}] SKIP: transcript files not found", file=sys.stderr)
        return False

    transcript_source = "v2" if (tx_ref_source == "v2" and tx_tgt_source == "v2") else "clarin"
    if tx_ref_source != tx_tgt_source:
        print(
            f"  [{session_id}] WARNING: mixed transcript sources "
            f"(ref={tx_ref_source}, tgt={tx_tgt_source})",
            file=sys.stderr,
        )

    print(f"  [{session_id}] Loading audio (transcript_source={transcript_source})...")
    y_ref, sr_ref = librosa.load(str(wav_ref), sr=None, mono=True)
    y_tgt, sr_tgt = librosa.load(str(wav_tgt), sr=None, mono=True)

    # Correct for sessions where the WAV header reports the wrong sample rate.
    true_sr = SAMPLE_RATE_OVERRIDES.get(session_id)
    if true_sr is not None:
        target_sr_out = 16000
        print(
            f"  [{session_id}] Sample rate correction: {true_sr} Hz → {target_sr_out} Hz",
            file=sys.stderr,
        )
        y_ref = librosa.resample(y_ref, orig_sr=true_sr, target_sr=target_sr_out, res_type=res_type)
        y_tgt = librosa.resample(y_tgt, orig_sr=true_sr, target_sr=target_sr_out, res_type=res_type)
        sr_ref = sr_tgt = target_sr_out

    if sr_ref != sr_tgt:
        print(
            f"  [{session_id}] WARNING: sample rate mismatch ({sr_ref} vs {sr_tgt})",
            file=sys.stderr,
        )

    n_ref = len(y_ref)
    n_tgt = len(y_tgt)
    resample_ratio = n_ref / n_tgt

    # Resample target to match reference length exactly.
    if n_tgt != n_ref:
        print(f"  [{session_id}] Resampling channel {target_channel} (ratio {resample_ratio:.6f})...")
        sr_scaled = round(sr_tgt * resample_ratio)
        y_tgt_aligned = librosa.resample(y_tgt, orig_sr=sr_tgt, target_sr=sr_scaled, res_type=res_type)
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
    tx_ref_aligned = copy.deepcopy(tx_ref)
    tx_tgt_aligned = remap_tier_a(tx_tgt, resample_ratio)

    reference_duration = n_ref / sr_ref
    for tx in (tx_ref_aligned, tx_tgt_aligned):
        meta = tx.setdefault("metadata", {})
        meta["audio_file"] = mixed_name
        meta["audio_duration"] = round(reference_duration, 6)

    tag_speaker(tx_ref_aligned, reference_channel)
    tag_speaker(tx_tgt_aligned, target_channel)

    ref_tx_out = output_dir / aligned_output_name(tx_ref_path, session_id, reference_channel, tx_ref_source)
    tgt_tx_out = output_dir / aligned_output_name(tx_tgt_path, session_id, target_channel, tx_tgt_source)
    write_json(ref_tx_out, tx_ref_aligned)
    write_json(tgt_tx_out, tx_tgt_aligned)
    print(f"  [{session_id}] Written: {ref_tx_out.name}, {tgt_tx_out.name}")

    # Merged transcript.
    all_words = (
        list(tx_ref_aligned.get("words", []))
        + list(tx_tgt_aligned.get("words", []))
    )
    all_words.sort(key=lambda w: w["start"] if w["start"] is not None else float("inf"))

    merged = {
        "session_id": session_id,
        "audio_file": mixed_name,
        "recording_duration": round(reference_duration, 6),
        "words": all_words,
    }
    merged_path = output_dir / f"{session_id}_transcript_merged.json"
    write_json(merged_path, merged)
    print(f"  [{session_id}] Written: {merged_path.name}")

    # Update session_params.json with Stage 2 fields.
    update_session_params(output_dir, {
        "resample_ratio": round(resample_ratio, 8),
        "transcript_source": transcript_source,
    })

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Stage 2 synthesis: align and mix all sessions (Tier A)."
    )
    parser.add_argument("--corpus-root", required=True, help="Path to CLARIN corpus root (WAV files).")
    parser.add_argument("--transcript-root", required=True, help="Path to v2 transcript root; CLARIN transcripts used as fallback when v2 not found.")
    parser.add_argument("--output-root", required=True, help="Path to output root.")
    parser.add_argument("--sessions", default=None, help="Comma-separated session IDs (default: all).")
    parser.add_argument("--res-type", default="kaiser_best", help="librosa resampling quality.")
    args = parser.parse_args()

    corpus_root = Path(args.corpus_root).expanduser().resolve()
    transcript_root = Path(args.transcript_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()

    for label, p in [("corpus-root", corpus_root), ("transcript-root", transcript_root)]:
        if not p.exists() or not p.is_dir():
            print(f"Error: {label} not found or not a directory: {p}", file=sys.stderr)
            sys.exit(1)

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

        if session_id in EXCLUDED_SESSIONS or params.get("status") == "excluded":
            print(f"[{session_id}] Skipping (excluded: {EXCLUDED_SESSIONS.get(session_id, params.get('exclusion_reason', ''))})")
            skipped += 1
            continue

        drift = params["drift_percent"]
        above = params.get("above_1pct_threshold", False)

        flag = " [>1%]" if above else ""
        print(f"[{session_id}] Processing (drift={drift:.3f}%{flag})...")
        ok = process_session(
            session_id, params, corpus_root, transcript_root, output_root, args.res_type
        )
        if ok:
            processed += 1
        else:
            skipped += 1

    print(f"\nDone: {processed} processed, {skipped} skipped.")


if __name__ == "__main__":
    main()
