"""Command-line validation of parser JSONL before PeTTa graph construction."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from hybrid_miner.ingestion.semantic_documents import ingest_jsonl
from hybrid_miner.ontology import OntologyBundle


def main(argv: Sequence[str] | None = None) -> int:
    arguments = argparse.ArgumentParser(prog="python -m hybrid_miner.ingestion")
    arguments.add_argument("--bundle", required=True, type=Path)
    arguments.add_argument("--input", required=True, type=Path)
    arguments.add_argument("--accepted-output", required=True, type=Path)
    arguments.add_argument("--rejected-output", required=True, type=Path)
    options = arguments.parse_args(argv)
    try:
        bundle = OntologyBundle.load_json(options.bundle)
        result = ingest_jsonl(
            bundle=bundle,
            input_path=options.input,
            accepted_path=options.accepted_output,
            rejected_path=options.rejected_output,
        )
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Accepted: {result.accepted} -> {result.accepted_path}")
    print(f"Rejected: {result.rejected} -> {result.rejected_path}")
    return 2 if result.rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
