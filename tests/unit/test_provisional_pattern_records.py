"""Preserve provisional identities through source matching and record creation."""

import copy
import hashlib
import json

import pytest

from hybrid_miner.contracts.pattern_record import (
    PatternRecord,
    validate_extraction_pattern_record,
)
from hybrid_miner.contracts.semantic_document import SemanticDocument
from hybrid_miner.emission.emit_petta import emit_petta
from hybrid_miner.emission.symbols import entity_symbol, predicate_symbol
from hybrid_miner.mining import exact_matches
from hybrid_miner.mining.build_pattern_records import build_pattern_records
from hybrid_miner.mining.exact_matches import _pattern_clauses, recover_exact_matches
from hybrid_miner.ontology import OntologyBundle


@pytest.fixture
def provisional_artifacts(tmp_path):
    bundle = OntologyBundle.model_validate(
        {
            "contract_version": "ontology-bundle-v1",
            "bundle_id": "provisional-test",
            "bundle_version": "1",
            "ontology_versions": ["upper-v1"],
            "types": ["Event", "Entity"],
            "predicates": [
                {
                    "name": "Event",
                    "ontology_version": "upper-v1",
                    "argument_types": [["Event"]],
                },
            ],
        }
    )
    documents = []
    for number, relation in enumerate(("sing", "open"), 1):
        source_text = "Nora sang." if relation == "sing" else "Nora opened."
        event_end = len(source_text) - 1
        documents.append(
            SemanticDocument.model_validate(
                {
                    "document_id": f"doc-{number}",
                    "corpus_id": "corpus",
                    "source_text": source_text,
                    "parser": {
                        "model": "teacher",
                        "parser_version": "parser-v1",
                        "prompt_version": "prompt-v1",
                        "ontology_versions": ["upper-v1"],
                    },
                    "entities": [
                        {
                            "id": "event",
                            "type": "Event",
                            "span": {"start": 5, "end": event_end},
                        },
                        {
                            "id": "nora",
                            "type": "Entity",
                            "span": {"start": 0, "end": 4},
                        },
                    ],
                    "assertions": [
                        {
                            "predicate": "Event",
                            "arguments": ["event"],
                            "argument_types": ["Event"],
                            "status": "extracted-hypothesis",
                            "factuality": "asserted",
                            "confidence": 0.95,
                            "source_spans": [{"start": 5, "end": event_end}],
                        },
                        {
                            "predicate": "ProvisionalRelation",
                            "provisional_relation_id": relation,
                            "arguments": ["event", "nora"],
                            "argument_types": ["Event", "Entity"],
                            "status": "extracted-hypothesis",
                            "factuality": "asserted",
                            "confidence": 0.9,
                            "source_spans": [{"start": 0, "end": event_end}],
                        },
                    ],
                }
            )
        )
    source = tmp_path / "documents.jsonl"
    source.write_text(
        "".join(d.canonical_json() + "\n" for d in documents), encoding="utf-8"
    )
    emit_petta(
        bundle=bundle,
        input_path=source,
        atoms_path=tmp_path / "facts.metta",
        metadata_path=tmp_path / "metadata.metta",
        provenance_path=tmp_path / "provenance.jsonl",
    )
    return bundle


