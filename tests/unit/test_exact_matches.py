"""Source-evidence and degeneracy checks for mined PeTTa bindings."""

import hashlib
import json

import pytest

from hybrid_miner.contracts.semantic_document import SemanticDocument
from hybrid_miner.emission.emit_petta import emit_petta
from hybrid_miner.emission.symbols import entity_symbol
from hybrid_miner.mining import exact_matches
from hybrid_miner.mining.exact_matches import (
    _documents,
    _evidence_index,
    _facts,
    _linked_binding,
    _pattern_clauses,
)
from hybrid_miner.ontology import OntologyBundle


def _bundle() -> OntologyBundle:
    return OntologyBundle.model_validate(
        {
            "contract_version": "ontology-bundle-v1",
            "bundle_id": "evidence-test",
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


def _document() -> SemanticDocument:
    return SemanticDocument.model_validate(
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


def _artifacts(tmp_path, document=None):
    source = tmp_path / "documents.jsonl"
    source.write_text(
        (document or _document()).canonical_json() + "\n", encoding="utf-8"
    )
    atoms = tmp_path / "facts.metta"
    provenance = tmp_path / "provenance.jsonl"
    emit_petta(
        bundle=_bundle(),
        input_path=source,
        atoms_path=atoms,
        metadata_path=tmp_path / "metadata.metta",
        provenance_path=provenance,
    )
    facts = _facts(atoms)
    documents = _documents(source, _bundle())
    evidence = _evidence_index(provenance, facts, documents, _bundle())
    return facts, documents, evidence, provenance


def test_exact_binding_recovers_source_anchored_evidence(tmp_path) -> None:
    facts, documents, evidence, _ = _artifacts(tmp_path)
    pattern = "(, (Agent@upper-v1 $v0 $v1) (Event@upper-v1 $v0))"
    clauses, variables = _pattern_clauses(pattern, _bundle())
    linked = _linked_binding(
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

    assert linked["document_id"] == "doc-1"
    assert [item["evidence_id"] for item in linked["evidence"]] == [
        "ev:corpus-1:doc-1:1",
        "ev:corpus-1:doc-1:0",
    ]
    assert [item["source_spans"][0]["text"] for item in linked["evidence"]] == [
        "Nora sang",
        "sang",
    ]


def test_rejects_conjunction_reusing_the_same_fact(tmp_path) -> None:
    facts, documents, evidence, _ = _artifacts(tmp_path)
    clauses, variables = _pattern_clauses(
        "(, (Agent@upper-v1 $v0 $v1) (Agent@upper-v1 $v0 $v2))", _bundle()
    )
    with pytest.raises(ValueError, match="reuses a ground fact"):
        _linked_binding(
            clauses,
            variables,
            (
                entity_symbol("corpus-1", "doc-1", "singing"),
                entity_symbol("corpus-1", "doc-1", "nora"),
                entity_symbol("corpus-1", "doc-1", "nora"),
            ),
            facts=facts,
            evidence=evidence,
            documents=documents,
        )


def test_rejects_provenance_that_disagrees_with_document(tmp_path) -> None:
    facts, documents, _, provenance = _artifacts(tmp_path)
    rows = [
        json.loads(line) for line in provenance.read_text(encoding="utf-8").splitlines()
    ]
    rows[0]["assertion"]["source_spans"] = [{"start": 0, "end": 4}]
    provenance.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="disagrees with validated inputs"):
        _evidence_index(provenance, facts, documents, _bundle())


@pytest.mark.parametrize(
    ("mode", "minimum", "expected"),
    [
        ("mixed", 2, (1, 0)),
        ("mixed", 3, (0, 1)),
        ("collapsed", 1, (0, 1)),
        ("invalid", 1, (0, 1)),
        ("one", None, (0, 1)),
        ("one", 1, (1, 0)),
        ("single", None, (0, 1)),
    ],
)
def test_filters_bindings_without_discarding_valid_candidate(
    tmp_path, monkeypatch, mode, minimum, expected
) -> None:
    data = _document().model_dump(mode="json")
    data["source_text"] = "Nora sang with Sam."
    data["entities"].append(
        {"id": "sam", "type": "Entity", "span": {"start": 15, "end": 18}}
    )
    assertion = dict(data["assertions"][1])
    assertion["arguments"] = ["singing", "sam"]
    assertion["source_spans"] = [{"start": 0, "end": 18}]
    data["assertions"].append(assertion)
    _artifacts(tmp_path, SemanticDocument.model_validate(data))
    event, nora, sam = (
        entity_symbol("corpus-1", "doc-1", name) for name in ("singing", "nora", "sam")
    )
    bindings = [(event, nora, nora), (event, sam, sam)]
    if mode != "collapsed":
        bindings.extend([(event, nora, sam), (event, sam, nora)])
    if mode == "invalid":
        bindings.append((event, nora, "missing-entity"))
    if mode == "one":
        bindings = [(event, nora, nora), (event, nora, sam)]
    if mode == "single":
        bindings = [(event, nora, sam)]
    pattern = "(, (Agent@upper-v1 $v0 $v1) (Agent@upper-v1 $v0 $v2))"
    support = tmp_path / "support.jsonl"
    support.write_text(
        json.dumps(
            {
                "pattern": pattern,
                "support": len(bindings),
                "graph_sha256": hashlib.sha256(
                    (tmp_path / "facts.metta").read_bytes()
                ).hexdigest(),
                "petta_revision": "runtime",
                "miner_revision": "runtime",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(exact_matches, "_revision", lambda root: "runtime")
    monkeypatch.setattr(
        exact_matches, "_query_bindings", lambda *a, **kw: {0: bindings}
    )
    output, rejected = tmp_path / "exact.jsonl", tmp_path / "rejected.jsonl"
    assert (
        exact_matches.recover_exact_matches(
            bundle=_bundle(),
            documents_path=tmp_path / "documents.jsonl",
            atoms_path=tmp_path / "facts.metta",
            provenance_path=tmp_path / "provenance.jsonl",
            support_path=support,
            petta_root=tmp_path,
            miner_root=tmp_path,
            output_path=output,
            rejected_path=rejected,
            **({} if minimum is None else {"min_support": minimum}),
        )
        == expected
    )
    row = json.loads((output if expected[0] else rejected).read_text(encoding="utf-8"))
    assert row["reported_support"] == len(bindings)
    assert row["qualifying_support"] == (
        0 if mode == "collapsed" else 1 if mode in {"one", "single"} else 2
    )
    assert len(row["excluded_bindings"]) == (
        0 if mode == "single" else 1 if mode == "one" else 2
    )
    assert row["minimum_support"] == (2 if minimum is None else minimum)
    assert row["support_policy"] == "distinct-ground-facts-v1"
    if expected[0]:
        assert len(row["exact_bindings"]) == (1 if mode == "one" else 2)
    if mode == "invalid":
        assert "does not resolve to emitted facts" in row["reason"]
    if mode == "single":
        assert "reuses a ground fact" not in row["reason"]
