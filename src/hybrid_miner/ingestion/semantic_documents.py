"""Stream parser documents through the miner's versioned ontology contract."""

from __future__ import annotations

import json
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from hybrid_miner.contracts.semantic_document import SemanticDocument
from hybrid_miner.ontology import OntologyBundle


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Counts and inspectable artifacts from a completed validation pass."""

    accepted: int
    rejected: int
    accepted_path: Path
    rejected_path: Path


def ingest_jsonl(
    *,
    bundle: OntologyBundle,
    input_path: str | Path,
    accepted_path: str | Path,
    rejected_path: str | Path,
) -> IngestionResult:
    """Validate each source line, preserving rejected records for audit.

    The accepted artifact contains canonical SemanticDocument JSONL. No PeTTa
    atoms are emitted here; downstream graph construction must use only this
    validated artifact and the same ontology bundle.
    """

    source = Path(input_path)
    accepted = Path(accepted_path)
    rejected = Path(rejected_path)
    if len({path.resolve() for path in (source, accepted, rejected)}) != 3:
        raise ValueError("Input, accepted, and rejected paths must differ")
    if not source.is_file():
        raise FileNotFoundError(f"Semantic-document JSONL not found: {source}")
    for output in (accepted, rejected):
        if output.exists():
            raise FileExistsError(f"Output already exists: {output}")

    accepted.parent.mkdir(parents=True, exist_ok=True)
    rejected.parent.mkdir(parents=True, exist_ok=True)
    accepted_count = 0
    rejected_count = 0
    seen: set[tuple[str, str]] = set()
    created: list[Path] = []
    try:
        with ExitStack() as files:
            reader = files.enter_context(source.open("r", encoding="utf-8"))
            accepted_writer = files.enter_context(
                accepted.open("x", encoding="utf-8", newline="\n")
            )
            created.append(accepted)
            rejected_writer = files.enter_context(
                rejected.open("x", encoding="utf-8", newline="\n")
            )
            created.append(rejected)
            for line_number, line in enumerate(reader, start=1):
                raw = line.removesuffix("\n").removesuffix("\r")
                try:
                    if not raw.strip():
                        raise ValueError("Empty JSONL record")
                    document = SemanticDocument.model_validate_json(raw)
                    bundle.validate_document(document)
                    identity = (document.corpus_id, document.document_id)
                    if identity in seen:
                        raise ValueError(f"Duplicate semantic document {identity!r}")
                    seen.add(identity)
                except (ValidationError, ValueError) as error:
                    rejected_writer.write(
                        json.dumps(
                            {
                                "line_number": line_number,
                                "raw_line": raw,
                                "error": str(error),
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + "\n"
                    )
                    rejected_count += 1
                    continue
                accepted_writer.write(document.canonical_json() + "\n")
                accepted_count += 1
    except BaseException:
        for output in created:
            output.unlink(missing_ok=True)
        raise

    return IngestionResult(accepted_count, rejected_count, accepted, rejected)
