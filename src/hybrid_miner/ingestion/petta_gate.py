"""Admit emitted assertion and rule graphs with their required PeTTa evidence.

Documents are checked with the shared source contract. PeTTa checks emitted
facts, evidence and bundle-derived signatures. A failed document is quarantined
as a whole; intact documents are released with a hash-bound selection manifest.
Malformed or unassignable artifacts and runtime failures stop the run.
"""

from __future__ import annotations

import argparse
import importlib.resources
import json
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hybrid_miner.contracts.semantic_document import (
    ParserProvenance,
    SemanticAssertion,
    SemanticDocument,
    SemanticRule,
)
from hybrid_miner.emission import symbols
from hybrid_miner.emission.emit_petta import assertion_metadata, assertion_record
from hybrid_miner.emission.semantic_rules import (
    document_rule_graph,
    rule_graph,
)
from hybrid_miner.ingestion.admission import (
    AdmissionManifest,
    DocumentDecision,
    DocumentKey,
    bundle_digest,
    document_key,
    document_records,
    file_digest,
)
from hybrid_miner.mining.canonicalize_patterns import (
    parse_expression,
    render_expression,
)
from hybrid_miner.ontology import OntologyBundle

_DECISION = re.compile(r"^\(Gate(?P<decision>Accepted|Quarantined) (?P<index>\d+)\)$")


@dataclass(frozen=True, slots=True)
class GateResult:
    accepted: int
    rejected: int
    facts: int
    atoms_path: Path
    rejected_path: Path
    accepted_documents: int = 0
    rejected_documents: int = 0
    admission_path: Path | None = None


@dataclass(frozen=True, slots=True)
class _Entry:
    line_number: int
    raw_line: str
    fact: str
    evidence_id: str
    requirements: tuple[str, ...]

    @property
    def identity(self) -> DocumentKey:
        return document_key(json.loads(self.raw_line))


def _schema_atoms(bundle: OntologyBundle) -> list[str]:
    """Compile trusted declarations from the caller's bundle, never metadata."""
    result = [f"(KnownType {symbols.component(t)})" for t in bundle.types]
    provisional = symbols.predicate_symbol(
        "ProvisionalRelation", f"{bundle.bundle_id}@{bundle.bundle_version}"
    )
    result.append(f"(ProvisionalHead {provisional})")
    for signature in bundle.predicates:
        head = symbols.predicate_symbol(signature.name, signature.ontology_version)
        result.append(f"(PredicateArity {head} {len(signature.argument_types)})")
        result.extend(
            f"(AllowedType {head} {index} {symbols.component(kind)})"
            for index, allowed in enumerate(signature.argument_types)
            for kind in allowed
        )
    return result


def _signature_requirements(
    assertion: SemanticAssertion, bundle: OntologyBundle
) -> list[str]:
    result = [
        f"(schema (KnownType {symbols.component(kind)}))"
        for kind in assertion.argument_types
    ]
    if assertion.predicate == "ProvisionalRelation":
        head = symbols.predicate_symbol(
            "ProvisionalRelation", f"{bundle.bundle_id}@{bundle.bundle_version}"
        )
        result.append(f"(schema (ProvisionalHead {head}))")
        return result
    signature = next(
        (p for p in bundle.predicates if p.name == assertion.predicate), None
    )
    if signature is None:
        raise ValueError(f"undeclared predicate {assertion.predicate!r}")
    head = symbols.predicate_symbol(signature.name, signature.ontology_version)
    result.append(f"(schema (PredicateArity {head} {len(assertion.arguments)}))")
    result.extend(
        f"(schema (AllowedType {head} {position} {symbols.component(kind)}))"
        for position, kind in enumerate(assertion.argument_types)
    )
    return result


def _entry(
    *,
    line_number: int,
    raw_line: str,
    bundle: OntologyBundle,
) -> _Entry:
    payload = json.loads(raw_line)
    if not isinstance(payload, dict):
        raise ValueError("provenance record must be an object")
    if payload.get("kind") == "rule":
        return _rule_entry(line_number, raw_line, payload, bundle)
    assertion = SemanticAssertion.model_validate(payload["assertion"])
    corpus_id, document_id = document_key(payload)
    parser = ParserProvenance.model_validate(payload["parser"])
    if parser.ontology_versions != bundle.ontology_versions:
        raise ValueError("parser ontology versions do not match the bundle")
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

    requirements = _signature_requirements(assertion, bundle)

    def require(space: str, atom: str) -> None:
        kind = "fact" if space == "facts" else "meta"
        requirements.append(f"({kind} {atom})")

    require("facts", fact)
    for metadata_atom in assertion_metadata(
        assertion=assertion,
        parser=parser,
        corpus_id=corpus_id,
        document_id=document_id,
        index=index,
        fact=fact,
        version=version,
    ):
        require("meta", metadata_atom)
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
    if assertion.conditional_on is not None:
        condition = symbols.entity_symbol(
            corpus_id, document_id, assertion.conditional_on
        )
        require("meta", f"(EntityType {condition} Clause)")
    return _Entry(
        line_number=line_number,
        raw_line=raw_line,
        fact=fact,
        evidence_id=evidence_id,
        requirements=tuple(requirements),
    )


