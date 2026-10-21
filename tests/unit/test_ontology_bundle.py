"""Checks for external, versioned ontology-bundle inputs."""

from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from hybrid_miner.contracts.semantic_document import SemanticDocument
from hybrid_miner.ontology import OntologyBundle


def _bundle_data() -> dict:
    return {
        "contract_version": "ontology-bundle-v1",
        "bundle_id": "example-discourse",
        "bundle_version": "1.0",
        "ontology_versions": ["example-upper-v1", "example-discourse-v1"],
        "types": ["Clause", "Event"],
        "predicates": [
            {
                "name": "Concession",
                "ontology_version": "example-discourse-v1",
                "argument_types": [["Clause"], ["Clause", "Event"]],
            }
        ],
    }


def _document_data() -> dict:
    return {
        "document_id": "example-doc",
        "corpus_id": "example-corpus",
        "source_text": "Although it rained, we went.",
        "parser": {
            "model": "example-model",
            "parser_version": "parser-v1",
            "prompt_version": "prompt-v1",
            "ontology_versions": ["example-upper-v1", "example-discourse-v1"],
        },
        "entities": [
            {"id": "clause-1", "type": "Clause"},
            {"id": "clause-2", "type": "Clause"},
        ],
        "assertions": [
            {
                "predicate": "Concession",
                "arguments": ["clause-1", "clause-2"],
                "argument_types": ["Clause", "Clause"],
                "status": "extracted-hypothesis",
                "confidence": 0.9,
                "source_spans": [{"start": 0, "end": 18}],
            }
        ],
    }


def test_loads_json_and_validates_document() -> None:
    fixture = Path(__file__).parent.parent / "fixtures" / "example-ontology-bundle.json"
    bundle = OntologyBundle.load_json(fixture)
    document = SemanticDocument.model_validate(_document_data())

    assert bundle == OntologyBundle.model_validate(_bundle_data())
    assert bundle.validate_document(document) is document
    assert OntologyBundle.model_validate_json(bundle.canonical_json()) == bundle
    assert bundle.bundle_version == "1.0"


@pytest.mark.parametrize("contract_version", [None, "ontology-bundle-v2"])
def test_requires_supported_explicit_contract_version(contract_version) -> None:
    data = _bundle_data()
    if contract_version is None:
        del data["contract_version"]
    else:
        data["contract_version"] = contract_version

    with pytest.raises(ValidationError):
        OntologyBundle.model_validate(data)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda data: data["types"].append("Clause"), "identifiers must be unique"),
        (
            lambda data: data["predicates"].append(deepcopy(data["predicates"][0])),
            "duplicate predicate",
        ),
        (
            lambda data: data["predicates"][0].update(ontology_version="missing-v1"),
            "undeclared ontology version",
        ),
        (
            lambda data: data["predicates"][0].update(argument_types=[["Unknown"]]),
            "undeclared types",
        ),
        (
            lambda data: data["predicates"][0].update(argument_types=[[]]),
            "allowed type",
        ),
        (
            lambda data: data["predicates"][0].update(name="ProvisionalRelation"),
            "reserved",
        ),
    ],
)
def test_rejects_inconsistent_catalog(change, message) -> None:
    data = _bundle_data()
    change(data)

    with pytest.raises(ValidationError, match=message):
        OntologyBundle.model_validate(data)


def test_rejects_version_mismatch_and_unknown_predicate() -> None:
    bundle = OntologyBundle.model_validate(_bundle_data())
    data = _document_data()
    data["parser"]["ontology_versions"][1] = "example-discourse-v2"

    with pytest.raises(ValueError, match="ontology signature versions"):
        bundle.validate_document(SemanticDocument.model_validate(data))

    data = _document_data()
    data["assertions"][0]["predicate"] = "Unlisted"
    with pytest.raises(ValueError, match="unknown predicate"):
        bundle.validate_document(SemanticDocument.model_validate(data))


def test_rejects_argument_type_outside_signature() -> None:
    bundle = OntologyBundle.model_validate(_bundle_data())
    data = _document_data()
    data["entities"][0]["type"] = "Event"
    data["assertions"][0]["argument_types"][0] = "Event"

    with pytest.raises(ValueError, match="expects argument types"):
        bundle.validate_document(SemanticDocument.model_validate(data))


def test_empty_assertions_remain_valid() -> None:
    bundle = OntologyBundle.model_validate(_bundle_data())
    data = _document_data()
    data["assertions"] = []

    assert (
        bundle.validate_document(SemanticDocument.model_validate(data)).assertions == ()
    )


def test_rejects_undeclared_entity_type_without_assertions() -> None:
    bundle = OntologyBundle.model_validate(_bundle_data())
    data = _document_data()
    data["assertions"] = []
    data["entities"][0]["type"] = "Unknown"

    with pytest.raises(ValueError, match="undeclared type"):
        bundle.validate_document(SemanticDocument.model_validate(data))
