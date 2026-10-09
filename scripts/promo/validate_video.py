"""Decode the delivery video and check it against the final soundtrack."""

import argparse
import json
import re
import subprocess

import numpy as np
from scipy.signal import correlate, correlation_lags

from encode_video import PROMO_ROOT, MP4_NAME, find_ffmpeg


def mono_audio(ffmpeg, path):
    result = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-map", "0:a:0",
                             "-ac", "1", "-ar", "12000", "-f", "f32le", "pipe:1"],
                            check=True, capture_output=True)
    return np.frombuffer(result.stdout, dtype="<f4")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg")
    args = parser.parse_args()
    ffmpeg = find_ffmpeg(args.ffmpeg)
    video = PROMO_ROOT / MP4_NAME
    decoded = subprocess.run([ffmpeg, "-hide_banner", "-i", str(video), "-map", "0:v:0",
                              "-map", "0:a:0", "-f", "null", "-"],
                             check=True, capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
    metadata = decoded.stderr
    duration = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", metadata)
    if not duration:
        raise RuntimeError("No delivery duration found")
    seconds = int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3])
    video_line = next((line for line in metadata.splitlines() if "Stream #0:0" in line and "Video:" in line), "")
    audio_line = next((line for line in metadata.splitlines() if "Audio:" in line), "")
    if "Video: h264" not in video_line or "Audio: aac" not in audio_line:
        raise RuntimeError("Delivery is not H.264 / AAC")
    if abs(seconds - 60) > .02 or "1920x1080" not in video_line or "30 fps" not in video_line:
        raise RuntimeError("Delivery is not 60 seconds / 1080p / 30 fps")
    if "48000 Hz" not in audio_line or "stereo" not in audio_line:
        raise RuntimeError("Delivery is missing its stereo 48 kHz soundtrack")
    actual = mono_audio(ffmpeg, video)
    expected = mono_audio(ffmpeg, PROMO_ROOT / "audio" / "master.mp3")
    if not len(actual) or np.max(np.abs(actual)) < .001:
        raise RuntimeError("Delivery audio is silent")
    match = correlate(actual, expected, mode="full", method="fft")
    lag = int(correlation_lags(len(actual), len(expected))[int(np.argmax(match))])
    if lag >= 0:
        actual, expected = actual[lag:], expected[:len(actual) - lag]
    else:
        actual, expected = actual[:len(expected) + lag], expected[-lag:]
    count = min(len(actual), len(expected))
    similarity = float(np.corrcoef(actual[:count], expected[:count])[0, 1])
    if abs(lag / 12000) > .2 or not np.isfinite(similarity) or similarity < .9:
        raise RuntimeError(f"Soundtrack mismatch: offset {lag / 12000:.3f}s, correlation {similarity:.3f}")
    report = {"file": MP4_NAME, "duration_seconds": seconds, "width": 1920, "height": 1080,
              "fps": 30, "video_codec": "H.264", "audio_codec": "AAC", "audio_sample_rate": 48000,
              "audio_channels": 2, "file_bytes": video.stat().st_size, "full_decode_passed": True,
              "audio_master_correlation": round(similarity, 5),
              "audio_alignment_offset_seconds": round(lag / 12000, 5),
              "subtitles": "transparent text, baked into the video; captions.srt also provided",
              "readme_scene": "local preview of the project README"}
    (PROMO_ROOT / "video_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