@pytest.mark.parametrize("fixed", [False, True])
def test_provisional_pattern_survives_exact_and_record_validation(
    tmp_path, monkeypatch, provisional_artifacts, fixed
):
    bundle = provisional_artifacts
    head = predicate_symbol("ProvisionalRelation", "provisional-test@1")
    relation = "rel:sing" if fixed else "$v1"
    entity_variable = "$v1" if fixed else "$v2"
    pattern = f"(, (Event@upper-v1 $v0) ({head} {relation} $v0 {entity_variable}))"
    bindings = []
    for number, relation_id in enumerate(("sing", "open"), 1):
        if fixed and number == 2:
            continue
        operands = [entity_symbol("corpus", f"doc-{number}", "event")]
        if not fixed:
            operands.append(f"rel:{relation_id}")
        operands.append(entity_symbol("corpus", f"doc-{number}", "nora"))
        bindings.append(tuple(operands))
    digest = hashlib.sha256((tmp_path / "facts.metta").read_bytes()).hexdigest()
    support = tmp_path / "support.jsonl"
    support.write_text(
        json.dumps(
            {
                "pattern": pattern,
                "support": len(bindings),
                "graph_sha256": digest,
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
    exact = tmp_path / "exact.jsonl"
    assert recover_exact_matches(
        bundle=bundle,
        documents_path=tmp_path / "documents.jsonl",
        atoms_path=tmp_path / "facts.metta",
        provenance_path=tmp_path / "provenance.jsonl",
        support_path=support,
        petta_root=tmp_path,
        miner_root=tmp_path,
        output_path=exact,
        rejected_path=tmp_path / "exact-rejected.jsonl",
        min_support=1 if fixed else 2,
    ) == (1, 0)
    output = tmp_path / "records.jsonl"
    build_args = dict(
        bundle=bundle,
        documents_path=tmp_path / "documents.jsonl",
        atoms_path=tmp_path / "facts.metta",
        provenance_path=tmp_path / "provenance.jsonl",
        exact_path=exact,
        output_path=output,
        rejected_path=tmp_path / "record-rejected.jsonl",
    )
    assert build_pattern_records(**build_args) == (1, 0)
    record = validate_extraction_pattern_record(
        PatternRecord.model_validate_json(output.read_text(encoding="utf-8"))
    )
    assert record.canonical_term == pattern.replace("Event@upper-v1", "Event").replace(
        head, "ProvisionalRelation"
    )
    assert record.type_constraints["$v0"] == "Event"
    assert record.type_constraints[entity_variable] == "Entity"
    if not fixed:
        assert record.type_constraints["$v1"] == "ProvisionalRelationId"
    assert record.ontology_groundings == (
        "provisional-test@1:ProvisionalRelation",
        "upper-v1:Event",
    )
    observed = record.expensive_trace["provisional_relations"][0]["observed_relations"]
    assert {item["relation_id"] for item in observed} == (
        {"sing"} if fixed else {"sing", "open"}
    )
    assert record.expensive_endpoint == {"observed_support": len(bindings)}
    assert record.status == "candidate"
    assert record.semantic_level == "extracted"
    assert PatternRecord.from_canonical_json(record.to_canonical_json()) == record
    # A changed relation identity cannot pass the record builder's source recheck.
    row = json.loads(exact.read_text(encoding="utf-8"))
    forged = copy.deepcopy(row)
    forged["exact_bindings"][0]["evidence"][1]["provisional_relation_id"] = "forged"
    tampered = tmp_path / "forged.jsonl"
    tampered.write_text(json.dumps(forged) + "\n", encoding="utf-8")
    build_args.update(
        exact_path=tampered,
        output_path=tmp_path / "forged-records.jsonl",
        rejected_path=tmp_path / "forged-rejected.jsonl",
    )
    assert build_pattern_records(**build_args) == (0, 1)
    assert "disagrees with source evidence" in build_args["rejected_path"].read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    "clause, message",
    [
        ("(ProvisionalRelation@foreign rel:sing $v0)", "unsupported"),
        ("($predicate $v0)", "unsupported"),
        ("(Event@upper-v1 $v0 $v1)", "wrong arity"),
        (
            "(ProvisionalRelation@provisional-test%401 rel:sing)",
            "relation id and arguments",
        ),
        (
            "(ProvisionalRelation@provisional-test%401 entity:sing $v0)",
            "invalid relation",
        ),
        ("(ProvisionalRelation@provisional-test%401 rel: $v0)", "invalid relation"),
    ],
)
def test_rejects_unrepresentable_predicate_shapes(
    provisional_artifacts, clause, message
):
    with pytest.raises(ValueError, match=message):
        _pattern_clauses(clause, provisional_artifacts)
