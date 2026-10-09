"""Convert the fixed browser recording to a 60-second 1080p delivery video.

Also export a full-size poster and a seven-frame contact sheet. No network access
or system installation is performed; use --ffmpeg if it is not already available.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


PROMO_ROOT = Path(__file__).resolve().parents[2] / "assets" / "promo"
WEBM_NAME = "shujian-cube-promo.webm"
MP4_NAME = "shujian-cube-promo.mp4"
POSTER_NAME = "shujian-cube-promo-poster.png"
CONTACT_NAME = "shujian-cube-promo-contact-sheet.png"
CONTACT_TIMES = (2, 12, 23, 33, 42, 52, 58)


def find_ffmpeg(explicit: str | None = None) -> str:
    if explicit:
        executable = shutil.which(explicit)
        if executable:
            return executable
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
        raise FileNotFoundError(f"FFmpeg executable was not found: {explicit}")
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    try:
        import imageio_ffmpeg

        executable = imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError) as error:
        raise FileNotFoundError("FFmpeg is unavailable; pass --ffmpeg with a local executable path") from error
    if not Path(executable).is_file():
        raise FileNotFoundError("imageio_ffmpeg did not provide a local FFmpeg executable")
    return executable


@contextmanager
def atomic_output(destination: Path):
    with tempfile.NamedTemporaryFile(prefix=f".{destination.stem}-", suffix=destination.suffix,
                                     dir=destination.parent, delete=False) as file:
        temporary = Path(file.name)
    try:
        yield temporary
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError(f"FFmpeg produced an empty {destination.name}")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _run(ffmpeg: str, arguments: list[str]):
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "warning", "-y", *arguments], check=True)


def encode(ffmpeg: str, *, poster_time: float = 12.0, stills: bool = True):
    source = PROMO_ROOT / WEBM_NAME
    if not source.is_file():
        raise FileNotFoundError(f"Record the film first: {source}")
    destination = PROMO_ROOT / MP4_NAME
    with atomic_output(destination) as temporary:
        _run(ffmpeg, [
            "-protocol_whitelist", "file,pipe", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0",
            "-vf", "setpts=PTS-STARTPTS,scale=1920:1080:flags=lanczos,setsar=1,fps=30,tpad=stop_mode=clone:stop_duration=60",
            "-af", "asetpts=PTS-STARTPTS,apad=pad_dur=60", "-t", "60",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(temporary),
        ])
    print(f"Saved {destination}", flush=True)
    if not stills:
        return
    poster = PROMO_ROOT / POSTER_NAME
    with atomic_output(poster) as temporary:
        _run(ffmpeg, ["-protocol_whitelist", "file,pipe", "-ss", str(poster_time), "-i", str(destination),
                      "-frames:v", "1", "-update", "1", str(temporary)])
    print(f"Saved {poster}", flush=True)
    contact = PROMO_ROOT / CONTACT_NAME
    selected_frames = "+".join(f"eq(n,{second * 30})" for second in CONTACT_TIMES)
    with atomic_output(contact) as temporary:
        _run(ffmpeg, [
            "-protocol_whitelist", "file,pipe", "-i", str(destination),
            "-vf", f"select='{selected_frames}',scale=480:270,tile=4x2:nb_frames=7:color=0x071421",
            "-frames:v", "1", "-update", "1", str(temporary),
        ])
    print(f"Saved {contact} (frames at {', '.join(map(str, CONTACT_TIMES))} seconds)", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", help="Local FFmpeg executable (otherwise PATH or imageio_ffmpeg)")
    parser.add_argument("--poster-time", type=float, default=12.0, help="Poster timestamp in seconds (default: 12)")
    parser.add_argument("--skip-stills", action="store_true", help="Only encode the MP4")
    args = parser.parse_args(argv)
    if not 0 <= args.poster_time < 60:
        parser.error("--poster-time must be between 0 (inclusive) and 60 (exclusive)")
    try:
        encode(find_ffmpeg(args.ffmpeg), poster_time=args.poster_time, stills=not args.skip_stills)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Video export failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
