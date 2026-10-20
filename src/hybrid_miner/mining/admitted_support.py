"""Mine support from facts admitted by the PeTTa ingress gate."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from hybrid_miner.ingestion.petta_gate import GateResult, gate_emitted_facts
from hybrid_miner.mining.petta_runner import (
    MiningRun,
    mine_support,
    write_support_jsonl,
)
from hybrid_miner.ontology import OntologyBundle


class AdmissionRejected(ValueError):
    """No graph survived admission; no mining run was started."""


@dataclass(frozen=True, slots=True)
class AdmittedSupportRun:
    admission: GateResult
    mining: MiningRun
    support_path: Path


def mine_admitted_support(
    *,
    bundle: OntologyBundle,
    documents_path: str | Path,
    emitted_atoms_path: str | Path,
    metadata_path: str | Path,
    provenance_path: str | Path,
    petta_root: str | Path,
    miner_root: str | Path,
    admitted_atoms_path: str | Path,
    quarantine_path: str | Path,
    support_path: str | Path,
    admission_path: str | Path | None = None,
    min_support: int = 2,
    swipl: str = "swipl",
    timeout_seconds: int = 120,
) -> AdmittedSupportRun:
    """Admit emitted facts, then mine only the returned admitted graph.

    This is the integrated entry point. The raw-graph mining CLI remains
    available separately for standalone use of hybrid-miner.
    """

    manifest = (
        Path(admission_path)
        if admission_path is not None
        else Path(admitted_atoms_path).with_suffix(".admission.json")
    )
    paths = (
        documents_path,
        emitted_atoms_path,
        metadata_path,
        provenance_path,
        admitted_atoms_path,
        quarantine_path,
        support_path,
        manifest,
    )
    if len({Path(path).resolve() for path in paths}) != len(paths):
        raise ValueError("integrated mining input and output paths must differ")
    output = Path(support_path)
    if output.exists():
        raise FileExistsError(f"support output already exists: {output}")
    if (
        isinstance(min_support, bool)
        or not isinstance(min_support, int)
        or min_support < 1
    ):
        raise ValueError("min_support must be a positive integer")

    admission = gate_emitted_facts(
        bundle=bundle,
        documents_path=documents_path,
        admission_path=manifest,
        atoms_path=emitted_atoms_path,
        metadata_path=metadata_path,
        provenance_path=provenance_path,
        petta_root=petta_root,
        accepted_path=admitted_atoms_path,
        rejected_path=quarantine_path,
        swipl=swipl,
        timeout_seconds=timeout_seconds,
    )
    if not admission.facts:
        raise AdmissionRejected(
            f"PeTTa admitted no facts; mining did not start. "
            f"Inspect {admission.rejected_path}"
        )

    mining = mine_support(
        atoms_path=admission.atoms_path,
        petta_root=petta_root,
        miner_root=miner_root,
        min_support=min_support,
        swipl=swipl,
        timeout_seconds=timeout_seconds,
    )
    return AdmittedSupportRun(admission, mining, write_support_jsonl(mining, output))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hybrid_miner.mining.admitted_support"
    )
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--admission-output", type=Path)
    parser.add_argument("--emitted-atoms", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--petta-root", type=Path, required=True)
    parser.add_argument("--miner-root", type=Path, required=True)
    parser.add_argument("--admitted-output", type=Path, required=True)
    parser.add_argument("--quarantine-output", type=Path, required=True)
    parser.add_argument("--support-output", type=Path, required=True)
    parser.add_argument("--min-support", type=int, default=2)
    parser.add_argument("--swipl", default="swipl")
    options = parser.parse_args(argv)
    try:
        result = mine_admitted_support(
            bundle=OntologyBundle.load_json(options.bundle),
            documents_path=options.documents,
            admission_path=options.admission_output,
            emitted_atoms_path=options.emitted_atoms,
            metadata_path=options.metadata,
            provenance_path=options.provenance,
            petta_root=options.petta_root,
            miner_root=options.miner_root,
            admitted_atoms_path=options.admitted_output,
            quarantine_path=options.quarantine_output,
            support_path=options.support_output,
            min_support=options.min_support,
            swipl=options.swipl,
        )
    except AdmissionRejected as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(
        f"PeTTa admitted: {result.admission.accepted} evidence entries, "
        f"{result.admission.facts} facts -> {result.admission.atoms_path}"
    )
    print(f"Support records: {len(result.mining.records)} -> {result.support_path}")
    print(
        f"Documents: {result.admission.accepted_documents} accepted, "
        f"{result.admission.rejected_documents} quarantined"
    )
    print(f"Admission selection: {result.admission.admission_path}")
    print(f"Graph SHA-256: {result.mining.graph_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
