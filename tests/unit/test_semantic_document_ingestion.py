"""Behavioral checks for the parser-to-miner JSONL ingestion boundary."""

import json

import pytest

from hybrid_miner.contracts.semantic_document import SemanticDocument
from hybrid_miner.ingestion import ingest_jsonl
from hybrid_miner.ingestion.__main__ import main
from hybrid_miner.ontology import OntologyBundle


def _bundle() -> OntologyBundle:
    return OntologyBundle.model_validate(
        {
            "contract_version": "ontology-bundle-v1",
            "bundle_id": "ingestion-test",
            "bundle_version": "1",
            "ontology_versions": ["upper-v1"],
            "types": ["Event"],
            "predicates": [
                {
                    "name": "Event",
                    "ontology_version": "upper-v1",
                    "argument_types": [["Event"]],
                }
            ],
        }
    )


def _document() -> SemanticDocument:
    return SemanticDocument.model_validate(
        {
            "document_id": "doc-1",
            "corpus_id": "corpus-1",
            "source_text": "Mira left.",
            "parser": {
                "model": "teacher",
                "parser_version": "parser-v1",
                "prompt_version": "prompt-v1",
                "ontology_versions": ["upper-v1"],
            },
            "entities": [
                {"id": "leaving", "type": "Event", "span": {"start": 5, "end": 9}}
            ],
            "assertions": [
                {
                    "predicate": "Event",
                    "arguments": ["leaving"],
                    "argument_types": ["Event"],
                    "status": "extracted-hypothesis",
                    "factuality": "asserted",
                    "confidence": 0.9,
                    "source_spans": [{"start": 0, "end": 10}],
                }
            ],
        }
    )


def test_streams_accepted_documents_and_quarantines_invalid_lines(tmp_path) -> None:
    document = _document()
    wrong_bundle = json.loads(document.canonical_json())
    wrong_bundle["parser"]["ontology_versions"] = ["other-v1"]
    source = tmp_path / "source.jsonl"
    source.write_text(
        document.canonical_json()
        + "\n"
        + "not-json\n"
        + json.dumps(wrong_bundle)
        + "\n"
        + document.canonical_json()
        + "\n",
        encoding="utf-8",
    )

    accepted = tmp_path / "accepted.jsonl"
    rejected = tmp_path / "rejected.jsonl"
    result = ingest_jsonl(
        bundle=_bundle(),
        input_path=source,
        accepted_path=accepted,
        rejected_path=rejected,
    )

    assert (result.accepted, result.rejected) == (1, 3)
    assert accepted.read_text(encoding="utf-8").splitlines() == [
        document.canonical_json()
    ]
    failures = [json.loads(line) for line in rejected.read_text().splitlines()]
    assert [failure["line_number"] for failure in failures] == [2, 3, 4]
    assert failures[0]["raw_line"] == "not-json"
    assert "ontology signature versions" in failures[1]["error"]
    assert "Duplicate semantic document" in failures[2]["error"]


def test_refuses_path_collisions_and_existing_outputs(tmp_path) -> None:
    source = tmp_path / "source.jsonl"
    source.write_text(_document().canonical_json() + "\n", encoding="utf-8")
    accepted = tmp_path / "accepted.jsonl"
    rejected = tmp_path / "rejected.jsonl"

    with pytest.raises(ValueError, match="paths must differ"):
        ingest_jsonl(
            bundle=_bundle(),
            input_path=source,
            accepted_path=source,
            rejected_path=rejected,
        )
    accepted.write_text("existing\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="already exists"):
        ingest_jsonl(
            bundle=_bundle(),
            input_path=source,
            accepted_path=accepted,
            rejected_path=rejected,
        )
    assert accepted.read_text(encoding="utf-8") == "existing\n"


def test_cli_returns_nonzero_on_quarantined_record(tmp_path, capsys) -> None:
    source = tmp_path / "source.jsonl"
    source.write_text("not-json\n", encoding="utf-8")
    bundle = tmp_path / "bundle.json"
    bundle.write_text(_bundle().canonical_json(), encoding="utf-8")

    status = main(
        [
            "--bundle",
            str(bundle),
            "--input",
            str(source),
            "--accepted-output",
            str(tmp_path / "accepted.jsonl"),
            "--rejected-output",
            str(tmp_path / "rejected.jsonl"),
        ]
    )

    assert status == 2
    assert "Rejected: 1" in capsys.readouterr().out
