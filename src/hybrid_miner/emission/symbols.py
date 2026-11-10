"""Stable PeTTa symbol encoding shared by emission and evidence recovery."""

from __future__ import annotations

from urllib.parse import quote

NEGATED_PREFIX = "Negated-"
# Catalog predicate names must not start with these (checked in OntologyBundle).
MODALITY_PREFIXES = {
    "none": "",
    "obligation": "Obligation-",
    "permission": "Permission-",
}
FACT_VARIANTS = tuple(
    (polarity, modality)
    for polarity in ("positive", "negative")
    for modality in ("none", "obligation", "permission")
)
RESERVED_PREFIXES = (NEGATED_PREFIX, *(p for p in MODALITY_PREFIXES.values() if p))


def component(value: str) -> str:
    """Escape PeTTa delimiters without obscuring ordinary identifiers."""

    return quote(value, safe="-._~")


def entity_symbol(corpus_id: str, document_id: str, entity_id: str) -> str:
    return ":".join(
        ("ent", component(corpus_id), component(document_id), component(entity_id))
    )


def predicate_symbol(predicate: str, ontology_version: str) -> str:
    return f"{component(predicate)}@{component(ontology_version)}"


def fact_name(
    predicate: str, polarity: str = "positive", modality: str = "none"
) -> str:
    """Encode parser-supplied polarity and modality in their scope order.

    ``must not leave``: Obligation(Not p) -> Obligation-Negated-leave;
    ``may not leave``: Not(Permission p) -> Negated-Permission-leave.
      Unknown modalities raise KeyError.
    """
    negative = polarity == "negative"
    if negative and modality == "permission":
        return NEGATED_PREFIX + MODALITY_PREFIXES[modality] + predicate
    name = NEGATED_PREFIX + predicate if negative else predicate
    return MODALITY_PREFIXES[modality] + name


def fact_head(
    predicate: str,
    ontology_version: str,
    polarity: str = "positive",
    modality: str = "none",
) -> str:
    """Graph head of a ground fact; a negative or modal fact gets its own head."""
    return predicate_symbol(fact_name(predicate, polarity, modality), ontology_version)


def evidence_symbol(corpus_id: str, document_id: str, index: int) -> str:
    return ":".join(("ev", component(corpus_id), component(document_id), str(index)))
