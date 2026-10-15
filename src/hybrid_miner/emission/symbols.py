"""Stable PeTTa symbol encoding shared by emission and evidence recovery."""

from __future__ import annotations

from urllib.parse import quote


def component(value: str) -> str:
    """Escape PeTTa delimiters without obscuring ordinary identifiers."""

    return quote(value, safe="-._~")


def entity_symbol(corpus_id: str, document_id: str, entity_id: str) -> str:
    return ":".join(
        ("ent", component(corpus_id), component(document_id), component(entity_id))
    )


def predicate_symbol(predicate: str, ontology_version: str) -> str:
    return f"{component(predicate)}@{component(ontology_version)}"


def evidence_symbol(corpus_id: str, document_id: str, index: int) -> str:
    return ":".join(("ev", component(corpus_id), component(document_id), str(index)))
