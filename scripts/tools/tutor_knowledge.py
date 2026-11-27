"""CLI entry point for local Tutor knowledge management."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hlagent.cli.tutor_knowledge import main

if __name__ == '__main__':
    raise SystemExit(main())
