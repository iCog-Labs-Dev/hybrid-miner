"""Admit emitted assertion facts only when their PeTTa metadata is present.

Document and ontology validation precede emission. This adapter checks
provenance identity against the selected bundle, asks PeTTa for one admission
decision per assertion, and never releases a partially admitted mining graph.
It does not repeat predicate arity or argument-type validation, independently
validate raw source text, or discover predicates.
"""

from __future__ import annotations

import argparse
import importlib.resources
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hybrid_miner.contracts.semantic_document import SemanticAssertion
from hybrid_miner.emission import symbols
from hybrid_miner.ontology import OntologyBundle

_DECISION = re.compile(r"^\(Gate(?P<decision>Accepted|Quarantined) (?P<index>\d+)\)$")


@dataclass(frozen=True, slots=True)
class GateResult:
    accepted: int
    rejected: int
    facts: int
    atoms_path: Path
    rejected_path: Path


@dataclass(frozen=True, slots=True)
class _Entry:
    line_number: int
    raw_line: str
    fact: str
    evidence_id: str
    requirements: tuple[str, ...]


def _entry(
    *,
    line_number: int,
    raw_line: str,
    bundle: OntologyBundle,
) -> _Entry:
    payload = json.loads(raw_line)
    if not isinstance(payload, dict):
        raise ValueError("provenance record must be an object")
    assertion = SemanticAssertion.model_validate(payload["assertion"])
    corpus_id = str(payload["corpus_id"])
    document_id = str(payload["document_id"])
    index = payload["assertion_index"]
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ValueError("assertion_index must be a nonnegative integer")
    evidence_id = symbols.evidence_symbol(corpus_id, document_id, index)
    if payload["evidence_id"] != evidence_id:
        raise ValueError("evidence_id does not match document identity")
    signatures = {item.name: item for item in bundle.predicates}
    if assertion.predicate == "ProvisionalRelation":
        version = f"{bundle.bundle_id}@{bundle.bundle_version}"
        assert assertion.provisional_relation_id is not None
        relation = f"rel:{symbols.component(assertion.provisional_relation_id)}"
        arguments = [relation]
    else:
        signature = signatures.get(assertion.predicate)
        if signature is None:
            raise ValueError(f"undeclared predicate {assertion.predicate!r}")
        version = signature.ontology_version
        arguments = []
    if payload["ontology_version"] != version:
        raise ValueError("predicate ontology version does not match the bundle")
    if payload["bundle_id"] != bundle.bundle_id:
        raise ValueError("bundle_id does not match the selected bundle")
    if payload["bundle_version"] != bundle.bundle_version:
        raise ValueError("bundle_version does not match the selected bundle")
    head = symbols.predicate_symbol(assertion.predicate, version)
    entities = [
        symbols.entity_symbol(corpus_id, document_id, argument)
        for argument in assertion.arguments
    ]
    arguments.extend(entities)
    fact = f"({head} {' '.join(arguments)})"
    if payload["atom"] != fact:
        raise ValueError("fact does not match assertion and ontology identity")

    requirements: list[str] = []

    def require(space: str, atom: str) -> None:
        kind = "fact" if space == "facts" else "meta"
        requirements.append(f"({kind} {atom})")

    require("facts", fact)
    require(
        "meta",
        f"(AssertionEvidence {evidence_id} {fact} "
        f"extracted-hypothesis {repr(assertion.confidence)})",
    )
    if assertion.predicate == "ProvisionalRelation":
        require("meta", f"(ProvisionalPredicate {head} {relation})")
    else:
        require(
            "meta",
            f"(PredicateIdentity {head} "
            f"{symbols.component(assertion.predicate)} "
            f"{symbols.component(version)})",
        )
    for position, (entity, argument_type) in enumerate(
        zip(entities, assertion.argument_types, strict=True)
    ):
        encoded_type = symbols.component(argument_type)
        require("meta", f"(EntityType {entity} {encoded_type})")
        if assertion.predicate != "ProvisionalRelation":
            require(
                "meta",
                f"(AllowedType {head} {position} {encoded_type})",
            )
    for span in assertion.source_spans:
        require(
            "meta",
            f"(SourceSpan {evidence_id} {span.start} {span.end})",
        )
    return _Entry(
        line_number=line_number,
        raw_line=raw_line,
        fact=fact,
        evidence_id=evidence_id,
        requirements=tuple(requirements),
    )


