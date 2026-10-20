"""Source rules remain typed annotations, not unconditional mined facts."""

from copy import deepcopy

import pytest

from hybrid_miner.contracts import SemanticDocument
from hybrid_miner.emission.emit_petta import emit_petta
from hybrid_miner.emission.semantic_rules import (
    document_rule_graph,
    rule_predicate_view,
)
from hybrid_miner.ingestion.petta_gate import _entry
from hybrid_miner.mining.exact_matches import _documents, _evidence_index, _facts
from hybrid_miner.ontology import OntologyBundle


def rule_document() -> dict:
    source = "All red things are nice."

    def clause(relation):
        return {
            "predicate": "ProvisionalRelation",
            "provisional_relation_id": relation,
            "arguments": ["$x"],
            "argument_types": ["Entity"],
            "status": "extracted-hypothesis",
            "confidence": 0.9,
            "source_spans": [{"start": 0, "end": len(source)}],
        }

    return {
        "document_id": "rule-1",
        "corpus_id": "rules",
        "source_text": source,
        "parser": {
            "model": "teacher",
            "parser_version": "parser-v1",
            "prompt_version": "prompt-v1",
            "ontology_versions": ["upper-v1"],
        },
        "entities": [],
        "assertions": [],
        "rules": [
            {
                "id": "rule-1",
                "status": "extracted-hypothesis",
                "variables": [{"id": "$x", "type": "Entity"}],
                "antecedents": [clause("red")],
                "consequents": [clause("nice")],
                "source_spans": [{"start": 0, "end": len(source)}],
            }
        ],
    }


def test_rule_round_trip_does_not_assert_its_consequent():
    document = SemanticDocument.model_validate(rule_document())
    assert not document.assertions
    assert document.rules[0].consequents[0].factuality == "hypothetical"
    assert document.rules[0].quantifier == "universal"
    encoded = document.canonical_json()
    assert SemanticDocument.model_validate_json(encoded).canonical_json() == encoded
    document.validate_against_signatures(ontology_versions=("upper-v1",), signatures={})


@pytest.mark.parametrize(
    "change, message",
    [
        ("undeclared", "declared and used"),
        ("unbound", "must occur in the antecedents"),
        ("wrong_type", "type mismatch"),
        ("bad_span", "rule source span exceeds"),
        ("asserted", "hypothetical"),
        ("duplicate", "rule ids must be unique"),
    ],
)
def test_invalid_rule_is_rejected(change, message):
    payload = rule_document()
    rule = payload["rules"][0]
    if change == "undeclared":
        rule["variables"] = []
    elif change == "unbound":
        rule["variables"].append({"id": "$y", "type": "Entity"})
        rule["consequents"][0]["arguments"] = ["$y"]
    elif change == "wrong_type":
        rule["consequents"][0]["argument_types"] = ["Clause"]
    elif change == "bad_span":
        rule["source_spans"][0]["end"] = 999
    elif change == "asserted":
        rule["consequents"][0]["factuality"] = "asserted"
    elif change == "duplicate":
        payload["rules"].append(deepcopy(rule))
    with pytest.raises(ValueError, match=message):
        SemanticDocument.model_validate(payload)


def test_rule_variable_cannot_escape_to_standalone_assertions():
    payload = rule_document()
    payload["assertions"] = [payload["rules"][0]["antecedents"][0]]
    with pytest.raises(ValueError, match="unknown assertion argument"):
        SemanticDocument.model_validate(payload)


def test_rule_literals_are_checked_against_supplied_signatures():
    payload = rule_document()
    literal = payload["rules"][0]["antecedents"][0]
    literal["predicate"] = "Entity"
    del literal["provisional_relation_id"]
    document = SemanticDocument.model_validate(payload)
    with pytest.raises(ValueError, match="expects argument types"):
        document.validate_against_signatures(
            ontology_versions=("upper-v1",),
            signatures={"Entity": (frozenset({"Clause"}),)},
        )


def test_bundle_rule_types_are_checked_when_a_document_is_supplied():
    bundle = OntologyBundle.model_validate(
        {
            "contract_version": "ontology-bundle-v1",
            "bundle_id": "rules",
            "bundle_version": "v1",
            "ontology_versions": ["upper-v1"],
            "types": ["Entity"],
            "predicates": [],
        }
    )
    bundle.validate_document(SemanticDocument.model_validate(rule_document()))
    payload = rule_document()
    rule = payload["rules"][0]
    rule["variables"][0]["type"] = "UndeclaredType"
    for literal in [*rule["antecedents"], *rule["consequents"]]:
        literal["argument_types"] = ["UndeclaredType"]
    document = SemanticDocument.model_validate(payload)
    with pytest.raises(ValueError, match="rule variable.*undeclared type"):
        bundle.validate_document(document)


