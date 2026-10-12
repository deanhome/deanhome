"""GUI entry point (``python -m serato_analyzer``)."""

import sys


def main() -> int:
    from .gui.app import run

    return run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
