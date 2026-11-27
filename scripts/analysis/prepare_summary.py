"""CLI wrapper for explicit binary association summary preparation."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hlagent.cli.prepare_summary import main

if __name__ == '__main__':
    raise SystemExit(main())
