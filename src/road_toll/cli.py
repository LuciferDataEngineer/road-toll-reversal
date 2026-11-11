"""Command line entry points. The default path never needs Docker."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from road_toll.paths import CONTRACTS, ROOT


def test_contracts() -> int:
    code = 0
    for path in sorted(CONTRACTS.glob("*.odcs.yaml")):
        executable = Path(sys.executable).with_name("datacontract")
        completed = subprocess.run([str(executable), "test", str(path)], cwd=ROOT, check=False)
        code = max(code, completed.returncode)
    return code


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="road_toll")
    parser.add_argument(
        "command",
        choices=("pipeline", "model", "dashboard", "contracts", "benchmark", "all"),
    )
    args = parser.parse_args(argv)
    if args.command in {"pipeline", "all"}:
        from road_toll.pipeline import build_pipeline

        build_pipeline()
    if args.command in {"model", "all"}:
        from road_toll.model import run_model

        run_model()
    if args.command in {"dashboard", "all"}:
        from road_toll.dashboard import build_dashboard

        build_dashboard()
    if args.command == "contracts":
        raise SystemExit(test_contracts())
    if args.command == "benchmark":
        from road_toll.benchmark import run_benchmark

        run_benchmark()


if __name__ == "__main__":
    main()
