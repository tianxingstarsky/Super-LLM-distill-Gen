"""Run the optional use case in an isolated CUDA training environment."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lib.presentation.grpo_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
