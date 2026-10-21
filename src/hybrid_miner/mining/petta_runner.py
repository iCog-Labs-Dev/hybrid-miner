"""Execute the support-count baseline on a caller-supplied PeTTa fact graph.

This layer has no SemanticDocument, ontology-bundle, or extraction-provenance
dependency. Semantic-document admission is provided by admitted_support.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from hybrid_miner.mining.canonicalize_patterns import canonicalize_pattern

_MINER_MODULES = (
    "experiments/utils/surp-utils/boost_petta.py",
    "experiments/utils/common-utils",
    "experiments/utils/index_to_var",
    "experiments/utils/constants",
    "experiments/utils/TruthValue",
    "experiments/utils/surp-utils/partition-metta",
    "experiments/utils/surp-utils/binomialMetta",
    "experiments/utils/surp-utils/beta-dist",
    "experiments/utils/surp-utils/surp-utils",
    "experiments/utils/surp-utils/IIsurp_utils",
    "experiments/utils/sort",
    "experiments/utils/variable-combinations",
    "experiments/utils/surp-utils/blk-abstractness",
    "experiments/utils/remove_useless_clauses",
    "experiments/utils/surp-utils/eq-prob-petta",
    "experiments/utils/surp-utils/bs-utils",
    "experiments/utils/surp-utils/jsd-helper",
    "experiments/truth-values/est-tv",
    "experiments/truth-values/emp-tv",
    "experiments/surprisingness/emp-prob",
    "experiments/surprisingness/isurp",
    "experiments/surprisingness/isurp-old",
    "experiments/surprisingness/jsd",
    "experiments/surprisingness/IIsurp-old",
    "experiments/frequent-pattern-miner/valuation",
    "experiments/frequent-pattern-miner/shallow-abstractions",
    "experiments/frequent-pattern-miner/specialization",
    "experiments/frequent-pattern-miner/candidate",
    "experiments/frequent-pattern-miner/conjunction-expansion",
    "experiments/frequent-pattern-miner/frequent-pattern-miner-generic",
    "experiments/pattern-miner/pattern-miner",
)
_SUPPORT = re.compile(r"^\(supportOf (?P<pattern>.+) (?P<support>\d+)\)$")


@dataclass(frozen=True, slots=True)
class SupportCount:
    pattern: str
    support: int


@dataclass(frozen=True, slots=True)
class MiningRun:
    """Exact support records with the revisions and graph digest used."""

    records: tuple[SupportCount, ...]
    graph_sha256: str
    petta_revision: str
    miner_revision: str


def _revision(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _runner_source(miner_root: Path, atoms: Path, min_support: int) -> str:
    # Upstream depth zero performs the base conjunction expansion (two clauses
    # for atomic seeds). It is not a switch that disables conjunction mining.
    imports = [
        f"!(import! &self {(miner_root / module).as_posix()})"
        for module in _MINER_MODULES
    ]
    return "\n".join(
        [
            *imports,
            f"!(import! &db {atoms.as_posix()})",
            "!(bind! &res (new-space))",
            f"!(pattern-miner &res &db $x {min_support} 0 none none none True)",
            "!(get-atoms &res)",
            "",
        ]
    )


def mine_support(
    *,
    atoms_path: str | Path,
    petta_root: str | Path,
    miner_root: str | Path,
    min_support: int = 2,
    swipl: str = "swipl",
    timeout_seconds: int = 120,
) -> MiningRun:
    """Mine an already prepared graph without asserting semantic admission.

    The fixed upstream depth-zero baseline expands atomic seeds once. Results
    are raw binding-support counts, not source-audited or promoted templates.
    """

    if min_support < 1:
        raise ValueError("min_support must be positive")
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive")
    atoms = Path(atoms_path).resolve()
    petta = Path(petta_root).resolve()
    miner = Path(miner_root).resolve()
    main = petta / "src" / "main.pl"
    if not atoms.is_file():
        raise FileNotFoundError(f"PeTTa fact graph not found: {atoms}")
    if not main.is_file():
        raise FileNotFoundError(f"PeTTa entry point not found: {main}")
    if not (miner / "experiments" / "pattern-miner" / "pattern-miner.metta").is_file():
        raise FileNotFoundError(f"Hyperon Miner checkout not found: {miner}")
    graph_sha256 = hashlib.sha256(atoms.read_bytes()).hexdigest()

    with tempfile.TemporaryDirectory(prefix="hybrid-miner-") as temporary:
        script = Path(temporary) / "run.metta"
        script.write_text(_runner_source(miner, atoms, min_support), encoding="utf-8")
        result = subprocess.run(
            [
                swipl,
                "--stack_limit=8g",
                "-q",
                "-s",
                str(main),
                "--",
                str(script),
                "--silent",
            ],
            cwd=temporary,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    if result.returncode:
        raise RuntimeError(
            f"PeTTa miner exited {result.returncode}: "
            f"{result.stderr.strip()}\n{result.stdout[-2000:]}"
        )
    support_by_pattern: dict[str, int] = {}
    for line in result.stdout.splitlines():
        match = _SUPPORT.fullmatch(line.strip())
        if match is None:
            continue
        pattern = canonicalize_pattern(match.group("pattern"))
        support = int(match.group("support"))
        previous = support_by_pattern.setdefault(pattern, support)
        if previous != support:
            raise ValueError(f"conflicting support counts for {pattern}")
    records = tuple(
        SupportCount(pattern, support_by_pattern[pattern])
        for pattern in sorted(support_by_pattern)
    )
    return MiningRun(records, graph_sha256, _revision(petta), _revision(miner))


def write_support_jsonl(run: MiningRun, output_path: str | Path) -> Path:
    """Persist observed support records without overwriting prior runs."""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as writer:
        for record in run.records:
            writer.write(
                json.dumps(
                    {
                        "graph_sha256": run.graph_sha256,
                        "miner_revision": run.miner_revision,
                        "pattern": record.pattern,
                        "petta_revision": run.petta_revision,
                        "support": record.support,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
    return output
