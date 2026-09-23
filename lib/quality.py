"""Compatibility surface for callers of the former all-in-one quality module.

New callers use ``ReleaseApplication`` from ``lib.bootstrap.releases``.
"""
from __future__ import annotations

from pathlib import Path

from lib.bootstrap.releases import release_application
from lib.domain.release_quality import report, sample_hash


def read_samples(path: Path) -> list[dict]:
    return release_application().read_samples(path)


def export_release(samples, fmt, parent: Path, decisions=(), corpus_path=None,
                   dpo_path=None, tag=None, bulk=False):
    return release_application().export_release(samples, fmt, parent, decisions,
                                                corpus_path, dpo_path, tag, bulk)
