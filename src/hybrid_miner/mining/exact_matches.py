"""Recover exact PeTTa bindings and source evidence for mined support records.

This audit artifact is not a promoted PatternRecord. Raw matching support is
verified separately from support under the distinct-ground-facts policy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from collections import defaultdict
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from hybrid_miner.contracts.semantic_document import (
    ParserProvenance,
    SemanticAssertion,
    SemanticDocument,
)
from hybrid_miner.emission.symbols import (
    component,
    entity_symbol,
    evidence_symbol,
    predicate_symbol,
)
from hybrid_miner.mining.canonicalize_patterns import (
    Expression,
    canonicalize_pattern,
    parse_expression,
    render_expression,
)
from hybrid_miner.ontology import OntologyBundle

_MARKER = "CandidateBinding"
_MAX_BINDINGS = 10_000
_SUPPORT_POLICY = "distinct-ground-facts-v1"


class _ReusedGroundFact(ValueError):
    """A valid conjunction binding fails the distinct-ground-facts policy."""


class _ArtifactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Support(_ArtifactModel):
    pattern: str
    support: int = Field(strict=True, gt=0)
    graph_sha256: str
    petta_revision: str
    miner_revision: str


class _Evidence(_ArtifactModel):
    atom: str
    assertion_index: int = Field(strict=True, ge=0)
    evidence_id: str
    bundle_id: str
    bundle_version: str
    corpus_id: str
    document_id: str
    ontology_version: str
    parser: ParserProvenance
    assertion: SemanticAssertion


def _revision(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _json_lines(path: Path) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


def _facts(path: Path) -> set[str]:
    facts: set[str] = set()
    for number, line in enumerate(_json_lines(path), start=1):
        expression = parse_expression(line)
        if (
            not isinstance(expression, tuple)
            or len(expression) < 2
            or any(
                not isinstance(part, str) or part.startswith("$") for part in expression
            )
            or render_expression(expression) != line
            or line in facts
        ):
            raise ValueError(f"invalid or duplicate ground fact on line {number}")
        facts.add(line)
    if not facts:
        raise ValueError("fact graph is empty")
    return facts


def _documents(
    path: Path, bundle: OntologyBundle
) -> dict[tuple[str, str], SemanticDocument]:
    documents: dict[tuple[str, str], SemanticDocument] = {}
    for number, line in enumerate(_json_lines(path), start=1):
        document = bundle.validate_document(SemanticDocument.model_validate_json(line))
        key = (document.corpus_id, document.document_id)
        if key in documents:
            raise ValueError(f"duplicate document on line {number}: {key}")
        documents[key] = document
    return documents


def _expected_atom(
    row: _Evidence, versions: dict[str, str], bundle: OntologyBundle
) -> str:
    assertion = row.assertion
    if assertion.predicate == "ProvisionalRelation":
        version = f"{bundle.bundle_id}@{bundle.bundle_version}"
        assert assertion.provisional_relation_id is not None
        operands = [f"rel:{component(assertion.provisional_relation_id)}"]
    else:
        version = versions[assertion.predicate]
        operands = []
    if row.ontology_version != version:
        raise ValueError(f"wrong ontology version for {row.evidence_id}")
    operands.extend(
        entity_symbol(row.corpus_id, row.document_id, argument)
        for argument in assertion.arguments
    )
    return f"({predicate_symbol(assertion.predicate, version)} {' '.join(operands)})"


def _evidence_index(
    path: Path,
    facts: set[str],
    documents: dict[tuple[str, str], SemanticDocument],
    bundle: OntologyBundle,
) -> dict[str, tuple[_Evidence, ...]]:
    versions = {item.name: item.ontology_version for item in bundle.predicates}
    indexed: dict[str, list[_Evidence]] = defaultdict(list)
    seen: set[tuple[str, str, int]] = set()
    for number, line in enumerate(_json_lines(path), start=1):
        row = _Evidence.model_validate_json(line)
        identity = (row.corpus_id, row.document_id)
        key = (*identity, row.assertion_index)
        document = documents.get(identity)
        if (
            row.bundle_id != bundle.bundle_id
            or row.bundle_version != bundle.bundle_version
            or document is None
            or row.assertion_index >= len(document.assertions)
            or row.parser != document.parser
            or row.assertion != document.assertions[row.assertion_index]
            or row.evidence_id
            != evidence_symbol(row.corpus_id, row.document_id, row.assertion_index)
            or row.atom != _expected_atom(row, versions, bundle)
            or row.atom not in facts
            or key in seen
        ):
            raise ValueError(
                f"provenance line {number} disagrees with validated inputs"
            )
        indexed[row.atom].append(row)
        seen.add(key)
    expected = {
        (doc.corpus_id, doc.document_id, index)
        for doc in documents.values()
        for index in range(len(doc.assertions))
    }
    if seen != expected or set(indexed) != facts:
        raise ValueError("facts, provenance, and document assertions do not agree")
    return {atom: tuple(rows) for atom, rows in indexed.items()}


def _pattern_clauses(
    pattern: str, bundle: OntologyBundle
) -> tuple[tuple[tuple[str, ...], ...], tuple[str, ...]]:
    expression = parse_expression(pattern)
    if not isinstance(expression, tuple):
        raise ValueError("pattern must be an expression")
    raw_clauses: tuple[Expression, ...] = (
        expression[1:] if expression[0] == "," else (expression,)
    )
    allowed_heads = {
        predicate_symbol(item.name, item.ontology_version) for item in bundle.predicates
    }
    clauses: list[tuple[str, ...]] = []
    variables: list[str] = []
    for clause in raw_clauses:
        if (
            not isinstance(clause, tuple)
            or len(clause) < 2
            or any(not isinstance(item, str) for item in clause)
            or clause[0] not in allowed_heads
        ):
            raise ValueError("pattern has an unsupported or ungrounded predicate")
        flat = tuple(item for item in clause if isinstance(item, str))
        clauses.append(flat)
        for token in flat[1:]:
            if token.startswith("$") and token not in variables:
                variables.append(token)
    if not clauses or len(clauses) > 6:
        raise ValueError("unsupported pattern conjunction size")
    return tuple(clauses), tuple(variables)


def _query_bindings(
    patterns: dict[int, tuple[str, tuple[str, ...]]],
    *,
    atoms_path: Path,
    petta_root: Path,
    swipl: str,
    timeout_seconds: int,
) -> dict[int, list[tuple[str, ...]]]:
    script_lines = [f"!(import! &db {atoms_path.resolve().as_posix()})"]
    for index, (pattern, variables) in patterns.items():
        body = " ".join((_MARKER, str(index), *variables))
        script_lines.append(f"!(match &db {pattern} ({body}))")
    results: dict[int, list[tuple[str, ...]]] = defaultdict(list)
    with tempfile.TemporaryDirectory(prefix="hybrid-miner-bindings-") as temporary:
        script = Path(temporary) / "bindings.metta"
        script.write_text("\n".join(script_lines) + "\n", encoding="utf-8")
        process = subprocess.run(
            [
                swipl,
                "--stack_limit=8g",
                "-q",
                "-s",
                str(petta_root.resolve() / "src" / "main.pl"),
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
    if process.returncode:
        raise RuntimeError(
            f"PeTTa exact matching exited {process.returncode}: "
            f"{process.stderr.strip()}\n{process.stdout[-2000:]}"
        )
    for line in process.stdout.splitlines():
        line = line.strip()
        if not line.startswith(f"({_MARKER} "):
            continue
        parsed = parse_expression(line)
        if (
            not isinstance(parsed, tuple)
            or len(parsed) < 2
            or not isinstance(parsed[1], str)
            or not parsed[1].isdigit()
            or any(not isinstance(item, str) for item in parsed[2:])
        ):
            raise ValueError(f"invalid PeTTa binding: {line}")
        index = int(parsed[1])
        if index not in patterns or len(parsed) != 2 + len(patterns[index][1]):
            raise ValueError(f"unexpected PeTTa binding: {line}")
        results[index].append(
            tuple(item for item in parsed[2:] if isinstance(item, str))
        )
        if len(results[index]) > _MAX_BINDINGS:
            raise ValueError(f"pattern {index} exceeds {_MAX_BINDINGS} exact bindings")
    return results


def _linked_binding(
    clauses: tuple[tuple[str, ...], ...],
    variables: tuple[str, ...],
    values: tuple[str, ...],
    *,
    facts: set[str],
    evidence: dict[str, tuple[_Evidence, ...]],
    documents: dict[tuple[str, str], SemanticDocument],
) -> dict[str, object]:
    bindings = dict(zip(variables, values, strict=True))
    grounded = tuple(
        render_expression(tuple(bindings.get(part, part) for part in clause))
        for clause in clauses
    )
    if any(atom not in facts for atom in grounded):
        raise ValueError("PeTTa binding does not resolve to emitted facts")
    rows = [row for atom in grounded for row in evidence[atom]]
    identities = {(row.corpus_id, row.document_id) for row in rows}
    if len(identities) != 1:
        raise ValueError("one binding crosses source documents")
    if len(set(grounded)) != len(grounded):
        raise _ReusedGroundFact("one binding reuses a ground fact across clauses")
    linked = []
    for row in rows:
        document = documents[(row.corpus_id, row.document_id)]
        linked.append(
            {
                "evidence_id": row.evidence_id,
                "atom": row.atom,
                "predicate": row.assertion.predicate,
                "ontology_version": row.ontology_version,
                "status": row.assertion.status,
                "factuality": row.assertion.factuality,
                "confidence": row.assertion.confidence,
                "source_spans": [
                    {
                        "start": span.start,
                        "end": span.end,
                        "text": document.source_text[span.start : span.end],
                    }
                    for span in row.assertion.source_spans
                ],
            }
        )
    corpus_id, document_id = next(iter(identities))
    return {
        "variables": bindings,
        "facts": grounded,
        "corpus_id": corpus_id,
        "document_id": document_id,
        "evidence": linked,
    }


def recover_exact_matches(
    *,
    bundle: OntologyBundle,
    documents_path: Path,
    atoms_path: Path,
    provenance_path: Path,
    support_path: Path,
    petta_root: Path,
    miner_root: Path,
    output_path: Path,
    rejected_path: Path,
    swipl: str = "swipl",
    timeout_seconds: int = 120,
    min_support: int = 1,
) -> tuple[int, int]:
    """Validate artifacts, query PeTTa, and write accepted/rejected bindings."""

    inputs = (documents_path, atoms_path, provenance_path, support_path)
    outputs = (output_path, rejected_path)
    if len({path.resolve() for path in (*inputs, *outputs)}) != 6:
        raise ValueError("input and output paths must all differ")
    if any(path.exists() for path in outputs):
        raise FileExistsError("exact-match output already exists")
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive")
    if (
        isinstance(min_support, bool)
        or not isinstance(min_support, int)
        or min_support < 1
    ):
        raise ValueError("min_support must be a positive integer")
    facts = _facts(atoms_path)
    documents = _documents(documents_path, bundle)
    evidence = _evidence_index(provenance_path, facts, documents, bundle)
    graph_sha256 = hashlib.sha256(atoms_path.read_bytes()).hexdigest()
    petta_revision = _revision(petta_root)
    miner_revision = _revision(miner_root)
    support = [_Support.model_validate_json(line) for line in _json_lines(support_path)]
    patterns: dict[int, tuple[str, tuple[str, ...]]] = {}
    clauses_by_index: dict[int, tuple[tuple[str, ...], ...]] = {}
    rejected: list[dict[str, object]] = []
    for index, record in enumerate(support):
        if (
            record.graph_sha256 != graph_sha256
            or record.petta_revision != petta_revision
            or record.miner_revision != miner_revision
        ):
            raise ValueError(
                f"support line {index + 1} has a different graph or runtime"
            )
        try:
            if canonicalize_pattern(record.pattern) != record.pattern:
                raise ValueError("pattern is not in canonical form")
            clauses, variables = _pattern_clauses(record.pattern, bundle)
        except ValueError as error:
            rejected.append(
                {
                    "pattern": record.pattern,
                    "reported_support": record.support,
                    "reason": str(error),
                }
            )
            continue
        patterns[index] = (record.pattern, variables)
        clauses_by_index[index] = clauses
    queried = (
        _query_bindings(
            patterns,
            atoms_path=atoms_path,
            petta_root=petta_root,
            swipl=swipl,
            timeout_seconds=timeout_seconds,
        )
        if patterns
        else {}
    )
    accepted: list[dict[str, object]] = []
    for index, (pattern, variables) in patterns.items():
        record = support[index]
        raw = queried.get(index, [])
        linked: list[dict[str, object]] = []
        excluded: list[dict[str, object]] = []
        try:
            if len(raw) != record.support or len(set(raw)) != len(raw):
                raise ValueError(
                    f"reported support {record.support} disagrees with "
                    f"{len(raw)} distinct exact PeTTa bindings"
                )
            for values in raw:
                try:
                    linked.append(
                        _linked_binding(
                            clauses_by_index[index],
                            variables,
                            values,
                            facts=facts,
                            evidence=evidence,
                            documents=documents,
                        )
                    )
                except _ReusedGroundFact as error:
                    excluded.append(
                        {
                            "variables": dict(zip(variables, values, strict=True)),
                            "reason": str(error),
                        }
                    )
            if len(linked) < min_support:
                raise ValueError(
                    f"qualifying support {len(linked)} is below minimum {min_support}; "
                    "a binding that reuses a ground fact is excluded"
                )
        except ValueError as error:
            rejected.append(
                {
                    "pattern": pattern,
                    "reported_support": record.support,
                    "reason": str(error),
                    "qualifying_support": len(linked),
                    "excluded_bindings": excluded,
                    "support_policy": _SUPPORT_POLICY,
                    "minimum_support": min_support,
                }
            )
            continue
        accepted.append(
            {
                "pattern": pattern,
                "reported_support": record.support,
                "qualifying_support": len(linked),
                "excluded_bindings": excluded,
                "support_policy": _SUPPORT_POLICY,
                "minimum_support": min_support,
                "exact_bindings": linked,
                "graph_sha256": graph_sha256,
                "petta_revision": petta_revision,
                "miner_revision": miner_revision,
                "bundle_id": bundle.bundle_id,
                "bundle_version": bundle.bundle_version,
            }
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rejected_path.parent.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        with ExitStack() as stack:
            accepted_file = stack.enter_context(output_path.open("x", encoding="utf-8"))
            created.append(output_path)
            rejected_file = stack.enter_context(
                rejected_path.open("x", encoding="utf-8")
            )
            created.append(rejected_path)
            for item in accepted:
                accepted_file.write(
                    json.dumps(item, sort_keys=True, ensure_ascii=False) + "\n"
                )
            for item in rejected:
                rejected_file.write(
                    json.dumps(item, sort_keys=True, ensure_ascii=False) + "\n"
                )
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return len(accepted), len(rejected)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = argparse.ArgumentParser(
        prog="python -m hybrid_miner.mining.exact_matches"
    )
    for name in (
        "bundle",
        "documents",
        "atoms",
        "provenance",
        "support",
        "petta-root",
        "miner-root",
        "output",
        "rejected-output",
    ):
        arguments.add_argument(f"--{name}", type=Path, required=True)
    arguments.add_argument("--swipl", default="swipl")
    arguments.add_argument(
        "--min-support",
        type=int,
        default=1,
        help="minimum distinct-fact bindings after filtering (default: 1)",
    )
    options = arguments.parse_args(argv)
    try:
        accepted, rejected = recover_exact_matches(
            bundle=OntologyBundle.load_json(options.bundle),
            documents_path=options.documents,
            atoms_path=options.atoms,
            provenance_path=options.provenance,
            support_path=options.support,
            petta_root=options.petta_root,
            miner_root=options.miner_root,
            output_path=options.output,
            rejected_path=options.rejected_output,
            swipl=options.swipl,
            min_support=options.min_support,
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Evidence-backed patterns: {accepted} -> {options.output}")
    print(f"Rejected patterns: {rejected} -> {options.rejected_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
