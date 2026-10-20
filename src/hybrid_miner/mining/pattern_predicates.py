"""Predicate shapes shared by exact matching and candidate construction.

A provisional wrapper has a relation-identifier operand before its entity
operands. That operand is not an entity or an approved ontology predicate.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote

from hybrid_miner.emission.semantic_rules import (
    RULE_GRAPH_VERSION,
    RULE_HEADS,
    rule_predicate_view,
)
from hybrid_miner.emission.symbols import component, predicate_symbol
from hybrid_miner.ontology import OntologyBundle

PROVISIONAL_RELATION_TYPE = "ProvisionalRelationId"


@dataclass(frozen=True, slots=True)
class PatternPredicate:
    name: str
    ontology_version: str
    arity: int | None
    rule: bool = False

    @property
    def provisional(self) -> bool:
        return self.name == "ProvisionalRelation"

    def validate_clause(self, clause: tuple[str, ...], bundle: OntologyBundle) -> None:
        if self.rule:
            if self.name == "ExtractedRule" and len(clause) != 3:
                raise ValueError("rule declaration has wrong arity")
            if self.name != "ExtractedRule" and len(clause) < 4:
                raise ValueError(
                    "rule literal needs scope, predicate identity and arguments"
                )
            if self.name != "ExtractedRule":
                rule_predicate_view(clause[2], bundle)
            elif clause[2] != "universal" and not clause[2].startswith("$"):
                raise ValueError("rule declaration has an unsupported quantifier")
            return
        if not self.provisional:
            if len(clause) - 1 != self.arity:
                raise ValueError(f"pattern predicate {self.name!r} has wrong arity")
            return
        if len(clause) < 3:
            raise ValueError("provisional pattern needs a relation id and arguments")
        relation = clause[1]
        if relation.startswith("$"):
            return
        if (
            not relation.startswith("rel:")
            or not relation[4:]
            or component(unquote(relation[4:])) != relation[4:]
        ):
            raise ValueError("provisional pattern has an invalid relation identifier")


def pattern_predicates(bundle: OntologyBundle) -> dict[str, PatternPredicate]:
    """Include the explicit provisional channel, not unlisted bare heads."""
    result = {
        predicate_symbol(item.name, item.ontology_version): PatternPredicate(
            item.name, item.ontology_version, len(item.argument_types)
        )
        for item in bundle.predicates
    }
    version = f"{bundle.bundle_id}@{bundle.bundle_version}"
    result[predicate_symbol("ProvisionalRelation", version)] = PatternPredicate(
        "ProvisionalRelation", version, None
    )
    result.update(
        {
            predicate_symbol(name, RULE_GRAPH_VERSION): PatternPredicate(
                name, RULE_GRAPH_VERSION, None, rule=True
            )
            for name in RULE_HEADS
        }
    )
    return result
