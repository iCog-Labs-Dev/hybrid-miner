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
    TypeConstraint,
    validate_extraction_pattern_record,
)
from hybrid_miner.contracts.semantic_document import SemanticDocument
from hybrid_miner.emission.semantic_rules import (
    rule_operand_types,
    rule_predicate_view,
    rule_source,
)
from hybrid_miner.emission.symbols import component, evidence_symbol
from hybrid_miner.ingestion.admission import excluded_documents
from hybrid_miner.mining.canonicalize_patterns import (
    Expression,
    canonicalize_pattern,
    render_expression,
)
from hybrid_miner.mining.exact_matches import (
    EvidenceEntry,
    _binding_order,
    _documents,
    _evidence_index,
    _facts,
    _json_lines,
    _linked_binding,
    _pattern_clauses,
    _RuleEvidence,
)
from hybrid_miner.mining.pattern_predicates import (
    PROVISIONAL_RELATION_TYPE,
    pattern_predicates,
)
from hybrid_miner.mining.pattern_types import constrain_variables, rule_type_constraints
from hybrid_miner.ontology import OntologyBundle


def _pattern_id(bundle: OntologyBundle, pattern: str) -> str:
    """Hash explicit identity fields without delimiter ambiguity."""
    payload = json.dumps(
        ["pattern-id-v1", bundle.bundle_id, bundle.bundle_version, pattern],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"pattern-{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _canonical_term(
    clauses: tuple[tuple[str, ...], ...], bundle: OntologyBundle
) -> str:
    heads = pattern_predicates(bundle)
    terms: list[Expression] = []
    for clause in clauses:
        predicate = heads[clause[0]]
        operands = list(clause[1:])
        if predicate.rule and predicate.name != "ExtractedRule":
            operands[1] = rule_predicate_view(operands[1], bundle)
        terms.append((predicate.name, *operands))
    bare = tuple(terms)
    expression: Expression = bare[0] if len(bare) == 1 else (",", *bare)
    return canonicalize_pattern(render_expression(expression))


