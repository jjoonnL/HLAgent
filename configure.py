"""Compatibility entry point; implementation lives in hlagent.cli.configure."""

from hlagent.cli.configure import main


if __name__ == "__main__":
    raise SystemExit(main())
