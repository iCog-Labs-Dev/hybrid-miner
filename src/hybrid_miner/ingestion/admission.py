"""Versioned selection of complete source documents for extraction mining.

This handoff belongs to the semantic-document adapter, not the raw graph API.
File hashes bind a selection to its artifacts; they are not authentication or
evidence of semantic truth.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hybrid_miner.ontology import OntologyBundle

DocumentKey = tuple[str, str]
Digest = str


def file_digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def bundle_digest(bundle: OntologyBundle) -> str:
    return hashlib.sha256(bundle.canonical_json().encode("utf-8")).hexdigest()


def document_key(payload: dict[str, Any]) -> DocumentKey:
    values = (payload.get("corpus_id"), payload.get("document_id"))
    if any(
        not isinstance(value, str) or not value or any(c.isspace() for c in value)
        for value in values
    ):
        raise ValueError("record needs unambiguous corpus_id and document_id")
    corpus, document = values
    assert isinstance(corpus, str) and isinstance(document, str)
    return corpus, document


def document_records(path: Path) -> dict[DocumentKey, str]:
    records = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line:
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"document line {number} is not an object")
        key = document_key(payload)
        if key in records:
            raise ValueError(f"duplicate document on line {number}: {key}")
        records[key] = line
    return records


class DocumentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    corpus_id: str = Field(min_length=1, pattern=r"^\S+$")
    document_id: str = Field(min_length=1, pattern=r"^\S+$")
    accepted: bool = Field(strict=True)
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def check_reasons(self) -> DocumentDecision:
        if self.accepted == bool(self.reasons) or any(not r for r in self.reasons):
            raise ValueError("only quarantined documents need nonempty reasons")
        return self


class AdmissionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["document-admission-v1"]
    bundle_sha256: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    documents_sha256: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    provenance_sha256: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    graph_sha256: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    decisions: tuple[DocumentDecision, ...]

    @model_validator(mode="after")
    def unique_documents(self) -> AdmissionManifest:
        identities = [(d.corpus_id, d.document_id) for d in self.decisions]
        if len(set(identities)) != len(identities):
            raise ValueError("admission decisions repeat a document")
        return self

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            with path.open("x", encoding="utf-8", newline="\n") as target:
                created = True
                target.write(
                    json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=True)
                )
                target.write("\n")
        except BaseException:
            if created:
                path.unlink(missing_ok=True)
            raise


def excluded_documents(
    admission_path: Path | None,
    *,
    bundle: OntologyBundle,
    documents_path: Path,
    provenance_path: Path,
    atoms_path: Path,
) -> set[DocumentKey]:
    """Verify a handoff before allowing declared document exclusions."""
    if admission_path is None:
        return set()
    manifest = AdmissionManifest.model_validate_json(
        admission_path.read_text(encoding="utf-8")
    )
    expected = (
        bundle_digest(bundle),
        file_digest(documents_path),
        file_digest(provenance_path),
        file_digest(atoms_path),
    )
    actual = (
        manifest.bundle_sha256,
        manifest.documents_sha256,
        manifest.provenance_sha256,
        manifest.graph_sha256,
    )
    if actual != expected:
        raise ValueError("admission manifest belongs to different input artifacts")
    keys = {(d.corpus_id, d.document_id) for d in manifest.decisions}
    if keys != set(document_records(documents_path)):
        raise ValueError("admission decisions do not cover the source documents")
    return {(d.corpus_id, d.document_id) for d in manifest.decisions if not d.accepted}
