"""The raw graph interface remains independent of extraction contracts."""

import subprocess
import sys
from pathlib import Path

from hybrid_miner.mining.petta_runner import _runner_source


def test_raw_graph_import_does_not_require_semantic_ingestion() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "sys.modules['hybrid_miner.contracts'] = None; "
            "sys.modules['hybrid_miner.ontology'] = None; "
            "sys.modules['hybrid_miner.ingestion'] = None; "
            "from hybrid_miner.mining.__main__ import main; "
            "main(['--help'])",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "No ontology bundle" in result.stdout
    assert "--bundle" not in result.stdout


def test_raw_runner_preserves_pinned_expansion_baseline() -> None:
    source = _runner_source(Path("miner"), Path("graph.metta"), 2)
    assert "!(import! &db graph.metta)" in source
    assert "!(pattern-miner &res &db $x 2 0 none none none True)" in source
    assert "!(get-atoms &res)" in source
