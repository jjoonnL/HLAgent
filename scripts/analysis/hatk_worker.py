"""CLI entry point; implementation lives in hlagent.analysis.hatk_worker."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hlagent.analysis.hatk_worker import main


if __name__ == "__main__":
    raise SystemExit(main())
