"""Execute the standalone interface without a document or ontology bundle."""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_real_raw_graph_cli(tmp_path: Path) -> None:
    atoms = tmp_path / "purchases.metta"
    atoms.write_text(
        "(Purchased buyer1 item1)\n(Product item1)\n"
        "(Purchased buyer2 item2)\n(Product item2)\n",
        encoding="utf-8",
    )
    output = tmp_path / "support.jsonl"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hybrid_miner.mining",
            "--atoms",
            str(atoms),
            "--petta-root",
            os.environ["PETTA_ROOT"],
            "--miner-root",
            os.environ["MINER_ROOT"],
            "--output",
            str(output),
            "--min-support",
            "2",
        ],
        capture_output=True,
        text=True,
        timeout=150,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rows = [
        json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        row["pattern"] == "(, (Product $v0) (Purchased $v1 $v0))"
        and row["support"] == 2
        for row in rows
    )
    assert all("@" not in row["pattern"] for row in rows)
    assert all(row["graph_sha256"] and row["petta_revision"] for row in rows)
