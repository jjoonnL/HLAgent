"""CLI entry point; implementation lives in hlagent.runtime.datasets."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hlagent.runtime.datasets import main


if __name__ == "__main__":
    raise SystemExit(main())
