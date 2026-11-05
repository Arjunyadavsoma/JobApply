from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
PROFILE_DIR = ROOT / "profile"
MASTER_RESUME_JSON = PROFILE_DIR / "master_resume.json"
BASE_RESUME_PDF = PROFILE_DIR / "source_resume.pdf"  # your original PDF, used when a job isn't tailored
CONFIG_PATH = ROOT / "config.yaml"

load_dotenv(ROOT / ".env")


@lru_cache
def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise SystemExit(
            f"Missing {CONFIG_PATH.name}. Run `python -m jobagent init` and edit it first."
        )
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def cfg(path: str, default: Any = None) -> Any:
    """Dotted lookup into config.yaml, e.g. cfg("search.min_match_score", 70)."""
    node: Any = load_config()
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node if node is not None else default
