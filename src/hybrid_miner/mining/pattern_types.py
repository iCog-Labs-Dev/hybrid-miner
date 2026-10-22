"""Type constraints for one binding, kept distinct from cross-binding variation."""

from __future__ import annotations

from hybrid_miner.contracts.semantic_document import SemanticRule
from hybrid_miner.emission.semantic_rules import rule_operand_types, rule_part


def constrain_variables(
    operands: tuple[str, ...],
    concrete: tuple[str, ...],
    permitted: tuple[frozenset[str], ...],
    *,
    binding_types: dict[str, str],
    binding_allowed: dict[str, set[str]],
) -> None:
    """Intersect positions within a binding; never union incompatible slots."""
    for operand, kind, allowed in zip(operands, concrete, permitted, strict=True):
        if kind not in allowed:
            raise ValueError(f"operand {operand} type {kind!r} violates its signature")
        if not operand.startswith("$"):
            continue
        if binding_types.setdefault(operand, kind) != kind:
            raise ValueError(
                f"variable {operand} has conflicting types within a binding"
            )
        binding_allowed.setdefault(operand, set(allowed)).intersection_update(allowed)
        if not binding_allowed[operand]:
            raise ValueError(f"variable {operand} has incompatible predicate positions")


def rule_type_constraints(
    rule: SemanticRule,
    fact_index: int,
    signatures: dict[str, tuple[frozenset[str], ...]],
) -> tuple[frozenset[str], ...]:
    """Keep structural rule sorts and scoped-variable sorts separate from entities."""
    concrete = rule_operand_types(rule, fact_index)
    _, literal = rule_part(rule, fact_index)
    if literal is None or literal.predicate == "ProvisionalRelation":
        return tuple(frozenset((kind,)) for kind in concrete)
    variables = {variable.id for variable in rule.variables}
    allowed = signatures[literal.predicate]
    return (
        frozenset(("RuleId",)),
        frozenset(("RulePredicateId",)),
        *(
            (
                frozenset(f"RuleVariable[{kind}]" for kind in options)
                if argument in variables
                else options
            )
            for argument, options in zip(literal.arguments, allowed, strict=True)
        ),
    )
