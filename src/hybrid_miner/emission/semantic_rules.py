"""Declarative, non-executable graph encoding of source-extracted rules.

All graph operands are ground symbols, including scoped rule variables. Rule
premises/conclusions never become standalone assertions or MeTTa rewrites.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hybrid_miner.contracts.semantic_document import (
    ParserProvenance,
    RuleLiteral,
    SemanticDocument,
    SemanticRule,
    SourceSpan,
)
from hybrid_miner.emission import symbols
from hybrid_miner.ontology import OntologyBundle

RULE_GRAPH_VERSION = "semantic-rule-v1"
RULE_HEADS = (
    "ExtractedRule",
    "RuleAntecedentPositive",
    "RuleAntecedentNegative",
    "RuleConsequentPositive",
    "RuleConsequentNegative",
)


def rule_symbol(corpus_id: str, document_id: str, rule_id: str) -> str:
    return ":".join(
        ("rule", *(symbols.component(v) for v in (corpus_id, document_id, rule_id)))
    )


def rule_evidence_symbol(
    corpus_id: str, document_id: str, rule_index: int, fact_index: int
) -> str:
    return ":".join(
        (
            "rev",
            symbols.component(corpus_id),
            symbols.component(document_id),
            str(rule_index),
            str(fact_index),
        )
    )


def rule_part(rule: SemanticRule, fact_index: int) -> tuple[str, RuleLiteral | None]:
    if fact_index == 0:
        return "ExtractedRule", None
    index = fact_index - 1
    if 0 <= index < len(rule.antecedents):
        side, literal = "Antecedent", rule.antecedents[index]
    else:
        index -= len(rule.antecedents)
        if not 0 <= index < len(rule.consequents):
            raise ValueError("rule graph fact index is out of range")
        side, literal = "Consequent", rule.consequents[index]
    return f"Rule{side}{literal.polarity.title()}", literal


def rule_operand_types(rule: SemanticRule, fact_index: int) -> tuple[str, ...]:
    _, literal = rule_part(rule, fact_index)
    if literal is None:
        return ("RuleId", "RuleQuantifier")
    variables = {variable.id for variable in rule.variables}
    return (
        "RuleId",
        "RulePredicateId",
        *(
            f"RuleVariable[{kind}]" if argument in variables else kind
            for argument, kind in zip(
                literal.arguments, literal.argument_types, strict=True
            )
        ),
    )


def rule_predicate_view(token: str, bundle: OntologyBundle) -> str:
    """Display predicate identity without discarding its qualified trace."""
    if token.startswith("$"):
        return token
    known = {
        f"pred:{symbols.predicate_symbol(p.name, p.ontology_version)}": (
            f"pred:{symbols.component(p.name)}"
        )
        for p in bundle.predicates
    }
    if token in known:
        return known[token]
    prefix = (
        f"prov:{symbols.component(bundle.bundle_id)}:"
        f"{symbols.component(bundle.bundle_version)}:"
    )
    if token.startswith(prefix) and token[len(prefix) :]:
        return "prov:" + token[len(prefix) :]
    raise ValueError("rule pattern has an unknown predicate identity")


def rule_source(
    rule: SemanticRule, fact_index: int
) -> tuple[tuple[SourceSpan, ...], float]:
    _, literal = rule_part(rule, fact_index)
    if literal is None:
        return rule.source_spans, min(
            a.confidence for a in (*rule.antecedents, *rule.consequents)
        )
    return literal.source_spans, literal.confidence


@dataclass(frozen=True, slots=True)
class RuleFact:
    atom: str
    evidence: dict[str, Any]
    metadata: tuple[str, ...]


def rule_graph(
    *,
    corpus_id: str,
    document_id: str,
    parser: ParserProvenance,
    rule: SemanticRule,
    rule_index: int,
    entity_types: dict[str, str],
    bundle: OntologyBundle,
) -> tuple[RuleFact, ...]:
    """Build the same rule graph for emission, admission and evidence checks."""
    signatures = {item.name: item for item in bundle.predicates}
    scope = rule_symbol(corpus_id, document_id, rule.id)
    declared = {variable.id: variable.type for variable in rule.variables}
    ordered = dict.fromkeys(
        argument
        for literal in (*rule.antecedents, *rule.consequents)
        for argument in literal.arguments
        if argument in declared
    )
    variables = {
        name: f"rvar:{scope[5:]}:v{index}" for index, name in enumerate(ordered)
    }
    variable_metadata = tuple(
        f"(RuleVariable {scope} {variables[name]} {symbols.component(declared[name])})"
        for name in ordered
    )
    result = []
    for index in range(1 + len(rule.antecedents) + len(rule.consequents)):
        name, literal = rule_part(rule, index)
        head = symbols.predicate_symbol(name, RULE_GRAPH_VERSION)
        operands = [scope]
        declarations = []
        if literal is None:
            operands.append(rule.quantifier)
        else:
            if literal.predicate == "ProvisionalRelation":
                assert literal.provisional_relation_id is not None
                relation = symbols.component(literal.provisional_relation_id)
                identity = (
                    f"prov:{symbols.component(bundle.bundle_id)}:"
                    f"{symbols.component(bundle.bundle_version)}:{relation}"
                )
                declarations.append(f"(RuleProvisionalPredicate {identity} {relation})")
            else:
                signature = signatures[literal.predicate]
                predicate_id = symbols.predicate_symbol(
                    signature.name, signature.ontology_version
                )
                identity = f"pred:{predicate_id}"
                declarations.append(
                    f"(RuleCatalogPredicate {identity} "
                    f"{symbols.component(signature.name)} "
                    f"{symbols.component(signature.ontology_version)})"
                )
            operands.append(identity)
            for argument, kind in zip(
                literal.arguments, literal.argument_types, strict=True
            ):
                if argument in declared:
                    if declared[argument] != kind:
                        raise ValueError("rule variable type differs from literal")
                    operands.append(variables[argument])
                else:
                    if entity_types.get(argument) != kind:
                        raise ValueError("rule entity type differs from literal")
                    entity = symbols.entity_symbol(corpus_id, document_id, argument)
                    operands.append(entity)
                    declarations.append(
                        f"(EntityType {entity} {symbols.component(kind)})"
                    )
        atom = f"({head} {' '.join(operands)})"
        evidence_id = rule_evidence_symbol(corpus_id, document_id, rule_index, index)
        spans, confidence = rule_source(rule, index)
        metadata = [
            f"(RuleEvidence {evidence_id} {atom} {rule.status} {repr(confidence)})",
            f"(RuleScope {evidence_id} {scope} {rule.quantifier})",
            f"(RuleGraphVersion {evidence_id} {RULE_GRAPH_VERSION})",
            f"(ParserProvenance {evidence_id} {symbols.component(parser.model)} "
            f"{symbols.component(parser.parser_version)} "
            f"{symbols.component(parser.prompt_version)})",
            *variable_metadata,
            *declarations,
        ]
        metadata.extend(
            f"(SourceSpan {evidence_id} {span.start} {span.end})" for span in spans
        )
        if literal is not None:
            metadata.append(f"(RuleLiteralPolarity {evidence_id} {literal.polarity})")
            metadata.extend(
                f"(Alternative {evidence_id} {symbols.component(a)})"
                for a in literal.alternatives
            )
        evidence = {
            "kind": "rule",
            "atom": atom,
            "rule_index": rule_index,
            "fact_index": index,
            "evidence_id": evidence_id,
            "bundle_id": bundle.bundle_id,
            "bundle_version": bundle.bundle_version,
            "corpus_id": corpus_id,
            "document_id": document_id,
            "ontology_version": RULE_GRAPH_VERSION,
            "parser": parser.model_dump(mode="json"),
            "rule": rule.model_dump(mode="json"),
            "entity_types": entity_types,
        }
        result.append(RuleFact(atom, evidence, tuple(metadata)))
    return tuple(result)


def document_rule_graph(
    document: SemanticDocument, bundle: OntologyBundle, index: int
) -> tuple[RuleFact, ...]:
    return rule_graph(
        corpus_id=document.corpus_id,
        document_id=document.document_id,
        parser=document.parser,
        rule=document.rules[index],
        rule_index=index,
        entity_types={entity.id: entity.type for entity in document.entities},
        bundle=bundle,
    )
