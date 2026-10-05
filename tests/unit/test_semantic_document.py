"""Behavioral checks for the versioned S1 interchange contract."""

import json

import pytest
from hybrid_miner.contracts import SemanticDocument
from pydantic import ValidationError


def _payload() -> dict:
    text = "Although Mira promised Sol, she delayed."
    first_clause_end = text.index(",")
    second_clause_start = first_clause_end + 2
    return {
        "document_id": "doc-17",
        "corpus_id": "obligation-audit-v1",
        "source_text": text,
        "parser": {
            "model": "reference-model",
            "parser_version": "semantic-parser-v1",
            "prompt_version": "semantic-v1",
            "ontology_versions": ["upper-v1", "discourse-v1"],
        },
        "entities": [
            {"id": "clause-a", "type": "Clause", "span": [0, first_clause_end]},
            {
                "id": "clause-b",
                "type": "Clause",
                "span": [second_clause_start, len(text) - 1],
            },
        ],
        "assertions": [
            {
                "predicate": "Concession",
                "arguments": ["clause-a", "clause-b"],
                "argument_types": ["Clause", "Clause"],
                "status": "extracted-hypothesis",
                "confidence": 0.9,
                "source_spans": [[0, len(text)]],
                "alternatives": [],
            }
        ],
    }


def _normalized_payload() -> dict:
    payload = _payload()
    payload["entities"] = [
        {**entity, "span": {"start": entity["span"][0], "end": entity["span"][1]}}
        for entity in payload["entities"]
    ]
    payload["assertions"][0]["source_spans"] = [
        {"start": 0, "end": len(payload["source_text"])}
    ]
    return payload


def test_valid_document_has_stable_round_trip_and_signature_check():
    document = SemanticDocument.model_validate(_normalized_payload())
    document.validate_against_signatures(
        ontology_versions=("upper-v1", "discourse-v1"),
        signatures={"Concession": (frozenset({"Clause"}), frozenset({"Clause"}))},
    )

    serialized = document.canonical_json()
    assert (
        serialized == SemanticDocument.model_validate_json(serialized).canonical_json()
    )
    assert json.loads(serialized)["contract_version"] == "semantic-document-v1"


def test_unsupported_predicate_requires_explicit_provisional_relation():
    payload = _normalized_payload()
    payload["assertions"][0]["predicate"] = "NovelRelation"
    document = SemanticDocument.model_validate(payload)

    with pytest.raises(ValueError, match="unknown predicate"):
        document.validate_against_signatures(
            ontology_versions=("upper-v1", "discourse-v1"),
            signatures={"Concession": (frozenset({"Clause"}), frozenset({"Clause"}))},
        )

    payload["assertions"][0]["predicate"] = "ProvisionalRelation"
    with pytest.raises(ValidationError, match="provisional_relation_id"):
        SemanticDocument.model_validate(payload)

    payload["assertions"][0]["provisional_relation_id"] = "novel-1"
    SemanticDocument.model_validate(payload).validate_against_signatures(
        ontology_versions=("upper-v1", "discourse-v1"), signatures={}
    )


def test_signature_mismatch_is_rejected():
    document = SemanticDocument.model_validate(_normalized_payload())

    with pytest.raises(ValueError, match="expects argument types"):
        document.validate_against_signatures(
            ontology_versions=("upper-v1", "discourse-v1"),
            signatures={"Concession": (frozenset({"Clause"}), frozenset({"Entity"}))},
        )


def test_signature_check_requires_same_ontology_versions():
    document = SemanticDocument.model_validate(_normalized_payload())

    with pytest.raises(ValueError, match="ontology signature versions"):
        document.validate_against_signatures(
            ontology_versions=("upper-v1", "discourse-v2"), signatures={}
        )


def test_provisional_relation_cannot_shadow_known_predicate():
    payload = _normalized_payload()
    payload["assertions"][0]["predicate"] = "ProvisionalRelation"
    payload["assertions"][0]["provisional_relation_id"] = "Concession"
    document = SemanticDocument.model_validate(payload)

    with pytest.raises(ValueError, match="duplicates a catalog predicate"):
        document.validate_against_signatures(
            ontology_versions=("upper-v1", "discourse-v1"),
            signatures={"Concession": (frozenset({"Clause"}), frozenset({"Clause"}))},
        )


