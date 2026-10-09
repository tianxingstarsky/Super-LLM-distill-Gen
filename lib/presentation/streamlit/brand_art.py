"""Local, decorative artwork for the data studio.

The drawing represents source material becoming an organised data cube. It
contains no metrics, progress states or user data. A data URI keeps the art
crisp and avoids a network request or executable markup.
"""
from __future__ import annotations

import base64
from pathlib import Path

from streamlit import runtime


_STUDIO_ART = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 460 210" fill="none">
<defs>
  <linearGradient id="top" x1="203" y1="43" x2="320" y2="103" gradientUnits="userSpaceOnUse"><stop stop-color="white"/><stop offset="1" stop-color="#A8D9FF"/></linearGradient>
  <linearGradient id="left" x1="207" y1="85" x2="274" y2="165" gradientUnits="userSpaceOnUse"><stop stop-color="#62B6FB"/><stop offset="1" stop-color="#2475E6"/></linearGradient>
  <linearGradient id="right" x1="320" y1="80" x2="269" y2="168" gradientUnits="userSpaceOnUse"><stop stop-color="#297AF0"/><stop offset="1" stop-color="#124CC6"/></linearGradient>
  <linearGradient id="line" x1="36" y1="0" x2="401" y2="0" gradientUnits="userSpaceOnUse"><stop stop-color="#8BBDF5" stop-opacity=".1"/><stop offset=".5" stop-color="#4E91E5"/><stop offset="1" stop-color="#83BDED" stop-opacity=".3"/></linearGradient>
  <filter id="shadow" x="-.5" y="-.5" width="2" height="2"><feDropShadow dx="0" dy="10" stdDeviation="10" flood-color="#2162BA" flood-opacity=".12"/></filter>
</defs>
<ellipse cx="270" cy="179" rx="92" ry="10" fill="#B4D4F4" opacity=".24"/>
<g stroke="#98BDE7" stroke-width="1" opacity=".5">
  <path d="M178 155 270 207 403 133 312 81Z"/><path d="m155 143 115 65 154-87"/>
  <path d="m132 130 138 78 176-99"/>
</g>
<path d="M34 117h92c33 0 26-39 60-39h36M62 151h85c28 0 29-22 60-22h18M322 105h35c22 0 18-25 41-25h30" stroke="url(#line)" stroke-width="1.5" stroke-linecap="round"/>
<g transform="translate(72 54) rotate(-9 28 37)" filter="url(#shadow)">
  <rect width="53" height="69" rx="8" fill="white" stroke="#C3D9F0"/>
  <rect x="11" y="13" width="20" height="5" rx="2.5" fill="#71AEED"/>
  <path d="M11 29h30M11 38h30M11 47h21" stroke="#BED5ED" stroke-width="3" stroke-linecap="round"/>
</g>
<g transform="translate(129 114) rotate(8 22 22)">
  <rect width="44" height="42" rx="9" fill="#F8FCFF" stroke="#B6D4EE"/>
  <path d="m12 13-5 8 5 8m20-16 5 8-5 8M26 10l-8 22" stroke="#5798DB" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
</g>
<g filter="url(#shadow)" stroke-linejoin="round">
  <path d="m208 80 59-34a8 8 0 0 1 8 0l58 34-62 36Z" fill="url(#top)" stroke="#A3CDF5"/>
  <path d="m208 80 63 36v63l-58-33a10 10 0 0 1-5-9Z" fill="url(#left)"/>
  <path d="m271 116 62-36v57a10 10 0 0 1-5 9l-57 33Z" fill="url(#right)"/>
  <path d="m210 81 61 35 60-35M271 116v60" stroke="white" stroke-opacity=".55"/>
  <path d="m235 78 28-16a7 7 0 0 1 7 0l26 15-30 18Z" fill="white" fill-opacity=".72"/>
  <path d="m230 113 21 12v21l-21-12Z" fill="white" fill-opacity=".22"/>
  <path d="m289 127 25-14v4l-25 14Zm0 11 25-14v4l-25 14Zm0 11 17-10v4l-17 10Z" fill="white" fill-opacity=".55"/>
</g>
<g transform="translate(363 62) rotate(6 22 22)">
  <rect width="44" height="44" rx="12" fill="white" stroke="#C2DBF3"/>
  <path d="m13 22 6 6 12-13" stroke="#3383D8" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
</g>
<circle cx="169" cy="78" r="3" fill="#4B98E7"/>
<circle cx="345" cy="105" r="3" fill="#4B98E7"/>
<circle cx="149" cy="44" r="3" stroke="#92C2EF"/>
<path d="M345 41v8m-4-4h8M185 170v6m-3-3h6" stroke="#8DBBED" stroke-width="1.5" stroke-linecap="round"/>
</svg>'''

STUDIO_ART_URI = "data:image/svg+xml;base64," + base64.b64encode(_STUDIO_ART.encode()).decode()

_ASSET_ROOT = Path(__file__).resolve().parents[3] / "assets" / "studio"
_GENERATED_ART = {
    "hero": "hero-data-flow-v1.png",
    "documents": "entry-documents-v1.png",
    "agent": "entry-agent-v1.png",
    "brief": "entry-brief-v1.png",
    "review": "quality-review-v1.png",
    "delivery": "delivery-package-v1.png",
}


def studio_art_url(kind: str, fallback: str = STUDIO_ART_URI) -> str:
    """Serve original PNGs through the app's session-aware media cache.

    Register each image on every render so page changes cannot orphan an active
    image. A normal media URL also avoids sending megabytes of base64 HTML on
    every widget interaction. Standalone previews retain the vector fallback.
    """
    filename = _GENERATED_ART.get(kind)
    if not filename or not runtime.exists():
        return fallback
    path = _ASSET_ROOT / filename
    if not path.is_file():
        return fallback
    try:
        return runtime.get_instance().media_file_mgr.add(
            str(path), "image/png", "studio-art:" + kind)
    except OSError:
        return fallback