def _petta_checks(
    entries: list[_Entry],
    *,
    atoms_path: Path,
    metadata_path: Path,
    petta_root: Path,
    swipl: str,
    timeout_seconds: int,
    temporary_parent: Path,
) -> tuple[set[int], set[int]]:
    main = petta_root / "src" / "main.pl"
    if not main.is_file():
        raise FileNotFoundError(f"PeTTa entry point not found: {main}")
    with tempfile.TemporaryDirectory(
        prefix="hybrid-miner-gate-", dir=temporary_parent
    ) as temporary:
        root = Path(temporary)
        shutil.copyfile(atoms_path, root / "facts.metta")
        shutil.copyfile(metadata_path, root / "meta.metta")
        rule_source = importlib.resources.files("hybrid_miner.ingestion").joinpath(
            "ingest_semantic_assertion.metta"
        )
        (root / "ingest_semantic_assertion.metta").write_bytes(rule_source.read_bytes())
        script = root / "gate.metta"
        lines = [
            "!(bind! &accepted (new-space))",
            "!(bind! &quarantine (new-space))",
            "!(import! &facts facts)",
            "!(import! &meta meta)",
            "!(import! &self ingest_semantic_assertion)",
        ]
        for index, entry in enumerate(entries):
            requirements = "()"
            for requirement in reversed(entry.requirements):
                requirements = f"(cons {requirement} {requirements})"
            lines.append(
                f"!(ingest-semantic-assertion {index} {entry.fact} {requirements})"
            )
        lines.extend(
            [
                "!(match &accepted (IngressAccepted $id $atom) (GateAccepted $id))",
                "!(match &quarantine (IngressQuarantined $id $atom) "
                "(GateQuarantined $id))",
            ]
        )
        script.write_text("\n".join(lines) + "\n", encoding="utf-8")
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
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    if result.returncode:
        raise RuntimeError(
            f"PeTTa ingress gate exited {result.returncode}: "
            f"{result.stderr.strip()}\n{result.stdout[-2000:]}"
        )
    accepted: set[int] = set()
    quarantined: set[int] = set()
    for line in result.stdout.splitlines():
        decision = _DECISION.fullmatch(line.strip())
        if decision:
            target = (
                accepted if decision.group("decision") == "Accepted" else quarantined
            )
            target.add(int(decision.group("index")))
    expected = set(range(len(entries)))
    if accepted & quarantined or accepted | quarantined != expected:
        raise RuntimeError(
            "PeTTa did not produce exactly one ingress decision per assertion"
        )
    return accepted, quarantined


