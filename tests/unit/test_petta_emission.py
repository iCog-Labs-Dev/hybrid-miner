"""Checks for the validated-document to PeTTa interchange encoder."""

import json

import pytest

from hybrid_miner.contracts.semantic_document import SemanticDocument
from hybrid_miner.emission.__main__ import main
from hybrid_miner.emission.emit_petta import emit_petta
from hybrid_miner.ontology import OntologyBundle


def _bundle() -> OntologyBundle:
    return OntologyBundle.model_validate(
        {
            "contract_version": "ontology-bundle-v1",
            "bundle_id": "test-bundle",
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
                    "confidence": 0.9,
                    "source_spans": [{"start": 5, "end": 9}],
                }
            ],
        }
    )


def test_emits_versioned_fact_and_preserves_evidence(tmp_path) -> None:
    document = _document()
    source = tmp_path / "documents.jsonl"
    source.write_text(document.canonical_json() + "\n", encoding="utf-8")
    atoms = tmp_path / "facts.metta"
    metadata = tmp_path / "facts.metadata.metta"
    provenance = tmp_path / "facts.provenance.jsonl"

    result = emit_petta(
        bundle=_bundle(),
        input_path=source,
        atoms_path=atoms,
        metadata_path=metadata,
        provenance_path=provenance,
    )

    assert result.assertions == 1
    assert result.facts == 1
    fact = atoms.read_text(encoding="utf-8").strip()
    assert fact == "(Event@upper-v1 ent:corpus-1:doc-1:leaving)"
    evidence = json.loads(provenance.read_text(encoding="utf-8"))
    assert evidence["atom"] == fact
    assert evidence["ontology_version"] == "upper-v1"
    assert evidence["parser"]["model"] == "teacher"
    assert evidence["assertion"]["source_spans"] == [{"start": 5, "end": 9}]
    metadata_text = metadata.read_text(encoding="utf-8")
    assert f"(AssertionEvidence {evidence['evidence_id']} {fact} " in metadata_text
    assert f"(SourceSpan {evidence['evidence_id']} 5 9)" in metadata_text
    assert "(AllowedType Event@upper-v1 0 Event)" in metadata_text

    second_atoms = tmp_path / "second.metta"
    second_metadata = tmp_path / "second.metadata.metta"
    second_provenance = tmp_path / "second.provenance.jsonl"
    emit_petta(
        bundle=_bundle(),
        input_path=source,
        atoms_path=second_atoms,
        metadata_path=second_metadata,
        provenance_path=second_provenance,
    )
    assert atoms.read_bytes() == second_atoms.read_bytes()
    assert metadata.read_bytes() == second_metadata.read_bytes()
    assert provenance.read_bytes() == second_provenance.read_bytes()


def test_rejects_unvalidated_input_without_leaving_partial_artifacts(tmp_path) -> None:
    document = _document()
    source = tmp_path / "documents.jsonl"
    source.write_text(document.canonical_json() + "\nnot-json\n", encoding="utf-8")
    atoms = tmp_path / "facts.metta"
    metadata = tmp_path / "facts.metadata.metta"
    provenance = tmp_path / "facts.provenance.jsonl"

    with pytest.raises(ValueError, match="line 2"):
        emit_petta(
            bundle=_bundle(),
            input_path=source,
            atoms_path=atoms,
            metadata_path=metadata,
            provenance_path=provenance,
        )
    assert not atoms.exists()
    assert not metadata.exists()
    assert not provenance.exists()


def test_cli_reports_emitted_assertions(tmp_path, capsys) -> None:
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(_bundle().canonical_json(), encoding="utf-8")
    source = tmp_path / "documents.jsonl"
    source.write_text(_document().canonical_json() + "\n", encoding="utf-8")
    atoms = tmp_path / "facts.metta"
    metadata = tmp_path / "facts.metadata.metta"
    provenance = tmp_path / "facts.provenance.jsonl"

    status = main(
        [
            "--bundle",
            str(bundle_path),
            "--input",
            str(source),
            "--atoms-output",
            str(atoms),
            "--metadata-output",
            str(metadata),
            "--provenance-output",
            str(provenance),
        ]
    )
    assert status == 0
    assert "Facts: 1" in capsys.readouterr().out


def test_duplicate_fact_has_one_graph_atom_and_two_evidence_records(tmp_path) -> None:
    document = _document()
    duplicate = document.model_copy(
        update={"assertions": (document.assertions[0], document.assertions[0])}
    )
    source = tmp_path / "documents.jsonl"
    source.write_text(duplicate.canonical_json() + "\n", encoding="utf-8")
    atoms = tmp_path / "facts.metta"
    metadata = tmp_path / "facts.metadata.metta"
    provenance = tmp_path / "facts.provenance.jsonl"

    result = emit_petta(
        bundle=_bundle(),
        input_path=source,
        atoms_path=atoms,
        metadata_path=metadata,
        provenance_path=provenance,
    )

    assert (result.facts, result.assertions) == (1, 2)
    assert len(atoms.read_text(encoding="utf-8").splitlines()) == 1
    assert len(provenance.read_text(encoding="utf-8").splitlines()) == 2
