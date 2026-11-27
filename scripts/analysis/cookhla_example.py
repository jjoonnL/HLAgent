"""CLI entry point; implementation lives in hlagent.cli.cookhla_example."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hlagent.cli.cookhla_example import main


if __name__ == "__main__":
    raise SystemExit(main())
