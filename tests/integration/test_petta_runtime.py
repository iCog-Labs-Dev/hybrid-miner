"""Exercise admission, mining, and evidence recovery in the real PeTTa runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from hybrid_miner.contracts import PatternRecord, SemanticDocument
from hybrid_miner.emission.emit_petta import emit_petta
from hybrid_miner.emission.symbols import predicate_symbol
from hybrid_miner.ingestion import ingest_jsonl
from hybrid_miner.ingestion.admission import AdmissionManifest
from hybrid_miner.ingestion.petta_gate import _entry, _petta_checks
from hybrid_miner.mining.admitted_support import (
    AdmissionRejected,
    mine_admitted_support,
)
from hybrid_miner.mining.build_pattern_records import build_pattern_records
from hybrid_miner.mining.exact_matches import _query_bindings, recover_exact_matches
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


def test_real_petta_preserves_rules_without_firing_them(tmp_path: Path) -> None:
    """A true premise does not execute the extracted rule's conclusion."""
    bundle = _bundle()
    documents = []
    rule_text = "All red things are nice."
    for number, actor in enumerate(("Mira", "Nora"), 1):
        source = f"{rule_text} {actor} is red."
        actor_start = source.index(actor)

        def literal(relation):
            return {
                "predicate": "ProvisionalRelation",
                "provisional_relation_id": relation,
                "arguments": ["$x"],
                "argument_types": ["Entity"],
                "status": "extracted-hypothesis",
                "factuality": "hypothetical",
                "confidence": 0.9,
                "source_spans": [{"start": 0, "end": len(rule_text)}],
            }

        documents.append(
            SemanticDocument.model_validate(
                {
                    "corpus_id": "rules",
                    "document_id": f"doc-{number}",
                    "source_text": source,
                    "parser": {
                        "model": "fixture",
                        "parser_version": "fixture-v1",
                        "prompt_version": "fixture-v1",
                        "ontology_versions": ["upper-v1"],
                    },
                    "entities": [
                        {
                            "id": actor.lower(),
                            "type": "Entity",
                            "span": {"start": actor_start, "end": actor_start + 4},
                        }
                    ],
                    "assertions": [
                        {
                            "predicate": "ProvisionalRelation",
                            "provisional_relation_id": "red",
                            "arguments": [actor.lower()],
                            "argument_types": ["Entity"],
                            "status": "extracted-hypothesis",
                            "factuality": "asserted",
                            "confidence": 0.9,
                            "source_spans": [
                                {"start": actor_start, "end": len(source) - 1}
                            ],
                        }
                    ],
                    "rules": [
                        {
                            "id": "r1",
                            "status": "extracted-hypothesis",
                            "variables": [{"id": "$x", "type": "Entity"}],
                            "antecedents": [literal("red")],
                            "consequents": [literal("nice")],
                            "source_spans": [{"start": 0, "end": len(rule_text)}],
                        }
                    ],
                }
            )
        )
    source = tmp_path / "documents.jsonl"
    source.write_text(
        "".join(d.canonical_json() + "\n" for d in documents), encoding="utf-8"
    )
    atoms, metadata, provenance = (
        tmp_path / name
        for name in ("facts.metta", "metadata.metta", "provenance.jsonl")
    )
    emitted = emit_petta(
        bundle=bundle,
        input_path=source,
        atoms_path=atoms,
        metadata_path=metadata,
        provenance_path=provenance,
    )
    assert (emitted.assertions, emitted.rules, emitted.facts) == (2, 2, 8)
    petta_root, miner_root = _runtime_root("PETTA_ROOT"), _runtime_root("MINER_ROOT")
    admitted, support = tmp_path / "admitted.metta", tmp_path / "support.jsonl"
    run = mine_admitted_support(
        bundle=bundle,
        documents_path=source,
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
    assert (run.admission.accepted, run.admission.rejected) == (8, 0)
    head = predicate_symbol(
        "ProvisionalRelation", f"{bundle.bundle_id}@{bundle.bundle_version}"
    )
    matches = _query_bindings(
        {0: (f"({head} rel:nice $x)", ("$x",))},
        atoms_path=admitted,
        petta_root=petta_root,
        swipl="swipl",
        timeout_seconds=120,
    )
    assert not matches.get(0)
    exact = tmp_path / "exact.jsonl"
    exact_count, _ = recover_exact_matches(
        bundle=bundle,
        documents_path=source,
        atoms_path=admitted,
        provenance_path=provenance,
        support_path=support,
        petta_root=petta_root,
        miner_root=miner_root,
        output_path=exact,
        rejected_path=tmp_path / "exact-rejected.jsonl",
        min_support=2,
    )
    records = tmp_path / "records.jsonl"
    assert build_pattern_records(
        bundle=bundle,
        documents_path=source,
        atoms_path=admitted,
        provenance_path=provenance,
        exact_path=exact,
        output_path=records,
        rejected_path=tmp_path / "record-rejected.jsonl",
    ) == (exact_count, 0)
    candidates = [
        PatternRecord.from_canonical_json(line)
        for line in records.read_text(encoding="utf-8").splitlines()
    ]
    contexts = [r for r in candidates if r.expensive_trace.get("rule_contexts")]
    assert contexts
    for record in contexts:
        assert record.semantic_level == "extracted"
        assert record.status == "candidate"
        assert (
            record.expensive_trace["rule_fragment_semantics"]
            == "structural-subgraph-not-executable-implication"
        )
        assert record.expensive_trace["rule_contexts"][0]["rule"]["consequents"]
    # Removing one conclusion atom must not release a shortened rule graph.
    incomplete = tmp_path / "incomplete.metta"
    incomplete.write_text(
        "\n".join(
            line
            for line in atoms.read_text(encoding="utf-8").splitlines()
            if not line.startswith("(RuleConsequentPositive")
        )
        + "\n",
        encoding="utf-8",
    )
    blocked = tmp_path / "blocked-support.jsonl"
    with pytest.raises(AdmissionRejected):
        mine_admitted_support(
            bundle=bundle,
            documents_path=source,
            emitted_atoms_path=incomplete,
            metadata_path=metadata,
            provenance_path=provenance,
            petta_root=petta_root,
            miner_root=miner_root,
            admitted_atoms_path=tmp_path / "blocked.metta",
            quarantine_path=tmp_path / "blocked-rejected.jsonl",
            support_path=blocked,
            min_support=2,
        )
    assert not blocked.exists()
    assert not (tmp_path / "blocked.metta").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "missing_head",
    [
        "ParserProvenance",
        "ParserOntology",
        "PredicateOntology",
        "SourceSpan",
        "Factuality",
        "Alternative",
        "ConditionalOn",
    ],
)
def test_real_petta_document_quarantine_preserves_other_documents(
    tmp_path: Path, missing_head: str
) -> None:
    bundle_data = _bundle().model_dump(mode="json")
    bundle_data["types"].append("Clause")
    bundle = OntologyBundle.model_validate(bundle_data)
    source = tmp_path / "documents.jsonl"
    documents = (
        _document("A", "Mira left.", "mira", "leaving"),
        _document("B", "Nora sang.", "nora", "singing"),
        _document("C", "Tara left.", "tara", "leaving"),
    )
    conditional = documents[1].model_dump(mode="json")
    text = "If rain falls, Nora sings."
    conditional["source_text"] = text
    for entity, word in zip(conditional["entities"], ("Nora", "sings"), strict=True):
        entity["span"] = {
            "start": text.index(word),
            "end": text.index(word) + len(word),
        }
    conditional["entities"].append(
        {
            "id": "rain-clause",
            "type": "Clause",
            "span": {"start": 3, "end": 13},
        }
    )
    for assertion in conditional["assertions"]:
        assertion.update(
            factuality="hypothetical",
            conditional_on="rain-clause",
            alternatives=["The condition may not be satisfied"],
            source_spans=[{"start": 0, "end": len(text)}],
        )
    documents = (
        documents[0],
        SemanticDocument.model_validate(conditional),
        documents[2],
    )
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
    rows = [
        json.loads(line) for line in provenance.read_text(encoding="utf-8").splitlines()
    ]
    damaged_id = next(row["evidence_id"] for row in rows if row["document_id"] == "B")
    meta.write_text(
        "\n".join(
            line
            for line in meta.read_text(encoding="utf-8").splitlines()
            if not line.startswith(f"({missing_head} {damaged_id} ")
        )
        + "\n",
        encoding="utf-8",
    )
    petta, miner = _runtime_root("PETTA_ROOT"), _runtime_root("MINER_ROOT")
    admitted, support = tmp_path / "admitted.metta", tmp_path / "support.jsonl"
    run = mine_admitted_support(
        bundle=bundle,
        documents_path=source,
        emitted_atoms_path=atoms,
        metadata_path=meta,
        provenance_path=provenance,
        petta_root=petta,
        miner_root=miner,
        admitted_atoms_path=admitted,
        quarantine_path=tmp_path / "quarantine.jsonl",
        support_path=support,
        min_support=2,
    )
    assert (
        run.admission.accepted_documents,
        run.admission.rejected_documents,
        run.admission.facts,
    ) == (2, 1, 4)
    assert run.admission.admission_path is not None
    manifest = AdmissionManifest.model_validate_json(
        run.admission.admission_path.read_text(encoding="utf-8")
    )
    assert {d.document_id: d.accepted for d in manifest.decisions} == {
        "A": True,
        "B": False,
        "C": True,
    }
    assert ":B:" not in admitted.read_text(encoding="utf-8")
    exact = tmp_path / "exact.jsonl"
    assert recover_exact_matches(
        bundle=bundle,
        documents_path=source,
        atoms_path=admitted,
        provenance_path=provenance,
        support_path=support,
        petta_root=petta,
        miner_root=miner,
        output_path=exact,
        rejected_path=tmp_path / "exact-rejected.jsonl",
        admission_path=run.admission.admission_path,
        min_support=2,
    ) == (1, 2)
    patterns = tmp_path / "patterns.jsonl"
    assert build_pattern_records(
        bundle=bundle,
        documents_path=source,
        atoms_path=admitted,
        provenance_path=provenance,
        exact_path=exact,
        output_path=patterns,
        rejected_path=tmp_path / "patterns-rejected.jsonl",
        admission_path=run.admission.admission_path,
    ) == (1, 0)
    record = PatternRecord.from_canonical_json(
        patterns.read_text(encoding="utf-8").strip()
    )
    assert record.provenance.source_document_ids == ("A", "C")
    with pytest.raises(ValueError, match="disagrees with validated inputs"):
        recover_exact_matches(
            bundle=bundle,
            documents_path=source,
            atoms_path=admitted,
            provenance_path=provenance,
            support_path=support,
            petta_root=petta,
            miner_root=miner,
            output_path=tmp_path / "missing-selection.jsonl",
            rejected_path=tmp_path / "missing-selection-rejected.jsonl",
        )


