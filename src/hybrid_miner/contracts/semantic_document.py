"""Validated S1 semantic-document interchange contract."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTRACT_VERSION: Literal["semantic-document-v1"] = "semantic-document-v1"
_IDENTIFIER_RE = re.compile(r"^[^\s]+$")


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _nonempty_text(value: str) -> str:
    if not value.strip():
        raise ValueError("value must contain non-whitespace text")
    return value


def _identifier(value: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError("identifier must be nonempty and contain no whitespace")
    return value


class SourceSpan(_ContractModel):
    """Half-open Unicode code-point offsets into SemanticDocument.source_text."""

    start: int = Field(strict=True, ge=0)
    end: int = Field(strict=True, gt=0)

    @model_validator(mode="after")
    def validate_order(self) -> SourceSpan:
        if self.end <= self.start:
            raise ValueError("source span must satisfy start < end")
        return self


class ParserProvenance(_ContractModel):
    """Identity of the model, prompt, parser, and ontology bundle used."""

    model: str
    parser_version: str
    prompt_version: str
    ontology_versions: tuple[str, ...] = Field(min_length=1)
    provider: str | None = None
    model_revision: str | None = None

    @field_validator("model", "parser_version", "prompt_version")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return _nonempty_text(value)

    @field_validator("provider", "model_revision")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _nonempty_text(value) if value is not None else None

    @field_validator("ontology_versions")
    @classmethod
    def validate_ontology_versions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not version.strip() for version in value):
            raise ValueError("ontology versions must be nonempty")
        if len(set(value)) != len(value):
            raise ValueError("ontology versions must be unique")
        return value


class EntityRecord(_ContractModel):
    """A typed referent; span may be absent for an implicit proposition."""

    id: str
    type: str
    span: SourceSpan | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _identifier(value)

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        return _nonempty_text(value)


class SemanticAssertion(_ContractModel):
    """One ontology-grounded extraction hypothesis with source evidence."""

    predicate: str
    arguments: tuple[str, ...] = Field(min_length=1)
    argument_types: tuple[str, ...] = Field(min_length=1)
    status: Literal["extracted-hypothesis"]
    # Source presentation is distinct from the parser's extraction status.
    # None means it was not classified; it must never imply "asserted".
    factuality: Literal["asserted", "opinion", "speculative", "hypothetical"] | None = (
        None
    )
    # An explicit source condition for this hypothetical assertion. The target
    # must be a declared Clause entity; this is not a derived inference rule.
    conditional_on: str | None = None
    confidence: float = Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)
    source_spans: tuple[SourceSpan, ...] = Field(min_length=1)
    alternatives: tuple[str, ...] = ()
    notes: str | None = None
    provisional_relation_id: str | None = None

    @field_validator("predicate")
    @classmethod
    def validate_predicate(cls, value: str) -> str:
        return _identifier(value)

    @field_validator("arguments")
    @classmethod
    def validate_arguments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for argument in value:
            _identifier(argument)
        return value

    @field_validator("argument_types")
    @classmethod
    def validate_argument_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for argument_type in value:
            _nonempty_text(argument_type)
        return value

    @field_validator("alternatives")
    @classmethod
    def validate_alternatives(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("alternatives must be unique")
        for alternative in value:
            _nonempty_text(alternative)
        return value

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, value: str | None) -> str | None:
        return _nonempty_text(value) if value is not None else None

    @field_validator("provisional_relation_id")
    @classmethod
    def validate_provisional_relation_id(cls, value: str | None) -> str | None:
        return _identifier(value) if value is not None else None

    @field_validator("conditional_on")
    @classmethod
    def validate_conditional_on(cls, value: str | None) -> str | None:
        return _identifier(value) if value is not None else None

    @model_validator(mode="after")
    def validate_structure(self) -> SemanticAssertion:
        if len(self.arguments) != len(self.argument_types):
            raise ValueError("arguments and argument_types must have equal length")
        if not math.isfinite(self.confidence):
            raise ValueError("confidence must be finite")
        if self.conditional_on is not None and self.factuality != "hypothetical":
            raise ValueError("conditional_on requires hypothetical factuality")
        if self.predicate == "ProvisionalRelation":
            if self.provisional_relation_id is None:
                raise ValueError("ProvisionalRelation requires provisional_relation_id")
        elif self.provisional_relation_id is not None:
            raise ValueError(
                "only ProvisionalRelation can carry provisional_relation_id"
            )
        return self


class SemanticDocument(_ContractModel):
    """One source document and its S1 extraction hypotheses."""

    contract_version: Literal["semantic-document-v1"] = CONTRACT_VERSION
    document_id: str
    corpus_id: str
    source_text: str
    parser: ParserProvenance
    entities: tuple[EntityRecord, ...] = ()
    assertions: tuple[SemanticAssertion, ...] = ()

    @field_validator("document_id", "corpus_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _identifier(value)

    @field_validator("source_text")
    @classmethod
    def validate_source_text(cls, value: str) -> str:
        return _nonempty_text(value)

    @model_validator(mode="after")
    def validate_references_and_spans(self) -> SemanticDocument:
        entities = {entity.id: entity for entity in self.entities}
        if len(entities) != len(self.entities):
            raise ValueError("entity ids must be unique within a document")
        source_length = len(self.source_text)
        for entity in self.entities:
            if entity.span is not None and entity.span.end > source_length:
                raise ValueError(f"entity {entity.id!r} span exceeds source text")
        for assertion in self.assertions:
            if assertion.conditional_on is not None:
                condition = entities.get(assertion.conditional_on)
                if (
                    condition is None
                    or condition.type != "Clause"
                    or condition.span is None
                ):
                    raise ValueError(
                        "conditional_on must reference a source-anchored Clause entity"
                    )
            for span in assertion.source_spans:
                if span.end > source_length:
                    raise ValueError("assertion source span exceeds source text")
            for argument_id, argument_type in zip(
                assertion.arguments, assertion.argument_types, strict=True
            ):
                referenced_entity = entities.get(argument_id)
                if referenced_entity is None:
                    raise ValueError(f"unknown assertion argument {argument_id!r}")
                if referenced_entity.type != argument_type:
                    raise ValueError(
                        f"argument {argument_id!r} type {argument_type!r} "
                        f"does not match entity type {referenced_entity.type!r}"
                    )
        return self

    def canonical_json(self) -> str:
        """Serialize identically across processes for auditing and hashing."""

        payload = self.model_dump(mode="json")
        for assertion in payload["assertions"]:
            if assertion["factuality"] is None:
                del assertion["factuality"]
            if assertion["conditional_on"] is None:
                del assertion["conditional_on"]
        return json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def validate_against_signatures(
        self,
        *,
        ontology_versions: tuple[str, ...],
        signatures: Mapping[str, tuple[frozenset[str], ...]],
    ) -> None:
        """Reject undeclared predicates and mismatched ontology signatures.

        The caller supplies signatures from the ontology versions recorded in
        ``parser.ontology_versions``. Full catalog loading is a later module.
        """

        if ontology_versions != self.parser.ontology_versions:
            raise ValueError(
                "ontology signature versions do not match parser provenance"
            )
        for assertion in self.assertions:
            if assertion.predicate == "ProvisionalRelation":
                if assertion.provisional_relation_id in signatures:
                    raise ValueError(
                        "provisional relation duplicates a catalog predicate"
                    )
                continue
            expected = signatures.get(assertion.predicate)
            if expected is None:
                raise ValueError(
                    f"unknown predicate {assertion.predicate!r}; "
                    "use ProvisionalRelation"
                )
            if len(assertion.argument_types) != len(expected) or any(
                observed not in allowed
                for observed, allowed in zip(
                    assertion.argument_types, expected, strict=True
                )
            ):
                raise ValueError(
                    f"predicate {assertion.predicate!r} expects argument types "
                    f"{expected!r}, received {assertion.argument_types!r}"
                )
