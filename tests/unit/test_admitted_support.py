"""The integrated mining entry point cannot bypass PeTTa admission."""

from pathlib import Path

import pytest

from hybrid_miner.ingestion.petta_gate import GateResult
from hybrid_miner.mining import admitted_support
from hybrid_miner.mining.petta_runner import MiningRun
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


def _paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "documents_path": tmp_path / "documents.jsonl",
        "emitted_atoms_path": tmp_path / "emitted.metta",
        "metadata_path": tmp_path / "metadata.metta",
        "provenance_path": tmp_path / "provenance.jsonl",
        "petta_root": tmp_path / "petta",
        "miner_root": tmp_path / "hyperon-miner",
        "admitted_atoms_path": tmp_path / "admitted.metta",
        "quarantine_path": tmp_path / "quarantine.jsonl",
        "support_path": tmp_path / "support.jsonl",
    }


def test_mining_receives_only_gate_returned_graph(tmp_path, monkeypatch) -> None:
    paths = _paths(tmp_path)
    admitted = tmp_path / "gate-returned.metta"
    gate = GateResult(1, 0, 1, admitted, paths["quarantine_path"])
    mining = MiningRun((), "digest", "petta-revision", "miner-revision")
    received: list[Path] = []

    monkeypatch.setattr(admitted_support, "gate_emitted_facts", lambda **_kwargs: gate)

    def run_miner(**kwargs):
        received.append(kwargs["atoms_path"])
        return mining

    monkeypatch.setattr(admitted_support, "mine_support", run_miner)
    monkeypatch.setattr(
        admitted_support,
        "write_support_jsonl",
        lambda _run, path: Path(path),
    )
    result = admitted_support.mine_admitted_support(bundle=_bundle(), **paths)

    assert received == [admitted]
    assert received != [paths["emitted_atoms_path"]]
    assert result.admission is gate
    assert result.mining is mining


def test_quarantine_prevents_mining_and_support_output(tmp_path, monkeypatch) -> None:
    paths = _paths(tmp_path)
    gate = GateResult(0, 1, 0, paths["admitted_atoms_path"], paths["quarantine_path"])
    monkeypatch.setattr(admitted_support, "gate_emitted_facts", lambda **_kwargs: gate)

    def unexpected_mining(**_kwargs):
        raise AssertionError("miner ran after PeTTa quarantine")

    monkeypatch.setattr(admitted_support, "mine_support", unexpected_mining)
    with pytest.raises(admitted_support.AdmissionRejected, match="did not start"):
        admitted_support.mine_admitted_support(bundle=_bundle(), **paths)
    assert not paths["support_path"].exists()


@pytest.mark.parametrize("invalid", [0, -1, True])
def test_invalid_support_threshold_is_rejected_before_admission(
    tmp_path, monkeypatch, invalid
) -> None:
    def unexpected_admission(**kwargs):
        raise AssertionError("admission must not run")

    monkeypatch.setattr(admitted_support, "gate_emitted_facts", unexpected_admission)
    with pytest.raises(ValueError, match="positive integer"):
        admitted_support.mine_admitted_support(
            bundle=_bundle(), **_paths(tmp_path), min_support=invalid
        )


def test_admission_manifest_cannot_alias_support_output(tmp_path) -> None:
    paths = _paths(tmp_path)
    with pytest.raises(ValueError, match="paths must differ"):
        admitted_support.mine_admitted_support(
            bundle=_bundle(), **paths, admission_path=paths["support_path"]
        )


def test_existing_support_output_fails_before_admission(tmp_path, monkeypatch) -> None:
    paths = _paths(tmp_path)
    paths["support_path"].write_text("prior run\n", encoding="utf-8")

    def unexpected_admission(**_kwargs):
        raise AssertionError("gate ran despite existing support output")

    monkeypatch.setattr(admitted_support, "gate_emitted_facts", unexpected_admission)
    with pytest.raises(FileExistsError, match="already exists"):
        admitted_support.mine_admitted_support(bundle=_bundle(), **paths)
