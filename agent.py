"""Compatibility entry point; implementation lives in hlagent.cli.agent."""

from hlagent.cli.agent import main


if __name__ == "__main__":
    raise SystemExit(main())
