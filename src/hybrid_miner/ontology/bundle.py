"""Validated, repository-neutral ontology bundle input.

The bundle records signatures, not ontology rules or production catalog data.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hybrid_miner.contracts.semantic_document import SemanticDocument

ONTOLOGY_BUNDLE_CONTRACT_VERSION: Literal["ontology-bundle-v1"] = "ontology-bundle-v1"
_IDENTIFIER_RE = re.compile(r"^[^\s]+$")


def _identifier(value: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError("identifier must be nonempty and contain no whitespace")
    return value


def _unique_identifiers(values: tuple[str, ...]) -> tuple[str, ...]:
    for value in values:
        _identifier(value)
    if len(set(values)) != len(values):
        raise ValueError("identifiers must be unique")
    return values


class _BundleModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PredicateSignature(_BundleModel):
    """Allowed entity types at each position of a named predicate."""

    name: str
    ontology_version: str
    argument_types: tuple[tuple[str, ...], ...] = Field(min_length=1)

    @field_validator("name", "ontology_version")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        return _identifier(value)

    @field_validator("argument_types")
    @classmethod
    def validate_argument_types(
        cls, value: tuple[tuple[str, ...], ...]
    ) -> tuple[tuple[str, ...], ...]:
        for allowed in value:
            if not allowed:
                raise ValueError("each argument position needs an allowed type")
            _unique_identifiers(allowed)
        return value


class OntologyBundle(_BundleModel):
    """One explicit bundle release and the predicate signatures it permits."""

    contract_version: Literal["ontology-bundle-v1"]
    bundle_id: str
    bundle_version: str
    ontology_versions: tuple[str, ...] = Field(min_length=1)
    types: tuple[str, ...] = Field(min_length=1)
    predicates: tuple[PredicateSignature, ...] = ()

    @field_validator("bundle_id", "bundle_version")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        return _identifier(value)

    @field_validator("ontology_versions", "types")
    @classmethod
    def validate_identifier_list(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_identifiers(value)

    @model_validator(mode="after")
    def validate_catalog(self) -> OntologyBundle:
        known_types = set(self.types)
        known_versions = set(self.ontology_versions)
        names: set[str] = set()
        for predicate in self.predicates:
            if predicate.name == "ProvisionalRelation":
                raise ValueError(
                    "ProvisionalRelation is reserved for uncataloged hypotheses"
                )
            if predicate.name in names:
                raise ValueError(f"duplicate predicate {predicate.name!r}")
            names.add(predicate.name)
            if predicate.ontology_version not in known_versions:
                raise ValueError(
                    f"predicate {predicate.name!r} has undeclared ontology version"
                )
            for allowed in predicate.argument_types:
                unknown = set(allowed) - known_types
                if unknown:
                    raise ValueError(
                        f"predicate {predicate.name!r} uses undeclared types: "
                        f"{sorted(unknown)}"
                    )
        return self

    def signatures(self) -> dict[str, tuple[frozenset[str], ...]]:
        """Compile the shape expected by SemanticDocument signature checks."""

        return {
            predicate.name: tuple(
                frozenset(allowed) for allowed in predicate.argument_types
            )
            for predicate in self.predicates
        }

    def validate_document(self, document: SemanticDocument) -> SemanticDocument:
        """Check a document against this bundle's versions and signatures."""

        known_types = set(self.types)
        for entity in document.entities:
            if entity.type not in known_types:
                raise ValueError(
                    f"entity {entity.id!r} uses undeclared type {entity.type!r}"
                )
        document.validate_against_signatures(
            ontology_versions=self.ontology_versions, signatures=self.signatures()
        )
        return document

    def canonical_json(self) -> str:
        """Serialize the validated bundle as a stable JSON artifact."""

        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def load_json(cls, path: str | Path) -> OntologyBundle:
        """Load and validate a bundle supplied by a caller."""

        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))
