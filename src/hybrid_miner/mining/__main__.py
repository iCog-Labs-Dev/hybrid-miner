"""Mine a standalone fact graph without requiring extraction artifacts."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from hybrid_miner.mining.petta_runner import mine_support, write_support_jsonl


def main(argv: Sequence[str] | None = None) -> int:
    arguments = argparse.ArgumentParser(
        prog="python -m hybrid_miner.mining",
        description=(
            "Mine a standalone MeTTa fact graph using the pinned depth-zero "
            "baseline (one conjunction expansion). No ontology bundle or "
            "parser provenance is required; results do not attest semantic admission."
        ),
    )
    arguments.add_argument(
        "--atoms", type=Path, required=True, help="caller-prepared MeTTa fact graph"
    )
    arguments.add_argument("--petta-root", type=Path, required=True)
    arguments.add_argument("--miner-root", type=Path, required=True)
    arguments.add_argument("--output", type=Path, required=True)
    arguments.add_argument("--min-support", type=int, default=2)
    arguments.add_argument("--swipl", default="swipl")
    options = arguments.parse_args(argv)
    try:
        run = mine_support(
            atoms_path=options.atoms,
            petta_root=options.petta_root,
            miner_root=options.miner_root,
            min_support=options.min_support,
            swipl=options.swipl,
        )
        output = write_support_jsonl(run, options.output)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Support records: {len(run.records)} -> {output}")
    print(f"Graph SHA-256: {run.graph_sha256}")
    print(f"PeTTa revision: {run.petta_revision}")
    print(f"Hyperon Miner revision: {run.miner_revision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
