"""Candidate-record construction from exact, source-linked PeTTa bindings."""

import hashlib

import pytest

from hybrid_miner.contracts.semantic_document import SemanticDocument
from hybrid_miner.emission.emit_petta import emit_petta
from hybrid_miner.emission.symbols import entity_symbol
from hybrid_miner.mining.build_pattern_records import _record
from hybrid_miner.mining.exact_matches import (
    _documents,
    _evidence_index,
    _facts,
    _linked_binding,
    _pattern_clauses,
)
from hybrid_miner.ontology import OntologyBundle


def test_builds_typed_candidate_and_rejects_changed_evidence(tmp_path) -> None:
    bundle = OntologyBundle.model_validate(
        {
            "contract_version": "ontology-bundle-v1",
            "bundle_id": "record-test",
            "bundle_version": "1",
            "ontology_versions": ["upper-v1"],
            "types": ["Event", "Entity"],
            "predicates": [
                {
                    "name": "Event",
                    "ontology_version": "upper-v1",
                    "argument_types": [["Event"]],
                },
                {
                    "name": "Agent",
                    "ontology_version": "upper-v1",
                    "argument_types": [["Event"], ["Entity"]],
                },
            ],
        }
    )
    document = SemanticDocument.model_validate(
        {
            "document_id": "doc-1",
            "corpus_id": "corpus-1",
            "source_text": "Nora sang.",
            "parser": {
                "model": "teacher",
                "parser_version": "parser-v1",
                "prompt_version": "prompt-v1",
                "ontology_versions": ["upper-v1"],
            },
            "entities": [
                {"id": "nora", "type": "Entity", "span": {"start": 0, "end": 4}},
                {"id": "singing", "type": "Event", "span": {"start": 5, "end": 9}},
            ],
            "assertions": [
                {
                    "predicate": "Event",
                    "arguments": ["singing"],
                    "argument_types": ["Event"],
                    "status": "extracted-hypothesis",
                    "factuality": "asserted",
                    "confidence": 0.95,
                    "source_spans": [{"start": 5, "end": 9}],
                },
                {
                    "predicate": "Agent",
                    "arguments": ["singing", "nora"],
                    "argument_types": ["Event", "Entity"],
                    "status": "extracted-hypothesis",
                    "factuality": "asserted",
                    "confidence": 0.9,
                    "source_spans": [{"start": 0, "end": 9}],
                },
            ],
        }
    )
    source = tmp_path / "documents.jsonl"
    source.write_text(document.canonical_json() + "\n", encoding="utf-8")
    atoms = tmp_path / "facts.metta"
    provenance = tmp_path / "provenance.jsonl"
    emit_petta(
        bundle=bundle,
        input_path=source,
        atoms_path=atoms,
        metadata_path=tmp_path / "metadata.metta",
        provenance_path=provenance,
    )
    facts = _facts(atoms)
    documents = _documents(source, bundle)
    evidence = _evidence_index(provenance, facts, documents, bundle)
    pattern = "(, (Agent@upper-v1 $v0 $v1) (Event@upper-v1 $v0))"
    clauses, variables = _pattern_clauses(pattern, bundle)
    binding = _linked_binding(
        clauses,
        variables,
        (
            entity_symbol("corpus-1", "doc-1", "singing"),
            entity_symbol("corpus-1", "doc-1", "nora"),
        ),
        facts=facts,
        evidence=evidence,
        documents=documents,
    )
    digest = hashlib.sha256(atoms.read_bytes()).hexdigest()
    row = {
        "pattern": pattern,
        "exact_bindings": [binding],
        "reported_support": 1,
        "bundle_id": "record-test",
        "bundle_version": "1",
        "graph_sha256": digest,
        "miner_revision": "miner-revision-1",
        "petta_revision": "petta-revision-1",
    }
    record = _record(
        row,
        bundle=bundle,
        facts=facts,
        documents=documents,
        evidence=evidence,
        graph_sha256=digest,
    )
    assert record.canonical_term == "(, (Agent $v0 $v1) (Event $v0))"
    assert record.ontology_groundings == ("upper-v1:Agent", "upper-v1:Event")
    assert record.type_constraints == {"$v0": "Event", "$v1": "Entity"}
    assert record.cheap_features["support"] == 1
    assert record.status == "candidate"

    row["exact_bindings"][0]["evidence"][0]["source_spans"][0]["text"] = "wrong"
    with pytest.raises(ValueError, match="disagrees with source evidence"):
        _record(
            row,
            bundle=bundle,
            facts=facts,
            documents=documents,
            evidence=evidence,
            graph_sha256=digest,
        )
