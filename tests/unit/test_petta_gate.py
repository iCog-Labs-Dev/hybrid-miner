"""Contract checks for PeTTa ingress and assertion-level quarantine."""

import json

from hybrid_miner.contracts.semantic_document import SemanticDocument
from hybrid_miner.emission.emit_petta import emit_petta
from hybrid_miner.ingestion import petta_gate
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


def _document(provisional: bool = False) -> SemanticDocument:
    assertion = {
        "predicate": "ProvisionalRelation" if provisional else "Event",
        "arguments": ["leaving"],
        "argument_types": ["Event"],
        "status": "extracted-hypothesis",
        "confidence": 0.9,
        "source_spans": [{"start": 5, "end": 9}],
    }
    if provisional:
        assertion["provisional_relation_id"] = "UncatalogedEvent"
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
            "assertions": [assertion],
        }
    )


def test_emission_declares_provisional_head_for_petta(tmp_path) -> None:
    source = tmp_path / "documents.jsonl"
    source.write_text(
        _document(provisional=True).canonical_json() + "\n", encoding="utf-8"
    )
    metadata = tmp_path / "metadata.metta"
    emit_petta(
        bundle=_bundle(),
        input_path=source,
        atoms_path=tmp_path / "facts.metta",
        metadata_path=metadata,
        provenance_path=tmp_path / "provenance.jsonl",
    )
    assert (
        "(ProvisionalPredicate ProvisionalRelation@test-bundle%401 "
        "rel:UncatalogedEvent)"
    ) in metadata.read_text(encoding="utf-8")


def test_gate_quarantines_failed_petta_check(tmp_path, monkeypatch) -> None:
    bundle = _bundle()
    source = tmp_path / "documents.jsonl"
    source.write_text(_document().canonical_json() + "\n", encoding="utf-8")
    atoms = tmp_path / "facts.metta"
    metadata = tmp_path / "metadata.metta"
    provenance = tmp_path / "provenance.jsonl"
    emit_petta(
        bundle=bundle,
        input_path=source,
        atoms_path=atoms,
        metadata_path=metadata,
        provenance_path=provenance,
    )

    def without_allowed_type(entries, **_kwargs):
        return {
            (index, name)
            for index, entry in enumerate(entries)
            for name, _ in entry.checks
            if name != "a0"
        }

    monkeypatch.setattr(petta_gate, "_petta_checks", without_allowed_type)
    accepted = tmp_path / "gated.metta"
    rejected = tmp_path / "rejected.jsonl"
    result = petta_gate.gate_emitted_facts(
        bundle=bundle,
        atoms_path=atoms,
        metadata_path=metadata,
        provenance_path=provenance,
        petta_root=tmp_path,
        accepted_path=accepted,
        rejected_path=rejected,
    )
    assert (result.accepted, result.rejected, result.facts) == (0, 1, 0)
    assert accepted.read_text(encoding="utf-8") == ""
    assert "a0" in json.loads(rejected.read_text(encoding="utf-8"))["error"]


def test_gate_does_not_publish_a_partial_graph(tmp_path, monkeypatch) -> None:
    bundle = _bundle()
    source = tmp_path / "documents.jsonl"
    document = _document()
    provisional = _document(provisional=True).assertions[0]
    document = document.model_copy(
        update={"assertions": (document.assertions[0], provisional)}
    )
    source.write_text(document.canonical_json() + "\n", encoding="utf-8")
    atoms = tmp_path / "facts.metta"
    metadata = tmp_path / "metadata.metta"
    provenance = tmp_path / "provenance.jsonl"
    emit_petta(
        bundle=bundle,
        input_path=source,
        atoms_path=atoms,
        metadata_path=metadata,
        provenance_path=provenance,
    )

    def one_failed_check(entries, **_kwargs):
        return {
            (index, name)
            for index, entry in enumerate(entries)
            for name, _ in entry.checks
            if (index, name) != (0, "a0")
        }

    monkeypatch.setattr(petta_gate, "_petta_checks", one_failed_check)
    accepted = tmp_path / "gated.metta"
    rejected = tmp_path / "rejected.jsonl"
    result = petta_gate.gate_emitted_facts(
        bundle=bundle,
        atoms_path=atoms,
        metadata_path=metadata,
        provenance_path=provenance,
        petta_root=tmp_path,
        accepted_path=accepted,
        rejected_path=rejected,
    )
    assert (result.accepted, result.rejected, result.facts) == (0, 1, 0)
    assert accepted.read_text(encoding="utf-8") == ""


def test_gate_rejects_provenance_ontology_mismatch_before_petta(
    tmp_path, monkeypatch
) -> None:
    bundle = _bundle()
    source = tmp_path / "documents.jsonl"
    source.write_text(_document().canonical_json() + "\n", encoding="utf-8")
    atoms = tmp_path / "facts.metta"
    metadata = tmp_path / "metadata.metta"
    provenance = tmp_path / "provenance.jsonl"
    emit_petta(
        bundle=bundle,
        input_path=source,
        atoms_path=atoms,
        metadata_path=metadata,
        provenance_path=provenance,
    )
    payload = json.loads(provenance.read_text(encoding="utf-8"))
    payload["ontology_version"] = "upper-v2"
    provenance.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    monkeypatch.setattr(petta_gate, "_petta_checks", lambda _entries, **_kwargs: set())
    accepted = tmp_path / "gated.metta"
    rejected = tmp_path / "rejected.jsonl"
    result = petta_gate.gate_emitted_facts(
        bundle=bundle,
        atoms_path=atoms,
        metadata_path=metadata,
        provenance_path=provenance,
        petta_root=tmp_path,
        accepted_path=accepted,
        rejected_path=rejected,
    )
    assert (result.accepted, result.rejected, result.facts) == (0, 2, 0)
    rows = [
        json.loads(line) for line in rejected.read_text(encoding="utf-8").splitlines()
    ]
    assert any("ontology version" in row["error"] for row in rows)
    assert any("unlinked" in row["error"] for row in rows)
