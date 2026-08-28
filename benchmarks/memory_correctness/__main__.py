"""Module entrypoint for ``python -m benchmarks.memory_correctness``."""

from .runner import main


if __name__ == "__main__":
    raise SystemExit(main())
