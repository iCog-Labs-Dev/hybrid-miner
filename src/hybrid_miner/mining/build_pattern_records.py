"""Build source-linked candidate records from verified exact PeTTa matches."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path

from hybrid_miner.contracts.pattern_record import (
    PatternProvenance,
    PatternRecord,
    SourceSpan,
    validate_extraction_pattern_record,
)
from hybrid_miner.contracts.semantic_document import SemanticDocument
from hybrid_miner.emission.symbols import evidence_symbol, predicate_symbol
from hybrid_miner.mining.canonicalize_patterns import (
    Expression,
    canonicalize_pattern,
    render_expression,
)
from hybrid_miner.mining.exact_matches import (
    _documents,
    _Evidence,
    _evidence_index,
    _facts,
    _json_lines,
    _linked_binding,
    _pattern_clauses,
)
from hybrid_miner.ontology import OntologyBundle


def _canonical_term(
    clauses: tuple[tuple[str, ...], ...], bundle: OntologyBundle
) -> str:
    heads = {
        predicate_symbol(item.name, item.ontology_version): item.name
        for item in bundle.predicates
    }
    bare: tuple[Expression, ...] = tuple(
        (heads[clause[0]], *clause[1:]) for clause in clauses
    )
    expression: Expression = bare[0] if len(bare) == 1 else (",", *bare)
    return canonicalize_pattern(render_expression(expression))


def _record(
    row: dict[str, object],
    *,
    bundle: OntologyBundle,
    facts: set[str],
    documents: dict[tuple[str, str], SemanticDocument],
    evidence: dict[str, tuple[_Evidence, ...]],
    graph_sha256: str,
) -> PatternRecord:
    pattern = row.get("pattern")
    bindings = row.get("exact_bindings")
    if not isinstance(pattern, str) or not isinstance(bindings, list) or not bindings:
        raise ValueError("exact-match row needs a pattern and nonempty bindings")
    if canonicalize_pattern(pattern) != pattern:
        raise ValueError("exact-match pattern is not canonical")
    if (
        row.get("bundle_id") != bundle.bundle_id
        or row.get("bundle_version") != bundle.bundle_version
    ):
        raise ValueError("exact-match bundle identity differs")
    if row.get("graph_sha256") != graph_sha256:
        raise ValueError("exact-match graph digest differs")
    if row.get("reported_support") != len(bindings):
        raise ValueError("exact-match support differs from binding count")
    miner_revision = row.get("miner_revision")
    if not isinstance(miner_revision, str) or not miner_revision:
        raise ValueError("exact-match row lacks a miner revision")
    petta_revision = row.get("petta_revision")
    if not isinstance(petta_revision, str) or not petta_revision:
        raise ValueError("exact-match row lacks a PeTTa revision")

    clauses, variables = _pattern_clauses(pattern, bundle)
    heads = {
        predicate_symbol(item.name, item.ontology_version): item
        for item in bundle.predicates
    }
    types: dict[str, str] = {}
    source_spans: set[tuple[str, int, int]] = set()
    confidences: list[float] = []
    source_ids: set[str] = set()
    corpus_ids: set[str] = set()
    parser_versions: set[str] = set()
    model_revisions: set[str | None] = set()
    factuality_counts: dict[str, int] = {}
    seen_bindings: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    for binding in bindings:
        if not isinstance(binding, dict):
            raise ValueError("exact binding must be an object")
        values = binding.get("variables")
        if (
            not isinstance(values, dict)
            or set(values) != set(variables)
            or any(not isinstance(value, str) for value in values.values())
        ):
            raise ValueError("exact binding has invalid variable assignments")
        identity = (str(binding.get("document_id")), tuple(sorted(values.items())))
        if identity in seen_bindings:
            raise ValueError("exact-match row repeats a binding")
        seen_bindings.add(identity)
        recovered = _linked_binding(
            clauses,
            variables,
            tuple(values[variable] for variable in variables),
            facts=facts,
            evidence=evidence,
            documents=documents,
        )
        if json.dumps(recovered, sort_keys=True) != json.dumps(binding, sort_keys=True):
            raise ValueError("exact binding disagrees with source evidence")
        linked = recovered["evidence"]
        if not isinstance(linked, list) or len(linked) != len(clauses):
            raise ValueError("one clause has ambiguous source evidence")
        corpus_id = recovered["corpus_id"]
        document_id = recovered["document_id"]
        if not isinstance(corpus_id, str) or not isinstance(document_id, str):
            raise ValueError("exact binding lacks source identity")
        document = documents[(corpus_id, document_id)]
        corpus_ids.add(corpus_id)
        source_ids.add(document_id)
        parser_versions.add(document.parser.parser_version)
        model_revisions.add(document.parser.model_revision)
        for clause, linked_row in zip(clauses, linked, strict=True):
            if not isinstance(linked_row, dict):
                raise ValueError("exact evidence must be an object")
            for index in range(len(document.assertions)):
                if linked_row.get("evidence_id") == evidence_symbol(
                    corpus_id, document_id, index
                ):
                    break
            else:
                raise ValueError("exact evidence has no document assertion")
            assertion = document.assertions[index]
            if heads[clause[0]].name != assertion.predicate:
                raise ValueError("exact evidence predicate differs from pattern")
            for variable, argument_type in zip(
                clause[1:], assertion.argument_types, strict=True
            ):
                if not variable.startswith("$"):
                    continue
                previous = types.setdefault(variable, argument_type)
                if previous != argument_type:
                    raise ValueError(f"variable {variable} has conflicting types")
            for span in assertion.source_spans:
                source_spans.add((document_id, span.start, span.end))
            confidences.append(assertion.confidence)
            factuality = assertion.factuality or "unclassified"
            factuality_counts[factuality] = factuality_counts.get(factuality, 0) + 1
    if len(corpus_ids) != 1 or len(parser_versions) != 1:
        raise ValueError("candidate spans incompatible corpora or parser versions")
    ontology_groundings = tuple(
        sorted(
            {
                f"{heads[clause[0]].ontology_version}:{heads[clause[0]].name}"
                for clause in clauses
            }
        )
    )
    identifier = hashlib.sha256(
        f"{bundle.bundle_id}:{bundle.bundle_version}:{pattern}".encode()
    ).hexdigest()
    record = PatternRecord(
        contract_version="pattern-record-v1",
        id=f"pattern-{identifier}",
        canonical_term=_canonical_term(clauses, bundle),
        interface_variables=variables,
        existential_variables=(),
        type_constraints=types,
        semantic_level="extracted",
        ontology_groundings=ontology_groundings,
        semantic_consequences=None,
        source_spans=tuple(
            SourceSpan(document_id=doc, start=start, end=end)
            for doc, start, end in sorted(source_spans)
        ),
        parser_confidences=tuple(confidences),
        derivation_or_definition=None,
        provenance=PatternProvenance(
            corpus_id=next(iter(corpus_ids)),
            parser_version=next(iter(parser_versions)),
            ontology_versions=bundle.ontology_versions,
            source_document_ids=tuple(sorted(source_ids)),
            miner_version=miner_revision,
            model_version=(
                next(iter(model_revisions)) if len(model_revisions) == 1 else None
            ),
        ),
        generator_history=("hyperon-miner:depth-zero",),
        cheap_features={
            "clause_count": len(clauses),
            "variable_count": len(variables),
        },
        cheap_endpoint=None,
        expensive_endpoint={"observed_support": len(bindings)},
        expensive_trace={
            "factuality_counts": factuality_counts,
            "minimum_parser_confidence": min(confidences),
            "qualified_pattern": pattern,
            "graph_sha256": row["graph_sha256"],
            "petta_revision": petta_revision,
        },
        controller_prediction=None,
        controller_uncertainty=None,
        causal_statistics=None,
        runtime_cost=None,
        status="candidate",
    )
    return validate_extraction_pattern_record(record)


def build_pattern_records(
    *,
    bundle: OntologyBundle,
    documents_path: Path,
    atoms_path: Path,
    provenance_path: Path,
    exact_path: Path,
    output_path: Path,
    rejected_path: Path,
) -> tuple[int, int]:
    """Materialize candidates; quarantine representationally invalid matches."""

    paths = (
        documents_path,
        atoms_path,
        provenance_path,
        exact_path,
        output_path,
        rejected_path,
    )
    if len({path.resolve() for path in paths}) != len(paths):
        raise ValueError("input and output paths must all differ")
    if output_path.exists() or rejected_path.exists():
        raise FileExistsError("record output already exists")
    facts = _facts(atoms_path)
    documents = _documents(documents_path, bundle)
    evidence = _evidence_index(provenance_path, facts, documents, bundle)
    graph_sha256 = hashlib.sha256(atoms_path.read_bytes()).hexdigest()
    accepted: list[str] = []
    rejected: list[str] = []
    for number, line in enumerate(_json_lines(exact_path), start=1):
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"exact-match line {number} is not an object")
        try:
            accepted.append(
                _record(
                    row,
                    bundle=bundle,
                    facts=facts,
                    documents=documents,
                    evidence=evidence,
                    graph_sha256=graph_sha256,
                ).to_canonical_json()
            )
        except (ValueError, KeyError) as error:
            rejected.append(
                json.dumps(
                    {
                        "line_number": number,
                        "pattern": row.get("pattern"),
                        "reason": str(error),
                    },
                    sort_keys=True,
                )
            )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rejected_path.parent.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        with ExitStack() as stack:
            accepted_file = stack.enter_context(output_path.open("x", encoding="utf-8"))
            created.append(output_path)
            rejected_file = stack.enter_context(
                rejected_path.open("x", encoding="utf-8")
            )
            created.append(rejected_path)
            accepted_file.writelines(item + "\n" for item in accepted)
            rejected_file.writelines(item + "\n" for item in rejected)
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return len(accepted), len(rejected)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hybrid_miner.mining.build_pattern_records"
    )
    for name in (
        "bundle",
        "documents",
        "atoms",
        "provenance",
        "exact",
        "output",
        "rejected-output",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    options = parser.parse_args(argv)
    try:
        accepted, rejected = build_pattern_records(
            bundle=OntologyBundle.load_json(options.bundle),
            documents_path=options.documents,
            atoms_path=options.atoms,
            provenance_path=options.provenance,
            exact_path=options.exact,
            output_path=options.output,
            rejected_path=options.rejected_output,
        )
    except (OSError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Candidate records: {accepted} -> {options.output}")
    print(f"Rejected records: {rejected} -> {options.rejected_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
