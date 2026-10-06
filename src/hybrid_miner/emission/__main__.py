"""Emit PeTTa facts and a provenance sidecar from validated JSONL."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from hybrid_miner.emission.emit_petta import emit_petta
from hybrid_miner.ontology import OntologyBundle


def main(argv: Sequence[str] | None = None) -> int:
    arguments = argparse.ArgumentParser(prog="python -m hybrid_miner.emission")
    arguments.add_argument("--bundle", required=True, type=Path)
    arguments.add_argument("--input", required=True, type=Path)
    arguments.add_argument("--atoms-output", required=True, type=Path)
    arguments.add_argument("--metadata-output", required=True, type=Path)
    arguments.add_argument("--provenance-output", required=True, type=Path)
    options = arguments.parse_args(argv)
    try:
        result = emit_petta(
            bundle=OntologyBundle.load_json(options.bundle),
            input_path=options.input,
            atoms_path=options.atoms_output,
            metadata_path=options.metadata_output,
            provenance_path=options.provenance_output,
        )
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Facts: {result.facts} -> {result.atoms_path}")
    print(f"Assertion evidence: {result.assertions} -> {result.metadata_path}")
    print(f"Provenance: {result.provenance_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
