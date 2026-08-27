"""Module entrypoint for ``python -m benchmarks.minicode``."""

from .runner import main


if __name__ == "__main__":
    raise SystemExit(main())