@pytest.mark.parametrize("mutation", ["arity", "type"])
def test_real_petta_signature_authority_is_not_supplied_metadata(
    tmp_path: Path, mutation: str
) -> None:
    bundle = _bundle()
    source = tmp_path / "documents.jsonl"
    source.write_text(
        _document("A", "Mira left.", "mira", "leaving").canonical_json() + "\n",
        encoding="utf-8",
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
    payload = next(
        json.loads(line)
        for line in provenance.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["assertion"]["predicate"] == "Agent"
    )
    if mutation == "arity":
        payload["assertion"]["arguments"] = payload["assertion"]["arguments"][:1]
        payload["assertion"]["argument_types"] = payload["assertion"]["argument_types"][
            :1
        ]
        payload["atom"] = " ".join(payload["atom"].split()[:2]) + ")"
    else:
        payload["assertion"]["argument_types"] = ["Entity", "Event"]
    entry = _entry(line_number=1, raw_line=json.dumps(payload), bundle=bundle)
    # Forge every requested metadata item, including misleading AllowedType
    # declarations. Only the separately compiled bundle must determine validity.
    atoms.write_text(entry.fact + "\n", encoding="utf-8")
    meta.write_text(
        "\n".join(r[6:-1] for r in entry.requirements if r.startswith("(meta ")) + "\n",
        encoding="utf-8",
    )
    assert _petta_checks(
        [entry],
        bundle=bundle,
        atoms_path=atoms,
        metadata_path=meta,
        petta_root=_runtime_root("PETTA_ROOT"),
        swipl="swipl",
        timeout_seconds=120,
        temporary_parent=tmp_path,
    ) == (set(), {0})


def test_real_petta_provisional_patterns_reach_records(tmp_path: Path) -> None:
    """Protect the complete provisional path in the pinned runtime CI job."""
    bundle = _bundle()
    source = tmp_path / "source.jsonl"
    documents = []
    for document in (
        _document("doc-1", "Mira sang.", "mira", "singing"),
        _document("doc-2", "Nora sang.", "nora", "singing"),
    ):
        value = document.model_dump(mode="json")
        value["assertions"].append(
            {
                "predicate": "ProvisionalRelation",
                "provisional_relation_id": "sing",
                "arguments": ["singing", document.entities[0].id],
                "argument_types": ["Event", "Entity"],
                "status": "extracted-hypothesis",
                "factuality": "asserted",
                "confidence": 0.9,
                "source_spans": [{"start": 0, "end": 9}],
            }
        )
        documents.append(SemanticDocument.model_validate(value))
    source.write_text(
        "".join(document.canonical_json() + "\n" for document in documents),
        encoding="utf-8",
    )
    atoms, metadata, provenance = (
        tmp_path / name
        for name in ("facts.metta", "metadata.metta", "provenance.jsonl")
    )
    emit_petta(
        bundle=bundle,
        input_path=source,
        atoms_path=atoms,
        metadata_path=metadata,
        provenance_path=provenance,
    )
    petta_root, miner_root = _runtime_root("PETTA_ROOT"), _runtime_root("MINER_ROOT")
    admitted, support = tmp_path / "admitted.metta", tmp_path / "support.jsonl"
    run = mine_admitted_support(
        bundle=bundle,
        documents_path=source,
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
    assert (run.admission.accepted, run.admission.rejected) == (6, 0)
    exact = tmp_path / "exact.jsonl"
    exact_count, _ = recover_exact_matches(
        bundle=bundle,
        documents_path=source,
        atoms_path=admitted,
        provenance_path=provenance,
        support_path=support,
        petta_root=petta_root,
        miner_root=miner_root,
        output_path=exact,
        rejected_path=tmp_path / "exact-rejected.jsonl",
        min_support=2,
    )
    records = tmp_path / "patterns.jsonl"
    assert build_pattern_records(
        bundle=bundle,
        documents_path=source,
        atoms_path=admitted,
        provenance_path=provenance,
        exact_path=exact,
        output_path=records,
        rejected_path=tmp_path / "patterns-rejected.jsonl",
    ) == (exact_count, 0)
    provisional = [
        PatternRecord.from_canonical_json(line)
        for line in records.read_text(encoding="utf-8").splitlines()
        if "ProvisionalRelation" in json.loads(line)["canonical_term"]
    ]
    assert provisional
    for record in provisional:
        assert record.status == "candidate"
        assert record.semantic_level == "extracted"
        assert (
            "petta-runtime-fixture@1:ProvisionalRelation" in record.ontology_groundings
        )
        assert record.expensive_trace["provisional_relations"]
        for clause in record.expensive_trace["provisional_relations"]:
            argument = clause["relation_argument"]
            if argument.startswith("$"):
                assert record.type_constraints[argument] == "ProvisionalRelationId"
            else:
                assert argument == "rel:sing"
        assert record.expensive_endpoint["observed_support"] >= 2


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
        documents_path=accepted,
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
    assert rows[0]["qualifying_support"] == 2
    assert rows[0]["excluded_bindings"] == []
    assert rows[0]["support_policy"] == "distinct-ground-facts-v1"
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

    patterns = tmp_path / "patterns.jsonl"
    patterns_rejected = tmp_path / "patterns-rejected.jsonl"
    counts = build_pattern_records(
        bundle=bundle,
        documents_path=accepted,
        atoms_path=admitted,
        provenance_path=provenance,
        exact_path=exact,
        output_path=patterns,
        rejected_path=patterns_rejected,
    )
    assert counts == (1, 0)
    record = PatternRecord.from_canonical_json(
        patterns.read_text(encoding="utf-8").strip()
    )
    assert record.contract_version == "pattern-record-v1"
    assert record.canonical_term == "(, (Agent $v0 $v1) (Event $v0))"
    assert record.interface_variables == ("$v0", "$v1")
    assert record.type_constraints == {"$v0": "Event", "$v1": "Entity"}
    assert record.ontology_groundings == ("upper-v1:Agent", "upper-v1:Event")
    assert record.provenance.source_document_ids == ("doc-1", "doc-2")
    assert record.provenance.miner_version == run.mining.miner_revision
    assert record.provenance.ontology_versions == ("upper-v1",)
    assert {
        (span.document_id, span.start, span.end) for span in record.source_spans
    } == {
        ("doc-1", 0, 9),
        ("doc-1", 5, 9),
        ("doc-2", 0, 9),
        ("doc-2", 5, 9),
    }
    assert record.cheap_features == {"clause_count": 2, "variable_count": 2}
    assert record.expensive_endpoint == {"observed_support": 2}
    assert record.expensive_trace is not None
    assert record.expensive_trace["reported_support"] == 2
    assert record.expensive_trace["excluded_binding_count"] == 0
    assert record.expensive_trace["petta_revision"] == run.mining.petta_revision
    assert record.expensive_trace["graph_sha256"] == run.mining.graph_sha256
    assert record.status == "candidate"

    repeated_patterns = tmp_path / "patterns-repeat.jsonl"
    assert build_pattern_records(
        bundle=bundle,
        documents_path=accepted,
        atoms_path=admitted,
        provenance_path=provenance,
        exact_path=exact,
        output_path=repeated_patterns,
        rejected_path=tmp_path / "patterns-repeat-rejected.jsonl",
    ) == (1, 0)
    assert repeated_patterns.read_bytes() == patterns.read_bytes()

    missing_metadata = tmp_path / "missing-metadata.metta"
    missing_metadata.write_text("", encoding="utf-8")
    blocked_support = tmp_path / "blocked-support.jsonl"
    blocked_graph = tmp_path / "blocked-graph.metta"
    with pytest.raises(AdmissionRejected, match="mining did not start"):
        mine_admitted_support(
            bundle=bundle,
            documents_path=accepted,
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
