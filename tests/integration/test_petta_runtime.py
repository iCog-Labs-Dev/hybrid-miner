"""Exercise admission, mining, and evidence recovery in the real PeTTa runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from hybrid_miner.contracts import SemanticDocument
from hybrid_miner.emission.emit_petta import emit_petta
from hybrid_miner.ingestion import ingest_jsonl
from hybrid_miner.mining.admitted_support import (
    AdmissionRejected,
    mine_admitted_support,
)
from hybrid_miner.mining.exact_matches import recover_exact_matches
from hybrid_miner.ontology import OntologyBundle


def _runtime_root(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"{name} must point to the pinned runtime checkout")
    root = Path(value).resolve()
    if not root.is_dir():
        pytest.fail(f"{name} directory does not exist: {root}")
    return root


def _bundle() -> OntologyBundle:
    return OntologyBundle.model_validate(
        {
            "contract_version": "ontology-bundle-v1",
            "bundle_id": "petta-runtime-fixture",
            "bundle_version": "1",
            "ontology_versions": ["upper-v1"],
            "types": ["Entity", "Event"],
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


def _document(document_id: str, text: str, actor: str, event: str) -> SemanticDocument:
    return SemanticDocument.model_validate(
        {
            "document_id": document_id,
            "corpus_id": "petta-runtime-fixture",
            "source_text": text,
            "parser": {
                "model": "fixture",
                "parser_version": "fixture-v1",
                "prompt_version": "fixture-v1",
                "ontology_versions": ["upper-v1"],
            },
            "entities": [
                {"id": actor, "type": "Entity", "span": {"start": 0, "end": 4}},
                {"id": event, "type": "Event", "span": {"start": 5, "end": 9}},
            ],
            "assertions": [
                {
                    "predicate": "Event",
                    "arguments": [event],
                    "argument_types": ["Event"],
                    "status": "extracted-hypothesis",
                    "confidence": 0.9,
                    "source_spans": [{"start": 5, "end": 9}],
                },
                {
                    "predicate": "Agent",
                    "arguments": [event, actor],
                    "argument_types": ["Event", "Entity"],
                    "status": "extracted-hypothesis",
                    "confidence": 0.9,
                    "source_spans": [{"start": 0, "end": 9}],
                },
            ],
        }
    )


def test_real_petta_admission_mining_and_exact_bindings(tmp_path: Path) -> None:
    petta_root = _runtime_root("PETTA_ROOT")
    miner_root = _runtime_root("MINER_ROOT")
    bundle = _bundle()
    source = tmp_path / "source.jsonl"
    source.write_text(
        "\n".join(
            document.canonical_json()
            for document in (
                _document("doc-1", "Mira left.", "mira", "leaving"),
                _document("doc-2", "Nora sang.", "nora", "singing"),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    accepted = tmp_path / "accepted.jsonl"
    ingestion = ingest_jsonl(
        bundle=bundle,
        input_path=source,
        accepted_path=accepted,
        rejected_path=tmp_path / "ingestion-rejected.jsonl",
    )
    assert (ingestion.accepted, ingestion.rejected) == (2, 0)

    atoms = tmp_path / "facts.metta"
    metadata = tmp_path / "metadata.metta"
    provenance = tmp_path / "provenance.jsonl"
    emission = emit_petta(
        bundle=bundle,
        input_path=accepted,
        atoms_path=atoms,
        metadata_path=metadata,
        provenance_path=provenance,
    )
    assert (emission.assertions, emission.facts) == (4, 4)

    admitted = tmp_path / "admitted.metta"
    support = tmp_path / "support.jsonl"
    run = mine_admitted_support(
        bundle=bundle,
        emitted_atoms_path=atoms,
        metadata_path=metadata,
        provenance_path=provenance,
        petta_root=petta_root,
        miner_root=miner_root,
        admitted_atoms_path=admitted,
        quarantine_path=tmp_path / "quarantine.jsonl",
        support_path=support,
        min_support=2,
    )
    assert (run.admission.accepted, run.admission.rejected, run.admission.facts) == (
        4,
        0,
        4,
    )
    assert admitted.read_bytes() == atoms.read_bytes()
    assert any(record.support == 2 for record in run.mining.records)

    exact = tmp_path / "exact.jsonl"
    exact_accepted, exact_rejected = recover_exact_matches(
        bundle=bundle,
        documents_path=accepted,
        atoms_path=admitted,
        provenance_path=provenance,
        support_path=support,
        petta_root=petta_root,
        miner_root=miner_root,
        output_path=exact,
        rejected_path=tmp_path / "exact-rejected.jsonl",
    )
    assert (exact_accepted, exact_rejected) == (1, 2)
    rows = [json.loads(line) for line in exact.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["pattern"] == "(, (Agent@upper-v1 $v0 $v1) (Event@upper-v1 $v0))"
    assert rows[0]["reported_support"] == 2
    assert {binding["document_id"] for binding in rows[0]["exact_bindings"]} == {
        "doc-1",
        "doc-2",
    }
    rejected_rows = [
        json.loads(line)
        for line in (tmp_path / "exact-rejected.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert all("reuses a ground fact" in row["reason"] for row in rejected_rows)

    missing_metadata = tmp_path / "missing-metadata.metta"
    missing_metadata.write_text("", encoding="utf-8")
    blocked_support = tmp_path / "blocked-support.jsonl"
    blocked_graph = tmp_path / "blocked-graph.metta"
    with pytest.raises(AdmissionRejected, match="mining did not start"):
        mine_admitted_support(
            bundle=bundle,
            emitted_atoms_path=atoms,
            metadata_path=missing_metadata,
            provenance_path=provenance,
            petta_root=petta_root,
            miner_root=miner_root,
            admitted_atoms_path=blocked_graph,
            quarantine_path=tmp_path / "blocked-quarantine.jsonl",
            support_path=blocked_support,
            min_support=2,
        )
    assert blocked_graph.read_text(encoding="utf-8") == ""
    assert not blocked_support.exists()
    assert len((tmp_path / "blocked-quarantine.jsonl").read_text().splitlines()) == 4