def test_emitter_preserves_rules_without_asserting_consequents(tmp_path):
    bundle = OntologyBundle.model_validate(
        {
            "contract_version": "ontology-bundle-v1",
            "bundle_id": "rules",
            "bundle_version": "v1",
            "ontology_versions": ["upper-v1"],
            "types": ["Entity"],
            "predicates": [
                {
                    "name": "Entity",
                    "ontology_version": "upper-v1",
                    "argument_types": [["Entity"]],
                }
            ],
        }
    )
    document = SemanticDocument.model_validate(rule_document())
    bundle.validate_document(document)
    source = tmp_path / "documents.jsonl"
    source.write_text(document.canonical_json() + "\n", encoding="utf-8")
    outputs = [
        tmp_path / name for name in ("facts.metta", "meta.metta", "provenance.jsonl")
    ]
    result = emit_petta(
        bundle=bundle,
        input_path=source,
        atoms_path=outputs[0],
        metadata_path=outputs[1],
        provenance_path=outputs[2],
    )
    assert (result.assertions, result.rules, result.facts) == (0, 1, 3)
    graph = outputs[0].read_text(encoding="utf-8")
    assert "(ExtractedRule@semantic-rule-v1 " in graph
    assert "(RuleAntecedentPositive@semantic-rule-v1 " in graph
    assert "(RuleConsequentPositive@semantic-rule-v1 " in graph
    assert "(ProvisionalRelation@" not in graph
    assert "$" not in graph
    assert "(=" not in graph and "!(" not in graph
    facts = _facts(outputs[0])
    documents = _documents(source, bundle)
    evidence = _evidence_index(outputs[2], facts, documents, bundle)
    assert set(evidence) == facts
    for line in outputs[2].read_text(encoding="utf-8").splitlines():
        entry = _entry(line_number=1, raw_line=line, bundle=bundle)
        assert all(f"(fact {fact})" in entry.requirements for fact in facts)
    meta = outputs[1].read_text(encoding="utf-8").splitlines()
    assert len([line for line in meta if line.startswith("(RuleVariable ")]) == 1


def _rule_bundle():
    return OntologyBundle.model_validate(
        {
            "contract_version": "ontology-bundle-v1",
            "bundle_id": "rules",
            "bundle_version": "v1",
            "ontology_versions": ["upper-v1"],
            "types": ["Entity"],
            "predicates": [],
        }
    )


def test_rule_variables_are_scoped_and_alpha_normalized():
    payload = rule_document()
    second = deepcopy(payload["rules"][0])
    second["id"] = "second-rule"
    payload["rules"].append(second)
    document = SemanticDocument.model_validate(payload)
    first_graph = document_rule_graph(document, _rule_bundle(), 0)
    second_graph = document_rule_graph(document, _rule_bundle(), 1)
    assert first_graph[1].atom != second_graph[1].atom
    assert "rvar:rules:rule-1:rule-1:v0" in first_graph[1].atom
    assert "rvar:rules:rule-1:second-rule:v0" in second_graph[1].atom
    renamed = deepcopy(payload)
    renamed["rules"][0]["variables"][0]["id"] = "$renamed"
    for clause in (
        *renamed["rules"][0]["antecedents"],
        *renamed["rules"][0]["consequents"],
    ):
        clause["arguments"] = ["$renamed"]
    renamed_doc = SemanticDocument.model_validate(renamed)
    assert [p.atom for p in first_graph] == [
        p.atom for p in document_rule_graph(renamed_doc, _rule_bundle(), 0)
    ]


def test_rule_conjunction_and_negative_conclusion_are_not_flattened():
    payload = rule_document()
    rule = payload["rules"][0]
    extra = deepcopy(rule["antecedents"][0])
    extra["provisional_relation_id"] = "round"
    rule["antecedents"].append(extra)
    rule["consequents"][0]["polarity"] = "negative"
    document = SemanticDocument.model_validate(payload)
    parts = document_rule_graph(document, _rule_bundle(), 0)
    assert len(parts) == 4
    assert sum(p.atom.startswith("(RuleAntecedentPositive") for p in parts) == 2
    assert parts[-1].atom.startswith("(RuleConsequentNegative")
    assert all(not p.atom.startswith("(ProvisionalRelation") for p in parts)


def test_rule_graph_preserves_named_entity_constants():
    payload = rule_document()
    source = "All red things are near Nora."
    payload["source_text"] = source
    payload["entities"] = [
        {
            "id": "nora",
            "type": "Entity",
            "span": {"start": source.index("Nora"), "end": source.index("Nora") + 4},
        }
    ]
    rule = payload["rules"][0]
    rule["source_spans"] = [{"start": 0, "end": len(source)}]
    for clause in (*rule["antecedents"], *rule["consequents"]):
        clause["source_spans"] = [{"start": 0, "end": len(source)}]
    rule["consequents"][0].update(
        provisional_relation_id="near",
        arguments=["$x", "nora"],
        argument_types=["Entity", "Entity"],
    )
    document = SemanticDocument.model_validate(payload)
    part = document_rule_graph(document, _rule_bundle(), 0)[-1]
    assert "rvar:rules:rule-1:rule-1:v0 ent:rules:rule-1:nora" in part.atom


def test_known_predicate_and_negative_premise_keep_their_identity():
    payload = rule_document()
    literal = payload["rules"][0]["antecedents"][0]
    literal["predicate"] = "Entity"
    literal["polarity"] = "negative"
    del literal["provisional_relation_id"]
    bundle_payload = _rule_bundle().model_dump(mode="json")
    bundle_payload["predicates"] = [
        {
            "name": "Entity",
            "ontology_version": "upper-v1",
            "argument_types": [["Entity"]],
        }
    ]
    bundle = OntologyBundle.model_validate(bundle_payload)
    document = SemanticDocument.model_validate(payload)
    bundle.validate_document(document)
    premise = document_rule_graph(document, bundle, 0)[1]
    assert premise.atom.startswith("(RuleAntecedentNegative@semantic-rule-v1 ")
    assert "pred:Entity@upper-v1" in premise.atom
    assert rule_predicate_view("pred:Entity@upper-v1", bundle) == "pred:Entity"
    assert (
        "(RuleCatalogPredicate pred:Entity@upper-v1 Entity upper-v1)"
        in premise.metadata
    )
