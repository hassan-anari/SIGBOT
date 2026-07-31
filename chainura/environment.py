from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_project_environment(path: str | Path | None = None) -> bool:
    """Explicitly load a dotenv file without overriding the process environment."""
    dotenv_path = Path(path) if path is not None else PROJECT_ROOT / ".env"
    if not dotenv_path.is_file():
        return False
    # python-dotenv itself observes PYTHON_DOTENV_DISABLED. Keeping this call
    # explicit ensures imports never mutate the environment.
    return bool(load_dotenv(dotenv_path=dotenv_path, override=False))