def _rule_entry(
    line_number: int, raw_line: str, payload: dict[str, Any], bundle: OntologyBundle
) -> _Entry:
    rule_index = payload["rule_index"]
    if (
        isinstance(rule_index, bool)
        or not isinstance(rule_index, int)
        or rule_index < 0
    ):
        raise ValueError("invalid rule index")
    rule = SemanticRule.model_validate(payload["rule"])
    entity_types = payload["entity_types"]
    if not isinstance(entity_types, dict) or any(
        not isinstance(k, str) or not isinstance(v, str)
        for k, v in entity_types.items()
    ):
        raise ValueError("rule entity_types must map entity IDs to types")
    parser = ParserProvenance.model_validate(payload["parser"])
    if set(parser.ontology_versions) != set(bundle.ontology_versions):
        raise ValueError("rule parser ontology versions differ from bundle")
    # No source text is available here. Full document checks occur at ingestion;
    # admission verifies the complete, deterministically emitted rule artifact.
    parts = rule_graph(
        corpus_id=payload["corpus_id"],
        document_id=payload["document_id"],
        parser=parser,
        rule=rule,
        rule_index=payload["rule_index"],
        entity_types=payload["entity_types"],
        bundle=bundle,
    )
    index = payload["fact_index"]
    if (
        isinstance(index, bool)
        or not isinstance(index, int)
        or not 0 <= index < len(parts)
    ):
        raise ValueError("invalid rule graph fact index")
    if parts[index].evidence != payload:
        raise ValueError("rule provenance disagrees with its emitted graph identity")
    requirements = tuple(
        dict.fromkeys(
            requirement
            for part in parts
            for requirement in (
                f"(fact {part.atom})",
                *(f"(meta {atom})" for atom in part.metadata),
            )
        )
    )
    requirements += tuple(
        requirement
        for literal in (*rule.antecedents, *rule.consequents)
        for requirement in _signature_requirements(literal, bundle)
    )
    requirements += tuple(
        f"(schema (KnownType {symbols.component(v.type)}))" for v in rule.variables
    )
    return _Entry(
        line_number,
        raw_line,
        parts[index].atom,
        str(parts[index].evidence["evidence_id"]),
        requirements,
    )


def _petta_checks(
    entries: list[_Entry],
    *,
    bundle: OntologyBundle,
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
        required_facts = {
            requirement[6:-1]
            for entry in entries
            for requirement in entry.requirements
            if requirement.startswith("(fact ")
        }
        for source, name in (
            (atoms_path, "facts.metta"),
            (metadata_path, "meta.metta"),
        ):
            unique_lines = dict.fromkeys(
                line
                for line in source.read_text(encoding="utf-8").splitlines()
                if line and (source == metadata_path or line in required_facts)
            )
            (root / name).write_text("\n".join(unique_lines) + "\n", encoding="utf-8")
        (root / "schema.metta").write_text(
            "\n".join(_schema_atoms(bundle)) + "\n", encoding="utf-8"
        )
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
            "!(import! &schema schema)",
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
            "PeTTa did not produce exactly one ingress decision per evidence entry"
        )
    return accepted, quarantined


_METADATA_HEADS = frozenset(
    (
        "AssertionEvidence",
        "ParserProvenance",
        "PredicateOntology",
        "ParserOntology",
        "SourceSpan",
        "Alternative",
        "Factuality",
        "ConditionalOn",
        "PredicateIdentity",
        "AllowedType",
        "EntityType",
        "ProvisionalPredicate",
        "RuleEvidence",
        "RuleScope",
        "RuleGraphVersion",
        "RuleVariable",
        "RuleCatalogPredicate",
        "RuleProvisionalPredicate",
        "RuleLiteralPolarity",
    )
)
_EVIDENCE_HEADS = _METADATA_HEADS - {
    "PredicateIdentity",
    "AllowedType",
    "EntityType",
    "ProvisionalPredicate",
    "RuleVariable",
    "RuleCatalogPredicate",
    "RuleProvisionalPredicate",
}


