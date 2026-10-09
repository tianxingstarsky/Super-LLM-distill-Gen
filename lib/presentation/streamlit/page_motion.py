"""Keep page-entry motion separate from normal widget and live-task reruns."""
from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any


PAGE_MOTION_CSS = """
/* Keep the surface free of transforms: node inspectors use viewport coordinates. */
[data-testid="stApp"] .st-key-page-surface-a { animation: df-page-enter-a 220ms cubic-bezier(.22,1,.36,1); }
[data-testid="stApp"] .st-key-page-surface-b { animation: df-page-enter-b 220ms cubic-bezier(.22,1,.36,1); }
[data-testid="stApp"] .st-key-page-surface-a .df-page-copy { animation: df-page-copy-a 300ms cubic-bezier(.22,1,.36,1); }
[data-testid="stApp"] .st-key-page-surface-b .df-page-copy { animation: df-page-copy-b 300ms cubic-bezier(.22,1,.36,1); }
[data-testid="stApp"] .st-key-page-surface-a .df-hero-art img { animation: df-page-art-a 360ms cubic-bezier(.22,1,.36,1); }
[data-testid="stApp"] .st-key-page-surface-b .df-hero-art img { animation: df-page-art-b 360ms cubic-bezier(.22,1,.36,1); }
[data-testid="stSidebar"] [class*="st-key-nav-button"] button[kind="primary"]::before { animation: df-nav-arrive 240ms cubic-bezier(.22,1,.36,1); transform-origin:center; }
@keyframes df-page-enter-a { from { opacity:.82; } to { opacity:1; } }
@keyframes df-page-enter-b { from { opacity:.82; } to { opacity:1; } }
@keyframes df-page-copy-a { from { opacity:.75; transform:translateY(7px); } to { opacity:1; transform:translateY(0); } }
@keyframes df-page-copy-b { from { opacity:.75; transform:translateY(7px); } to { opacity:1; transform:translateY(0); } }
@keyframes df-page-art-a { from { opacity:.65; transform:translate(10px,3px) scale(.98); } to { opacity:1; transform:translate(0,0) scale(1); } }
@keyframes df-page-art-b { from { opacity:.65; transform:translate(10px,3px) scale(.98); } to { opacity:1; transform:translate(0,0) scale(1); } }
@keyframes df-nav-arrive { from { opacity:.4; transform:scaleY(.45); } to { opacity:1; transform:scaleY(1); } }
@media (prefers-reduced-motion:reduce) {
  [data-testid="stApp"] :is(.st-key-page-surface-a,.st-key-page-surface-b),
  [data-testid="stApp"] :is(.st-key-page-surface-a,.st-key-page-surface-b) :is(.df-page-copy,.df-hero-art img),
  [data-testid="stSidebar"] [class*="st-key-nav-button"] button[kind="primary"]::before { animation:none; }
}
"""


def page_surface_key(state: MutableMapping[str, Any], route: str) -> str:
    """Alternate animation names only when the rendered page changes.

    The caller supplies the renderer's stable name so route aliases share a
    surface. Container keys do not change explicit widget identities.
    """
    phase = state.get("page-motion-phase", "b")
    if phase not in {"a", "b"}:
        phase = "b"
    if state.get("page-motion-route") != route:
        phase = "a" if phase == "b" else "b"
        state["page-motion-route"] = route
    state["page-motion-phase"] = phase
    return f"page-surface-{phase}"
