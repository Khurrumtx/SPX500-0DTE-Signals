"""SPX 0DTE self-training agent — EDUCATIONAL ONLY, NOT FINANCIAL ADVICE."""

from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parents[1]
DEFAULT_CONFIG = PACKAGE_ROOT / "config.yaml"

__all__ = ["PACKAGE_ROOT", "REPO_ROOT", "DEFAULT_CONFIG"]
