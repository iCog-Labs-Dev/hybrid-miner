"""Accepted-subset handoffs preserve complete documents and source identities."""

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from hybrid_miner.contracts import SemanticDocument
from hybrid_miner.emission.emit_petta import emit_petta
from hybrid_miner.ingestion import petta_gate
from hybrid_miner.ingestion.admission import AdmissionManifest, excluded_documents
from hybrid_miner.mining.exact_matches import _documents, _evidence_index, _facts
from hybrid_miner.ontology import OntologyBundle


@pytest.fixture
def admitted_subset(tmp_path, monkeypatch):
    bundle = OntologyBundle.model_validate(
        {
            "contract_version": "ontology-bundle-v1",
            "bundle_id": "network",
            "bundle_version": "1",
            "ontology_versions": ["network-v1"],
            "types": ["Node"],
            "predicates": [
                {
                    "name": "Connects",
                    "ontology_version": "network-v1",
                    "argument_types": [["Node"], ["Node"]],
                }
            ],
        }
    )
    document = {
        "document_id": "A",
        "corpus_id": "network-corpus",
        "source_text": "A links B.",
        "parser": {
            "model": "producer",
            "parser_version": "v1",
            "prompt_version": "v1",
            "ontology_versions": ["network-v1"],
        },
        "entities": [
            {"id": "a", "type": "Node", "span": {"start": 0, "end": 1}},
            {"id": "b", "type": "Node", "span": {"start": 8, "end": 9}},
        ],
        "assertions": [
            {
                "predicate": "Connects",
                "arguments": ["a", "b"],
                "argument_types": ["Node", "Node"],
                "status": "extracted-hypothesis",
                "confidence": 0.9,
                "source_spans": [{"start": 0, "end": 9}],
            }
        ],
    }
    source = tmp_path / "documents.jsonl"
    documents = []
    for identity in ("A", "B", "C"):
        value = deepcopy(document)
        value["document_id"] = identity
        documents.append(SemanticDocument.model_validate(value))
    source.write_text(
        "".join(d.canonical_json() + "\n" for d in documents), encoding="utf-8"
    )
    atoms, meta, provenance = (
        tmp_path / n for n in ("facts.metta", "meta.metta", "provenance.jsonl")
    )
    emit_petta(
        bundle=bundle,
        input_path=source,
        atoms_path=atoms,
        metadata_path=meta,
        provenance_path=provenance,
    )
    rows = [json.loads(line) for line in provenance.read_text().splitlines()]
    rows[1]["assertion"]["confidence"] = 0.1
    provenance.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    monkeypatch.setattr(
        petta_gate,
        "_petta_checks",
        lambda entries, **kwargs: (set(range(len(entries))), set()),
    )
    result = petta_gate.gate_emitted_facts(
        bundle=bundle,
        documents_path=source,
        atoms_path=atoms,
        metadata_path=meta,
        provenance_path=provenance,
        petta_root=tmp_path,
        accepted_path=tmp_path / "admitted.metta",
        rejected_path=tmp_path / "quarantine.jsonl",
    )
    assert result.admission_path is not None
    return bundle, source, provenance, result


def test_mixed_batch_admits_only_complete_documents(admitted_subset):
    bundle, source, provenance, result = admitted_subset
    assert (result.accepted_documents, result.rejected_documents, result.facts) == (
        2,
        1,
        2,
    )
    assert result.admission_path is not None
    excluded = excluded_documents(
        result.admission_path,
        bundle=bundle,
        documents_path=source,
        provenance_path=provenance,
        atoms_path=result.atoms_path,
    )
    assert excluded == {("network-corpus", "B")}
    documents = _documents(source, bundle, excluded)
    assert set(documents) == {("network-corpus", "A"), ("network-corpus", "C")}
    facts = _facts(result.atoms_path)
    evidence = _evidence_index(provenance, facts, documents, bundle, excluded)
    assert set(evidence) == facts
    assert {row.assertion_index for rows in evidence.values() for row in rows} == {0}
    assert all(":B:" not in atom for atom in facts)


