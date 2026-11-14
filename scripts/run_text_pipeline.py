"""CLI for the Step 10b text pipeline stages."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from volab.data import REPO_ROOT  # noqa: E402
from volab.text import pipeline  # noqa: E402

load_dotenv(REPO_ROOT / ".env")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        required=True,
        choices=("fetch", "extract", "chunk", "dq", "index", "eval"),
    )
    parser.add_argument("--snapshot", default=pipeline.DEFAULT_SNAPSHOT)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    counts = pipeline.run_stage(args.stage, args.snapshot)
    print(f"{args.stage}: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