def _metadata_key(atom: str) -> tuple[str, ...]:
    parsed = parse_expression(atom)
    if not isinstance(parsed, tuple) or len(parsed) < 2:
        raise ValueError("metadata must be a ground expression")
    head, owner = parsed[:2]
    if not isinstance(head, str) or not isinstance(owner, str):
        raise ValueError("metadata needs a head and ground owner")
    if head not in _METADATA_HEADS:
        raise ValueError(f"unsupported evidence metadata head {head!r}")

    def ground(value: object) -> bool:
        return (
            all(ground(v) for v in value)
            if isinstance(value, tuple)
            else (isinstance(value, str) and not value.startswith("$"))
        )

    if not ground(parsed) or render_expression(parsed) != atom:
        raise ValueError("metadata must use canonical ground syntax")
    if head in {"AllowedType", "ProvisionalPredicate", "RuleVariable"}:
        if len(parsed) < 3 or not isinstance(parsed[2], str):
            raise ValueError("metadata declaration lacks an identifying operand")
        return head, owner, parsed[2]
    return head, owner


def _metadata_index(path: Path) -> dict[tuple[str, ...], set[str]]:
    indexed: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for atom in path.read_text(encoding="utf-8").splitlines():
        if atom:
            indexed[_metadata_key(atom)].add(atom)
    return dict(indexed)


def _metadata_problem(
    entry: _Entry,
    observed: dict[tuple[str, ...], set[str]],
    evidence_keys: set[tuple[str, ...]] | None = None,
) -> str | None:
    expected: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for requirement in entry.requirements:
        if requirement.startswith("(meta "):
            atom = requirement[6:-1]
            expected[_metadata_key(atom)].add(atom)
    for key, atoms in expected.items():
        actual = observed.get(key, set())
        # AllowedType is a set of bundle-permitted types at this position.
        # PeTTa's separate trusted schema, not this metadata set, is authority.
        if actual and key[0] != "AllowedType" and actual != atoms:
            return (
                f"contradictory or changed metadata for {key[0]} ({entry.evidence_id})"
            )
    if evidence_keys is None:
        evidence_keys = {key for key in observed if key[1] == entry.evidence_id}
    for key in evidence_keys:
        if (
            key[0] in _EVIDENCE_HEADS
            and key[1] == entry.evidence_id
            and key not in expected
        ):
            return f"unexpected {key[0]} evidence for {entry.evidence_id}"
    return None