def test_partial_graph_without_selection_is_not_silently_accepted(admitted_subset):
    bundle, source, provenance, result = admitted_subset
    with pytest.raises(ValueError, match="disagrees with validated inputs"):
        _evidence_index(
            provenance, _facts(result.atoms_path), _documents(source, bundle), bundle
        )


@pytest.mark.parametrize("changed", ["documents", "provenance", "graph", "bundle"])
def test_selection_cannot_be_reused_with_changed_artifacts(admitted_subset, changed):
    bundle, source, provenance, result = admitted_subset
    if changed == "bundle":
        bundle = bundle.model_copy(update={"bundle_version": "2"})
    else:
        target = {
            "documents": source,
            "provenance": provenance,
            "graph": result.atoms_path,
        }[changed]
        target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="different input artifacts"):
        excluded_documents(
            result.admission_path,
            bundle=bundle,
            documents_path=source,
            provenance_path=provenance,
            atoms_path=result.atoms_path,
        )


def test_admission_version_and_document_coverage_are_required(
    admitted_subset, tmp_path
):
    bundle, source, provenance, result = admitted_subset
    payload = json.loads(result.admission_path.read_text(encoding="utf-8"))
    unversioned = dict(payload)
    del unversioned["contract_version"]
    with pytest.raises(ValidationError):
        AdmissionManifest.model_validate(unversioned)
    payload["decisions"] = payload["decisions"][:-1]
    wrong = tmp_path / "wrong-selection.json"
    wrong.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="do not cover"):
        excluded_documents(
            wrong,
            bundle=bundle,
            documents_path=source,
            provenance_path=provenance,
            atoms_path=result.atoms_path,
        )


def test_unlinked_graph_atom_is_a_run_failure(admitted_subset, tmp_path):
    bundle, source, provenance, _ = admitted_subset
    atoms = tmp_path / "facts.metta"
    atoms.write_text(
        atoms.read_text(encoding="utf-8") + "(Connects@network-v1 orphan1 orphan2)\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="ownership is unknown"):
        petta_gate.gate_emitted_facts(
            bundle=bundle,
            documents_path=source,
            atoms_path=atoms,
            metadata_path=tmp_path / "meta.metta",
            provenance_path=provenance,
            petta_root=tmp_path,
            accepted_path=tmp_path / "unlinked.metta",
            rejected_path=tmp_path / "unlinked-rejected.jsonl",
        )
    assert not (tmp_path / "unlinked.metta").exists()


def test_duplicate_fact_quarantines_its_document_not_others(admitted_subset, tmp_path):
    bundle, source, provenance, _ = admitted_subset
    atoms = tmp_path / "facts.metta"
    content = atoms.read_text(encoding="utf-8")
    atoms.write_text(content + content.splitlines()[0] + "\n", encoding="utf-8")
    result = petta_gate.gate_emitted_facts(
        bundle=bundle,
        documents_path=source,
        atoms_path=atoms,
        metadata_path=tmp_path / "meta.metta",
        provenance_path=provenance,
        petta_root=tmp_path,
        accepted_path=tmp_path / "deduplicated.metta",
        rejected_path=tmp_path / "deduplicated-rejected.jsonl",
    )
    assert (result.accepted_documents, result.rejected_documents) == (1, 2)
    assert ":C:" in result.atoms_path.read_text(encoding="utf-8")


def test_single_document_failure_does_not_relax_metadata_consistency(tmp_path):
    entry = petta_gate._Entry(
        1, "{}", "(X a)", "ev:x", ("(meta (Factuality ev:x asserted))",)
    )
    observed = {
        ("Factuality", "ev:x"): {
            "(Factuality ev:x asserted)",
            "(Factuality ev:x hypothetical)",
        }
    }
    assert "contradictory" in petta_gate._metadata_problem(entry, observed)
    with pytest.raises(ValueError, match="canonical ground"):
        petta_gate._metadata_key("(Factuality ev:x $value)")
    with pytest.raises(ValueError, match="unsupported evidence"):
        petta_gate._metadata_key("(= atom replacement)")
