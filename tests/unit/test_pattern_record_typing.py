"""Preserve valid type alternatives without relaxing per-binding constraints."""

import hashlib
import json

import pytest

from hybrid_miner.contracts import PatternRecord, SemanticDocument
from hybrid_miner.emission.emit_petta import emit_petta
from hybrid_miner.emission.symbols import entity_symbol
from hybrid_miner.mining.build_pattern_records import build_pattern_records
from hybrid_miner.mining.exact_matches import (
    _documents,
    _evidence_index,
    _facts,
    _linked_binding,
    _pattern_clauses,
)
from hybrid_miner.mining.pattern_types import constrain_variables
from hybrid_miner.ontology import OntologyBundle


@pytest.mark.parametrize(
    ("kinds", "narrow"),
    [
        (("TypeA", "TypeA"), False),
        (("TypeB", "TypeB"), False),
        (("TypeA", "TypeB"), False),
        (("TypeB", "TypeA"), False),
        (("TypeA", "TypeA"), True),
    ],
)
def test_candidate_preserves_valid_type_alternatives(tmp_path, kinds, narrow):
    bundle = OntologyBundle.model_validate(
        {
            "contract_version": "ontology-bundle-v1",
            "bundle_id": "candidate-types",
            "bundle_version": "1",
            "ontology_versions": ["test-v1"],
            "types": ["TypeA", "TypeB"],
            "predicates": [
                {
                    "name": name,
                    "ontology_version": "test-v1",
                    "argument_types": [
                        ["TypeA"] if narrow and name == "Tagged" else ["TypeA", "TypeB"]
                    ],
                }
                for name in ("Observed", "Tagged")
            ],
        }
    )
    source_documents = []
    for number, kind in enumerate(kinds, 1):
        document = SemanticDocument.model_validate(
            {
                "document_id": f"doc-{number}",
                "corpus_id": "candidate-types",
                "source_text": "N.",
                "parser": {
                    "model": "example",
                    "parser_version": "1",
                    "prompt_version": "1",
                    "ontology_versions": ["test-v1"],
                },
                "entities": [{"id": "n", "type": kind, "span": {"start": 0, "end": 1}}],
                "assertions": [
                    {
                        "predicate": name,
                        "arguments": ["n"],
                        "argument_types": [kind],
                        "status": "extracted-hypothesis",
                        "confidence": 0.9,
                        "source_spans": [{"start": 0, "end": 1}],
                    }
                    for name in ("Observed", "Tagged")
                ],
            }
        )
        assert bundle.validate_document(document) == document
        source_documents.append(document)
    source = tmp_path / "documents.jsonl"
    source.write_text(
        "".join(doc.canonical_json() + "\n" for doc in source_documents),
        encoding="utf-8",
    )
    atoms, provenance = tmp_path / "facts.metta", tmp_path / "provenance.jsonl"
    emission = emit_petta(
        bundle=bundle,
        input_path=source,
        atoms_path=atoms,
        metadata_path=tmp_path / "metadata.metta",
        provenance_path=provenance,
    )
    assert (emission.assertions, emission.facts) == (4, 4)
    facts, documents = _facts(atoms), _documents(source, bundle)
    evidence = _evidence_index(provenance, facts, documents, bundle)
    pattern = "(, (Observed@test-v1 $v0) (Tagged@test-v1 $v0))"
    clauses, variables = _pattern_clauses(pattern, bundle)
    bindings = [
        _linked_binding(
            clauses,
            variables,
            (entity_symbol("candidate-types", doc.document_id, "n"),),
            facts=facts,
            evidence=evidence,
            documents=documents,
        )
        for doc in source_documents
    ]
    exact = tmp_path / "exact.jsonl"
    exact.write_text(
        json.dumps(
            {
                "pattern": pattern,
                "exact_bindings": bindings,
                "reported_support": 2,
                "qualifying_support": 2,
                "minimum_support": 2,
                "excluded_bindings": [],
                "support_policy": "distinct-ground-facts-v1",
                "bundle_id": bundle.bundle_id,
                "bundle_version": bundle.bundle_version,
                "graph_sha256": hashlib.sha256(atoms.read_bytes()).hexdigest(),
                "miner_revision": "miner",
                "petta_revision": "petta",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output, rejected = tmp_path / "records.jsonl", tmp_path / "rejected.jsonl"
    options = dict(
        bundle=bundle,
        documents_path=source,
        atoms_path=atoms,
        provenance_path=provenance,
        exact_path=exact,
        output_path=output,
        rejected_path=rejected,
    )
    assert build_pattern_records(**options) == (1, 0)
    assert rejected.read_bytes() == b""
    record = PatternRecord.from_canonical_json(output.read_text(encoding="utf-8"))
    assert record.type_constraints == {"$v0": "TypeA" if narrow else ("TypeA", "TypeB")}
    assert record.expensive_endpoint == {"observed_support": 2}
    assert record.provenance.source_document_ids == ("doc-1", "doc-2")
    assert len(record.parser_confidences) == 4
    assert record.expensive_trace["binding_types"] == [
        {
            "corpus_id": "candidate-types",
            "document_id": doc.document_id,
            "variables": {
                "$v0": entity_symbol("candidate-types", doc.document_id, "n")
            },
            "types": {"$v0": kind},
        }
        for doc, kind in zip(source_documents, kinds, strict=True)
    ]
    assert PatternRecord.from_canonical_json(record.to_canonical_json()) == record

    row = json.loads(exact.read_text(encoding="utf-8"))
    row["exact_bindings"].reverse()
    exact.write_text(json.dumps(row) + "\n", encoding="utf-8")
    reordered_output = tmp_path / "reordered.jsonl"
    options.update(
        output_path=reordered_output, rejected_path=tmp_path / "rejected2.jsonl"
    )
    assert build_pattern_records(**options) == (1, 0)
    assert reordered_output.read_bytes() == output.read_bytes()
    assert len(_documents(source, bundle)) == 2
    assert len(json.loads(exact.read_text(encoding="utf-8"))["exact_bindings"]) == 2


def test_variable_constraints_intersect_positions_not_union_them():
    actual, allowed = {}, {}
    constrain_variables(
        ("$v0", "$v0"),
        ("TypeA", "TypeA"),
        (frozenset(("TypeA", "TypeB")), frozenset(("TypeA",))),
        binding_types=actual,
        binding_allowed=allowed,
    )
    assert actual == {"$v0": "TypeA"}
    assert allowed == {"$v0": {"TypeA"}}


@pytest.mark.parametrize(
    ("concrete", "permitted", "message"),
    [
        (("TypeC",), (("TypeA", "TypeB"),), "violates its signature"),
        (("TypeB",), (("TypeA",),), "violates its signature"),
        (
            ("TypeA", "TypeB"),
            (("TypeA", "TypeB"), ("TypeA", "TypeB")),
            "conflicting types within a binding",
        ),
    ],
)
def test_invalid_types_are_not_rescued_by_an_alternative(concrete, permitted, message):
    with pytest.raises(ValueError, match=message):
        constrain_variables(
            ("$v0",) * len(concrete),
            concrete,
            tuple(frozenset(types) for types in permitted),
            binding_types={},
            binding_allowed={},
        )