def gate_emitted_facts(
    *,
    bundle: OntologyBundle,
    documents_path: str | Path,
    atoms_path: str | Path,
    metadata_path: str | Path,
    provenance_path: str | Path,
    petta_root: str | Path,
    accepted_path: str | Path,
    rejected_path: str | Path,
    admission_path: str | Path | None = None,
    swipl: str = "swipl",
    timeout_seconds: int = 120,
) -> GateResult:
    """Admit complete documents and publish an artifact-bound accepted selection."""
    documents_source = Path(documents_path)
    atoms, metadata, provenance = map(
        Path, (atoms_path, metadata_path, provenance_path)
    )
    accepted, rejected = map(Path, (accepted_path, rejected_path))
    manifest_path = (
        Path(admission_path)
        if admission_path is not None
        else accepted.with_suffix(".admission.json")
    )
    sources = (documents_source, atoms, metadata, provenance)
    outputs = (accepted, rejected, manifest_path)
    if len({p.resolve() for p in (*sources, *outputs)}) != len(sources) + len(outputs):
        raise ValueError("all ingress paths must be distinct")
    for source in sources:
        if not source.is_file():
            raise FileNotFoundError(f"PeTTa ingress input not found: {source}")
    for output in outputs:
        if output.exists():
            raise FileExistsError(f"output already exists: {output}")
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive")
    snapshots = {p: file_digest(p) for p in sources}
    records = document_records(documents_source)
    failures: dict[DocumentKey, list[str]] = defaultdict(list)
    expected: dict[DocumentKey, dict[str, dict[str, object]]] = {}
    owners: dict[str, set[DocumentKey]] = defaultdict(set)
    for key, line in records.items():
        try:
            document = bundle.validate_document(
                SemanticDocument.model_validate_json(line)
            )
            encoded = [
                assertion_record(document, index, bundle)[1]
                for index in range(len(document.assertions))
            ]
            encoded.extend(
                part.evidence
                for index in range(len(document.rules))
                for part in document_rule_graph(document, bundle, index)
            )
            expected[key] = {str(e["evidence_id"]): e for e in encoded}
            for evidence in encoded:
                owners[str(evidence["atom"])].add(key)
        except (KeyError, TypeError, ValueError) as error:
            failures[key].append(f"invalid source document: {error}")

    entries: list[_Entry] = []
    seen: dict[DocumentKey, set[str]] = defaultdict(set)
    for number, line in enumerate(
        provenance.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line:
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"provenance line {number} is not an object")
        key = document_key(payload)
        if key not in records:
            raise ValueError(f"provenance line {number} has no source document")
        if isinstance(payload.get("atom"), str):
            owners[payload["atom"]].add(key)
        try:
            entry = _entry(line_number=number, raw_line=line, bundle=bundle)
            if entry.evidence_id in seen[key]:
                raise ValueError(f"duplicate evidence_id {entry.evidence_id!r}")
            seen[key].add(entry.evidence_id)
            if key in expected and payload != expected[key].get(entry.evidence_id):
                failures[key].append(f"provenance line {number} disagrees with source")
            entries.append(entry)
        except (KeyError, TypeError, ValueError) as error:
            failures[key].append(f"provenance line {number}: {error}")
    for key, expected_rows in expected.items():
        if set(expected_rows) - seen[key]:
            failures[key].append(
                "provenance does not cover every source assertion/rule part"
            )

    observed_facts: set[str] = set()
    for number, atom in enumerate(atoms.read_text(encoding="utf-8").splitlines(), 1):
        if not atom:
            continue
        if atom not in owners:
            raise ValueError(
                f"unlinked fact on graph line {number}; ownership is unknown"
            )
        if atom in observed_facts:
            for key in owners[atom]:
                failures[key].append(f"duplicate emitted fact on line {number}")
        observed_facts.add(atom)
    metadata_index = _metadata_index(metadata)
    keys_by_evidence: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    for meta_key in metadata_index:
        if meta_key[0] in _EVIDENCE_HEADS:
            keys_by_evidence[meta_key[1]].add(meta_key)
    for entry in entries:
        problem = _metadata_problem(
            entry, metadata_index, keys_by_evidence.get(entry.evidence_id, set())
        )
        if problem is not None:
            failures[entry.identity].append(problem)
    accepted.parent.mkdir(parents=True, exist_ok=True)
    if entries:
        petta_accepted, petta_quarantined = _petta_checks(
            entries,
            bundle=bundle,
            atoms_path=atoms,
            metadata_path=metadata,
            petta_root=Path(petta_root),
            swipl=swipl,
            timeout_seconds=timeout_seconds,
            temporary_parent=accepted.parent,
        )
    else:
        petta_accepted, petta_quarantined = set(), set()
    for index, entry in enumerate(entries):
        if index in petta_quarantined:
            failures[entry.identity].append(
                f"PeTTa quarantined {entry.evidence_id}: "
                "fact, evidence or bundle signature mismatch"
            )
        elif index not in petta_accepted:
            raise RuntimeError("PeTTa did not decide every evidence entry")
    if any(file_digest(p) != digest for p, digest in snapshots.items()):
        raise RuntimeError("admission inputs changed during execution")

    accepted_entries = [entry for entry in entries if not failures.get(entry.identity)]
    accepted_facts = dict.fromkeys(entry.fact for entry in accepted_entries)
    decisions = tuple(
        DocumentDecision(
            corpus_id=corpus,
            document_id=document,
            accepted=not failures.get((corpus, document)),
            reasons=tuple(dict.fromkeys(failures.get((corpus, document), []))),
        )
        for corpus, document in sorted(records)
    )
    quarantine = [
        {"corpus_id": d.corpus_id, "document_id": d.document_id, "error": reason}
        for d in decisions
        for reason in d.reasons
    ]
    for output in outputs:
        output.parent.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        with ExitStack() as stack:
            graph = stack.enter_context(
                accepted.open("x", encoding="utf-8", newline="\n")
            )
            created.append(accepted)
            rejection = stack.enter_context(
                rejected.open("x", encoding="utf-8", newline="\n")
            )
            created.append(rejected)
            graph.writelines(atom + "\n" for atom in accepted_facts)
            rejection.writelines(
                json.dumps(q, sort_keys=True) + "\n" for q in quarantine
            )
        manifest = AdmissionManifest(
            contract_version="document-admission-v1",
            bundle_sha256=bundle_digest(bundle),
            documents_sha256=snapshots[documents_source],
            provenance_sha256=snapshots[provenance],
            graph_sha256=file_digest(accepted),
            decisions=decisions,
        )
        manifest.write(manifest_path)
        created.append(manifest_path)
    except BaseException:
        for output in created:
            output.unlink(missing_ok=True)
        raise
    rejected_count = sum(not d.accepted for d in decisions)
    return GateResult(
        len(accepted_entries),
        rejected_count,
        len(accepted_facts),
        accepted,
        rejected,
        len(decisions) - rejected_count,
        rejected_count,
        manifest_path,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hybrid_miner.ingestion.petta_gate")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--admission-output", type=Path)
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
            documents_path=options.documents,
            admission_path=options.admission_output,
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
    print(
        f"PeTTa accepted: {result.accepted_documents} documents, {result.facts} facts"
    )
    print(f"Admission selection: {result.admission_path}")
    print(
        f"Quarantined documents: {result.rejected_documents} -> {result.rejected_path}"
    )
    print(f"Mineable graph: {result.atoms_path}")
    return 0 if result.facts else 2


if __name__ == "__main__":
    raise SystemExit(main())