def gate_emitted_facts(
    *,
    bundle: OntologyBundle,
    atoms_path: str | Path,
    metadata_path: str | Path,
    provenance_path: str | Path,
    petta_root: str | Path,
    accepted_path: str | Path,
    rejected_path: str | Path,
    swipl: str = "swipl",
    timeout_seconds: int = 120,
) -> GateResult:
    """Produce a PeTTa-checked fact graph and per-assertion quarantine."""

    atoms = Path(atoms_path)
    metadata = Path(metadata_path)
    provenance = Path(provenance_path)
    accepted = Path(accepted_path)
    rejected = Path(rejected_path)
    paths = (atoms, metadata, provenance, accepted, rejected)
    if len({path.resolve() for path in paths}) != len(paths):
        raise ValueError("all ingress paths must be distinct")
    for source in (atoms, metadata, provenance):
        if not source.is_file():
            raise FileNotFoundError(f"PeTTa ingress input not found: {source}")
    for output in (accepted, rejected):
        if output.exists():
            raise FileExistsError(f"output already exists: {output}")
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive")

    entries: list[_Entry] = []
    failures: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(
        provenance.read_text(encoding="utf-8").splitlines(), start=1
    ):
        try:
            entry = _entry(line_number=line_number, raw_line=line, bundle=bundle)
            if entry.evidence_id in seen_ids:
                raise ValueError(f"duplicate evidence_id {entry.evidence_id!r}")
            seen_ids.add(entry.evidence_id)
            entries.append(entry)
        except (KeyError, TypeError, ValueError) as error:
            failures.append(
                {"line_number": line_number, "raw_line": line, "error": str(error)}
            )
    expected_facts = {entry.fact for entry in entries}
    observed_facts: set[str] = set()
    for line_number, fact in enumerate(
        atoms.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if fact not in expected_facts or fact in observed_facts:
            failures.append(
                {
                    "source": "atoms",
                    "line_number": line_number,
                    "raw_line": fact,
                    "error": "unlinked or duplicate fact in emitted graph",
                }
            )
        observed_facts.add(fact)
    accepted.parent.mkdir(parents=True, exist_ok=True)
    rejected.parent.mkdir(parents=True, exist_ok=True)
    petta_accepted, petta_quarantined = _petta_checks(
        entries,
        atoms_path=atoms,
        metadata_path=metadata,
        petta_root=Path(petta_root),
        swipl=swipl,
        timeout_seconds=timeout_seconds,
        temporary_parent=accepted.parent,
    )
    accepted_facts: dict[str, None] = {}
    accepted_count = 0
    for index, entry in enumerate(entries):
        if index in petta_quarantined:
            failures.append(
                {
                    "line_number": entry.line_number,
                    "evidence_id": entry.evidence_id,
                    "error": (
                        "PeTTa quarantined the assertion: emitted fact or "
                        "required metadata is missing"
                    ),
                    "raw_line": entry.raw_line,
                }
            )
        else:
            if index not in petta_accepted:
                raise RuntimeError("PeTTa did not accept the assertion")
            accepted_facts[entry.fact] = None
            accepted_count += 1
    # Downstream exact-match audits require the documents, provenance, and
    # graph to describe the same assertions. Until a filtered-document
    # interchange exists, a rejected assertion invalidates the whole graph.
    if failures:
        accepted_facts.clear()
        accepted_count = 0
    with accepted.open("x", encoding="utf-8", newline="\n") as writer:
        for fact in accepted_facts:
            writer.write(fact + "\n")
    try:
        with rejected.open("x", encoding="utf-8", newline="\n") as writer:
            for failure in sorted(failures, key=lambda item: item["line_number"]):
                writer.write(
                    json.dumps(failure, ensure_ascii=False, sort_keys=True) + "\n"
                )
    except BaseException:
        accepted.unlink(missing_ok=True)
        raise
    return GateResult(
        accepted_count, len(failures), len(accepted_facts), accepted, rejected
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hybrid_miner.ingestion.petta_gate")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--atoms", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--petta-root", type=Path, required=True)
    parser.add_argument("--accepted-output", type=Path, required=True)
    parser.add_argument("--rejected-output", type=Path, required=True)
    parser.add_argument("--swipl", default="swipl")
    options = parser.parse_args(argv)
    try:
        result = gate_emitted_facts(
            bundle=OntologyBundle.load_json(options.bundle),
            atoms_path=options.atoms,
            metadata_path=options.metadata,
            provenance_path=options.provenance,
            petta_root=options.petta_root,
            accepted_path=options.accepted_output,
            rejected_path=options.rejected_output,
            swipl=options.swipl,
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"PeTTa accepted: {result.accepted} assertions, {result.facts} facts")
    print(f"PeTTa quarantine entries: {result.rejected} -> {result.rejected_path}")
    print(f"Mineable graph: {result.atoms_path}")
    return 2 if result.rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
