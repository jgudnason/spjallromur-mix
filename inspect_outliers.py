#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import sys

import librosa
import matplotlib.pyplot as plt
import numpy as np


def find_session_wav(corpus_root: Path, session_id: str, speaker: str) -> Path | None:
    pattern = f"speaker_{speaker}_convo_{session_id}.wav"
    matches = list(corpus_root.rglob(pattern))
    return matches[0] if matches else None


def load_audio(wav_path: Path):
    y, sr = librosa.load(str(wav_path), sr=None)
    return y, sr


def compute_rms_envelope(y, sr, frame_length_sec=1.0):
    frame_length = int(sr * frame_length_sec)
    hop_length = frame_length  # Non-overlapping frames
    rms = librosa.feature.rms(y=y, frame_length=frame_length, hop_length=hop_length)
    return rms.flatten()


def plot_envelopes(session_id, y_a, sr_a, y_b, sr_b, delta_sec, drift_percent, output_path):
    rms_a = compute_rms_envelope(y_a, sr_a)
    rms_b = compute_rms_envelope(y_b, sr_b)
    
    if sr_a != sr_b:
        print(f"Warning: Different sample rates for {session_id}: A={sr_a}, B={sr_b}", file=sys.stderr)

    time_a = np.arange(len(rms_a)) * 1.0  # 1-sec frames
    time_b = np.arange(len(rms_b)) * 1.0
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4), sharex=True)
    
    ax1.plot(time_a, rms_a, label='Speaker A')
    ax1.set_title('Speaker A Envelope')
    ax1.set_ylabel('RMS Energy')
    ax1.grid(True)
    
    ax2.plot(time_b, rms_b, label='Speaker B', color='orange')
    ax2.set_title('Speaker B Envelope')
    ax2.set_ylabel('RMS Energy')
    ax2.grid(True)
    
    fig.suptitle(f'Session {session_id} - Delta: {delta_sec:.3f}s, Drift: {drift_percent:.3f}%')
    plt.xlabel('Time (seconds)')
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()


def main():
    parser = argparse.ArgumentParser(description="Inspect outlier sessions with high drift.")
    parser.add_argument("--corpus-root", required=True, help="Path to the corpus root.")
    parser.add_argument("--output-root", required=True, help="Path to the output root.")
    args = parser.parse_args()

    corpus_root = Path(args.corpus_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()

    if not corpus_root.exists() or not corpus_root.is_dir():
        print(f"Error: corpus root not found or not a directory: {corpus_root}", file=sys.stderr)
        sys.exit(1)

    output_dir = output_root / "inspection"
    output_dir.mkdir(parents=True, exist_ok=True)

    outlier_sessions = ["2284fd64", "2c1b4416", "5331448b", "2f1655ff", "2d219d50"]

    for session_id in outlier_sessions:
        wav_a = find_session_wav(corpus_root, session_id, "a")
        wav_b = find_session_wav(corpus_root, session_id, "b")

        if wav_a is None or wav_b is None:
            print(f"Skipping {session_id}: WAV files not found", file=sys.stderr)
            continue

        try:
            y_a, sr_a = load_audio(wav_a)
            y_b, sr_b = load_audio(wav_b)

            duration_a = len(y_a) / sr_a
            duration_b = len(y_b) / sr_b
            delta_sec = abs(duration_a - duration_b)
            reference_duration = max(duration_a, duration_b)
            drift_percent = (delta_sec / reference_duration) * 100 if reference_duration > 0 else 0.0

            output_path = output_dir / f"{session_id}.png"
            plot_envelopes(session_id, y_a, sr_a, y_b, sr_b, delta_sec, drift_percent, output_path)
            print(f"Plotted {session_id} -> {output_path}")

        except Exception as e:
            print(f"Error processing {session_id}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()