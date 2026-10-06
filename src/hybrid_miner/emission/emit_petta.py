"""Encode validated S1 assertions as loadable PeTTa facts.

The graph file contains only matchable relation atoms. A separate PeTTa
metadata space and a JSONL sidecar retain extraction evidence and provenance.
This is an interchange encoder, not PeTTa's graph canonicalizer or miner.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from hybrid_miner.contracts.semantic_document import SemanticDocument
from hybrid_miner.ontology import OntologyBundle


@dataclass(frozen=True, slots=True)
class EmissionResult:
    """Artifacts and number of assertion occurrences written."""

    assertions: int
    facts: int
    atoms_path: Path
    metadata_path: Path
    provenance_path: Path


def _component(value: str) -> str:
    """Escape delimiters and PeTTa syntax without obscuring ordinary names."""

    return quote(value, safe="-._~")


def _entity_symbol(document: SemanticDocument, entity_id: str) -> str:
    return ":".join(
        (
            "ent",
            _component(document.corpus_id),
            _component(document.document_id),
            _component(entity_id),
        )
    )


def _predicate_symbol(predicate: str, version: str) -> str:
    return f"{_component(predicate)}@{_component(version)}"


def _evidence_symbol(document: SemanticDocument, index: int) -> str:
    return ":".join(
        (
            "ev",
            _component(document.corpus_id),
            _component(document.document_id),
            str(index),
        )
    )


def _fact(
    document: SemanticDocument,
    assertion_index: int,
    versions: dict[str, str],
    bundle: OntologyBundle,
) -> tuple[str, dict[str, object]]:
    assertion = document.assertions[assertion_index]
    if assertion.predicate == "ProvisionalRelation":
        version = f"{bundle.bundle_id}@{bundle.bundle_version}"
        assert assertion.provisional_relation_id is not None
        relation = f"rel:{_component(assertion.provisional_relation_id)}"
        operands = [relation]
    else:
        version = versions[assertion.predicate]
        operands = []
    head = _predicate_symbol(assertion.predicate, version)
    operands.extend(
        _entity_symbol(document, entity_id) for entity_id in assertion.arguments
    )
    fact = f"({head} {' '.join(operands)})"
    evidence: dict[str, object] = {
        "atom": fact,
        "assertion_index": assertion_index,
        "evidence_id": _evidence_symbol(document, assertion_index),
        "bundle_id": bundle.bundle_id,
        "bundle_version": bundle.bundle_version,
        "corpus_id": document.corpus_id,
        "document_id": document.document_id,
        "ontology_version": version,
        "parser": document.parser.model_dump(mode="json"),
        "assertion": assertion.model_dump(mode="json"),
    }
    return fact, evidence


def _assertion_metadata(
    document: SemanticDocument, index: int, fact: str, version: str
) -> list[str]:
    assertion = document.assertions[index]
    evidence_id = _evidence_symbol(document, index)
    lines = [
        f"(AssertionEvidence {evidence_id} {fact} {assertion.status} "
        f"{repr(assertion.confidence)})",
        f"(ParserProvenance {evidence_id} {_component(document.parser.model)} "
        f"{_component(document.parser.parser_version)} "
        f"{_component(document.parser.prompt_version)})",
        f"(PredicateOntology {evidence_id} {_component(version)})",
    ]
    lines.extend(
        f"(ParserOntology {evidence_id} {_component(ontology_version)})"
        for ontology_version in document.parser.ontology_versions
    )
    lines.extend(
        f"(SourceSpan {evidence_id} {span.start} {span.end})"
        for span in assertion.source_spans
    )
    lines.extend(
        f"(Alternative {evidence_id} {_component(alternative)})"
        for alternative in assertion.alternatives
    )
    if assertion.factuality is not None:
        lines.append(f"(Factuality {evidence_id} {assertion.factuality})")
    if assertion.conditional_on is not None:
        lines.append(
            f"(ConditionalOn {evidence_id} "
            f"{_entity_symbol(document, assertion.conditional_on)})"
        )
    return lines


def emit_petta(
    *,
    bundle: OntologyBundle,
    input_path: str | Path,
    atoms_path: str | Path,
    metadata_path: str | Path,
    provenance_path: str | Path,
) -> EmissionResult:
    """Revalidate accepted JSONL and emit deterministic facts plus evidence.

    A source line with a bad contract, mismatched ontology, or repeated document
    ID aborts emission. Existing artifacts are never overwritten.
    """

    source = Path(input_path)
    atoms = Path(atoms_path)
    metadata = Path(metadata_path)
    provenance = Path(provenance_path)
    if len({path.resolve() for path in (source, atoms, metadata, provenance)}) != 4:
        raise ValueError("Input, atoms, metadata, and provenance paths must differ")
    if not source.is_file():
        raise FileNotFoundError(f"Validated document JSONL not found: {source}")
    for output in (atoms, metadata, provenance):
        if output.exists():
            raise FileExistsError(f"Output already exists: {output}")

    versions = {item.name: item.ontology_version for item in bundle.predicates}
    atoms.parent.mkdir(parents=True, exist_ok=True)
    metadata.parent.mkdir(parents=True, exist_ok=True)
    provenance.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    fact_count = 0
    seen: set[tuple[str, str]] = set()
    emitted_facts: set[str] = set()
    created: list[Path] = []
    try:
        with ExitStack() as files:
            reader = files.enter_context(source.open("r", encoding="utf-8"))
            atom_writer = files.enter_context(
                atoms.open("x", encoding="utf-8", newline="\n")
            )
            created.append(atoms)
            metadata_writer = files.enter_context(
                metadata.open("x", encoding="utf-8", newline="\n")
            )
            created.append(metadata)
            evidence_writer = files.enter_context(
                provenance.open("x", encoding="utf-8", newline="\n")
            )
            created.append(provenance)
            for predicate in bundle.predicates:
                head = _predicate_symbol(predicate.name, predicate.ontology_version)
                for position, allowed in enumerate(predicate.argument_types):
                    for entity_type in allowed:
                        metadata_writer.write(
                            f"(AllowedType {head} {position} "
                            f"{_component(entity_type)})\n"
                        )
            for line_number, line in enumerate(reader, start=1):
                try:
                    document = SemanticDocument.model_validate_json(line)
                    bundle.validate_document(document)
                    identity = (document.corpus_id, document.document_id)
                    if identity in seen:
                        raise ValueError(f"duplicate document {identity!r}")
                    seen.add(identity)
                except ValueError as error:
                    raise ValueError(f"line {line_number}: {error}") from error
                for entity in document.entities:
                    metadata_writer.write(
                        f"(EntityType {_entity_symbol(document, entity.id)} "
                        f"{_component(entity.type)})\n"
                    )
                for index in range(len(document.assertions)):
                    fact, evidence = _fact(document, index, versions, bundle)
                    if fact not in emitted_facts:
                        atom_writer.write(fact + "\n")
                        emitted_facts.add(fact)
                        fact_count += 1
                    version = str(evidence["ontology_version"])
                    for metadata_atom in _assertion_metadata(
                        document, index, fact, version
                    ):
                        metadata_writer.write(metadata_atom + "\n")
                    evidence_writer.write(
                        json.dumps(
                            evidence,
                            allow_nan=False,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                        + "\n"
                    )
                    count += 1
    except BaseException:
        for output in created:
            output.unlink(missing_ok=True)
        raise
    return EmissionResult(count, fact_count, atoms, metadata, provenance)