def test_missing_entity_reference_is_rejected():
    payload = _normalized_payload()
    payload["assertions"][0]["arguments"][1] = "missing-clause"

    with pytest.raises(ValidationError, match="unknown assertion argument"):
        SemanticDocument.model_validate(payload)


def test_entity_type_disagreement_is_rejected():
    payload = _normalized_payload()
    payload["assertions"][0]["argument_types"][1] = "Entity"

    with pytest.raises(ValidationError, match="does not match entity type"):
        SemanticDocument.model_validate(payload)


@pytest.mark.parametrize("target", ["entity", "assertion"])
def test_out_of_bounds_span_is_rejected(target):
    payload = _normalized_payload()
    beyond_end = len(payload["source_text"]) + 1
    if target == "entity":
        payload["entities"][0]["span"]["end"] = beyond_end
    else:
        payload["assertions"][0]["source_spans"][0]["end"] = beyond_end

    with pytest.raises(ValidationError, match="exceeds source text"):
        SemanticDocument.model_validate(payload)


def test_confidence_and_status_are_constrained():
    payload = _normalized_payload()
    payload["assertions"][0]["confidence"] = float("nan")
    with pytest.raises(ValidationError):
        SemanticDocument.model_validate(payload)

    payload["assertions"][0]["confidence"] = 0.9
    payload["assertions"][0]["status"] = "derived"
    with pytest.raises(ValidationError):
        SemanticDocument.model_validate(payload)

    payload["assertions"][0]["status"] = "extracted-hypothesis"
    payload["assertions"][0]["confidence"] = "0.9"
    with pytest.raises(ValidationError):
        SemanticDocument.model_validate(payload)


def test_factuality_is_optional_without_defaulting_to_asserted():
    document = SemanticDocument.model_validate(_normalized_payload())
    assert document.assertions[0].factuality is None
    assert document.assertions[0].conditional_on is None
    serialized_assertion = json.loads(document.canonical_json())["assertions"][0]
    assert "factuality" not in serialized_assertion
    assert "conditional_on" not in serialized_assertion

    payload = _normalized_payload()
    payload["assertions"][0]["factuality"] = "hypothetical"
    classified = SemanticDocument.model_validate(payload)
    assert classified.assertions[0].factuality == "hypothetical"
    assert (
        json.loads(classified.canonical_json())["assertions"][0]["factuality"]
        == "hypothetical"
    )

    payload["assertions"][0]["factuality"] = "proven"
    with pytest.raises(ValidationError, match="factuality"):
        SemanticDocument.model_validate(payload)


def test_conditional_scope_requires_hypothetical_assertion_and_clause():
    payload = _normalized_payload()
    assertion = payload["assertions"][0]
    assertion["factuality"] = "hypothetical"
    assertion["conditional_on"] = "clause-a"
    document = SemanticDocument.model_validate(payload)
    assert document.assertions[0].conditional_on == "clause-a"
    assert (
        json.loads(document.canonical_json())["assertions"][0]["conditional_on"]
        == "clause-a"
    )

    assertion["factuality"] = "asserted"
    with pytest.raises(ValidationError, match="requires hypothetical"):
        SemanticDocument.model_validate(payload)

    assertion["factuality"] = "hypothetical"
    assertion["conditional_on"] = "missing"
    with pytest.raises(ValidationError, match="source-anchored Clause"):
        SemanticDocument.model_validate(payload)

    assertion["conditional_on"] = "clause-a"
    payload["entities"][0]["type"] = "Entity"
    with pytest.raises(ValidationError, match="source-anchored Clause"):
        SemanticDocument.model_validate(payload)

    payload["entities"][0]["type"] = "Clause"
    payload["entities"][0]["span"] = None
    with pytest.raises(ValidationError, match="source-anchored Clause"):
        SemanticDocument.model_validate(payload)


def test_no_assertions_does_not_force_a_false_extraction():
    payload = _normalized_payload()
    payload["assertions"] = []

    document = SemanticDocument.model_validate(payload)

    assert document.assertions == ()


def test_duplicate_entity_ids_are_rejected():
    payload = _normalized_payload()
    payload["entities"][1]["id"] = payload["entities"][0]["id"]

    with pytest.raises(ValidationError, match="entity ids must be unique"):
        SemanticDocument.model_validate(payload)
