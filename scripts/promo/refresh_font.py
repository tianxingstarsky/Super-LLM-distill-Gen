"""Refresh the self-hosted Noto Sans SC film subset from Google Fonts."""

from pathlib import Path
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
PROMO = ROOT / "assets" / "promo"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def main():
    files = [PROMO / "film.mjs", PROMO / "timeline.mjs", PROMO / "index.html",
             ROOT / "scripts" / "promo" / "generate_audio.py", PROMO / "audio" / "cues.json"]
    chars = "".join(sorted(set("".join(file.read_text(encoding="utf-8") for file in files))))
    url = "https://fonts.googleapis.com/css2?" + urlencode({"family": "Noto Sans SC:wght@400;500;600;700", "text": chars})
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=60) as response:
        css = response.read().decode("utf-8")
    font_urls = set(re.findall(r"url\((https://[^)]+)\)", css))
    if len(font_urls) != 1:
        raise RuntimeError("Expected one shared variable-font subset")
    with urlopen(next(iter(font_urls)), timeout=60) as response:
        font = response.read()
    if font[:4] != b"wOF2":
        raise RuntimeError("The font response is not WOFF2")
    (PROMO / "fonts" / "promo.woff2").write_bytes(font)
    (PROMO / "fonts" / "source.txt").write_text(
        "Noto Sans SC. Google Fonts text subset. SIL Open Font License 1.1.\n" + url + "\n",
        encoding="utf-8",
    )
    print(f"Saved {len(font):,} bytes; {len(chars)} distinct characters")


if __name__ == "__main__":
    main()