def _record(
    row: dict[str, object],
    *,
    bundle: OntologyBundle,
    facts: set[str],
    documents: dict[tuple[str, str], SemanticDocument],
    evidence: dict[str, tuple[EvidenceEntry, ...]],
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
    reported_support = row.get("reported_support")
    qualifying_support = row.get("qualifying_support", reported_support)
    if (
        isinstance(qualifying_support, bool)
        or not isinstance(qualifying_support, int)
        or qualifying_support != len(bindings)
    ):
        raise ValueError("exact-match support differs from binding count")
    support_summary: dict[str, object] = {}
    if "qualifying_support" in row:
        excluded = row.get("excluded_bindings")
        minimum_support = row.get("minimum_support")
        if (
            isinstance(reported_support, bool)
            or not isinstance(reported_support, int)
            or not isinstance(excluded, list)
            or reported_support != qualifying_support + len(excluded)
            or row.get("support_policy") != "distinct-ground-facts-v1"
            or isinstance(minimum_support, bool)
            or not isinstance(minimum_support, int)
            or minimum_support < 1
            or qualifying_support < minimum_support
        ):
            raise ValueError("exact-match support policy or accounting is invalid")
        support_summary = {
            "reported_support": reported_support,
            "excluded_binding_count": len(excluded),
            "support_policy": row["support_policy"],
            "minimum_support": minimum_support,
        }
    miner_revision = row.get("miner_revision")
    if not isinstance(miner_revision, str) or not miner_revision:
        raise ValueError("exact-match row lacks a miner revision")
    petta_revision = row.get("petta_revision")
    if not isinstance(petta_revision, str) or not petta_revision:
        raise ValueError("exact-match row lacks a PeTTa revision")

    clauses, variables = _pattern_clauses(pattern, bundle)
    heads = pattern_predicates(bundle)
    signatures = bundle.signatures()
    permitted_types: dict[str, set[str]] = {}
    binding_type_trace: list[dict[str, object]] = []
    provisional: dict[int, set[tuple[str, tuple[str, ...]]]] = {}
    rule_contexts: dict[tuple[str, str, int], dict[str, object]] = {}
    evidence_by_id = {
        item.evidence_id: item for entries in evidence.values() for item in entries
    }
    source_spans: set[tuple[str, int, int]] = set()
    confidences: list[float] = []
    source_ids: set[str] = set()
    corpus_ids: set[str] = set()
    parser_versions: set[str] = set()
    model_revisions: set[str | None] = set()
    factuality_counts: dict[str, int] = {}
    seen_bindings: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    verified_bindings: list[tuple[dict[str, object], dict[str, str]]] = []
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
        verified_bindings.append((recovered, values))

    for recovered, values in sorted(
        verified_bindings, key=lambda pair: _binding_order(pair[0])
    ):
        linked = recovered["evidence"]
        if not isinstance(linked, list):
            raise ValueError("exact evidence must be a list")
        clauses_by_atom = {
            render_expression(tuple(values.get(part, part) for part in clause)): (
                clause_index,
                clause,
            )
            for clause_index, clause in enumerate(clauses)
        }
        if any(
            not isinstance(item, dict) or not isinstance(item.get("atom"), str)
            for item in linked
        ) or {item["atom"] for item in linked} != set(clauses_by_atom):
            raise ValueError("exact evidence does not cover the grounded clauses")
        corpus_id = recovered["corpus_id"]
        document_id = recovered["document_id"]
        if not isinstance(corpus_id, str) or not isinstance(document_id, str):
            raise ValueError("exact binding lacks source identity")
        document = documents[(corpus_id, document_id)]
        binding_types: dict[str, str] = {}
        binding_allowed: dict[str, set[str]] = {}
        corpus_ids.add(corpus_id)
        source_ids.add(document_id)
        parser_versions.add(document.parser.parser_version)
        model_revisions.add(document.parser.model_revision)
        # One grounded clause can have several source assertion occurrences.
        # Associate by atom rather than zipping evidence rows to pattern clauses;
        # support still counts variable bindings, not evidence occurrences.
        for linked_row in linked:
            if not isinstance(linked_row, dict):
                raise ValueError("exact evidence must be an object")
            clause_index, clause = clauses_by_atom[linked_row["atom"]]
            if linked_row.get("kind") == "rule":
                rule_row = evidence_by_id.get(str(linked_row.get("evidence_id")))
                if not isinstance(rule_row, _RuleEvidence):
                    raise ValueError("exact rule evidence has no source rule")
                predicate = heads[clause[0]]
                if not predicate.rule or linked_row["predicate"] != predicate.name:
                    raise ValueError("rule graph shape differs from exact evidence")
                constrain_variables(
                    clause[1:],
                    rule_operand_types(rule_row.rule, rule_row.fact_index),
                    rule_type_constraints(
                        rule_row.rule, rule_row.fact_index, signatures
                    ),
                    binding_types=binding_types,
                    binding_allowed=binding_allowed,
                )
                spans, confidence = rule_source(rule_row.rule, rule_row.fact_index)
                for span in (*spans, *rule_row.rule.source_spans):
                    source_spans.add((document_id, span.start, span.end))
                confidences.append(confidence)
                factuality_counts["hypothetical"] = (
                    factuality_counts.get("hypothetical", 0) + 1
                )
                rule_contexts[(corpus_id, document_id, rule_row.rule_index)] = {
                    "corpus_id": corpus_id,
                    "document_id": document_id,
                    "rule": rule_row.rule.model_dump(mode="json"),
                }
                continue
            for index in range(len(document.assertions)):
                if linked_row.get("evidence_id") == evidence_symbol(
                    corpus_id, document_id, index
                ):
                    break
            else:
                raise ValueError("exact evidence has no document assertion")
            assertion = document.assertions[index]
            predicate = heads[clause[0]]
            if predicate.name != assertion.predicate:
                raise ValueError("exact evidence predicate differs from pattern")
            operands = clause[1:]
            argument_types = assertion.argument_types
            if predicate.provisional:
                relation_id = assertion.provisional_relation_id
                if relation_id is None:
                    raise ValueError("provisional evidence lacks its relation identity")
                relation = values.get(clause[1], clause[1])
                if relation != f"rel:{component(relation_id)}":
                    raise ValueError(
                        "provisional binding differs from relation identity"
                    )
                argument_types = (PROVISIONAL_RELATION_TYPE, *argument_types)
                provisional.setdefault(clause_index, set()).add(
                    (relation_id, assertion.argument_types)
                )
            allowed = (
                tuple(frozenset((kind,)) for kind in argument_types)
                if predicate.provisional
                else signatures[assertion.predicate]
            )
            constrain_variables(
                operands,
                argument_types,
                allowed,
                binding_types=binding_types,
                binding_allowed=binding_allowed,
            )
            for span in assertion.source_spans:
                source_spans.add((document_id, span.start, span.end))
            confidences.append(assertion.confidence)
            factuality = assertion.factuality or "unclassified"
            factuality_counts[factuality] = factuality_counts.get(factuality, 0) + 1
        for variable, allowed_types in binding_allowed.items():
            permitted_types.setdefault(variable, set()).update(allowed_types)
        binding_type_trace.append(
            {
                "corpus_id": corpus_id,
                "document_id": document_id,
                "variables": values,
                "types": binding_types,
            }
        )
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
    types: dict[str, TypeConstraint] = {
        variable: next(iter(allowed)) if len(allowed) == 1 else tuple(sorted(allowed))
        for variable, allowed in permitted_types.items()
    }
    record = PatternRecord(
        contract_version="pattern-record-v1",
        id=_pattern_id(bundle, pattern),
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
            **support_summary,
            "binding_types": binding_type_trace,
            **(
                {
                    "rule_contexts": [
                        value for _, value in sorted(rule_contexts.items())
                    ],
                    "rule_fragment_semantics": (
                        "structural-subgraph-not-executable-implication"
                    ),
                }
                if rule_contexts
                else {}
            ),
            **(
                {
                    "provisional_relations": [
                        {
                            "clause_index": index,
                            "relation_argument": clauses[index][1],
                            "observed_relations": [
                                {
                                    "relation_id": relation_id,
                                    "argument_types": list(argument_types),
                                }
                                for relation_id, argument_types in sorted(observed)
                            ],
                        }
                        for index, observed in sorted(provisional.items())
                    ]
                }
                if provisional
                else {}
            ),
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
    admission_path: Path | None = None,
) -> tuple[int, int]:
    """Materialize candidates; quarantine representationally invalid matches."""

    paths: tuple[Path, ...] = (
        documents_path,
        atoms_path,
        provenance_path,
        exact_path,
        output_path,
        rejected_path,
    )
    if admission_path is not None:
        paths += (admission_path,)
    if len({path.resolve() for path in paths}) != len(paths):
        raise ValueError("input and output paths must all differ")
    if output_path.exists() or rejected_path.exists():
        raise FileExistsError("record output already exists")
    facts = _facts(atoms_path)
    excluded = excluded_documents(
        admission_path,
        bundle=bundle,
        documents_path=documents_path,
        provenance_path=provenance_path,
        atoms_path=atoms_path,
    )
    documents = _documents(documents_path, bundle, excluded)
    evidence = _evidence_index(provenance_path, facts, documents, bundle, excluded)
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
    parser.add_argument("--admission", type=Path)
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
            admission_path=options.admission,
        )
    except (OSError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Candidate records: {accepted} -> {options.output}")
    print(f"Rejected records: {rejected} -> {options.rejected_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
