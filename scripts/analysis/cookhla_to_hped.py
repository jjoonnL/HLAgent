"""CLI entry point; implementation lives in hlagent.analysis.cookhla_to_hped."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hlagent.analysis.cookhla_to_hped import main


if __name__ == "__main__":
    raise SystemExit(main())
