"""Deterministic candidate syntax for PeTTa support records."""

from __future__ import annotations

import re
from itertools import permutations
from typing import TypeAlias

_TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+')
_MAX_COMMUTATIVE_CLAUSES = 6
Expression: TypeAlias = str | tuple["Expression", ...]


def parse_expression(source: str) -> Expression:
    """Parse one complete PeTTa expression without evaluating it."""

    tokens = _TOKEN.findall(source)
    expression, end = _parse(tokens, 0)
    if end != len(tokens):
        raise ValueError("expected one complete PeTTa expression")
    return expression


def render_expression(expression: Expression) -> str:
    """Render a previously parsed expression deterministically."""

    return _render(expression)


def _parse(tokens: list[str], index: int) -> tuple[Expression, int]:
    if index >= len(tokens):
        raise ValueError("incomplete PeTTa expression")
    token = tokens[index]
    if token == ")":
        raise ValueError("unexpected closing parenthesis")
    if token != "(":
        return token, index + 1
    elements: list[Expression] = []
    index += 1
    while index < len(tokens) and tokens[index] != ")":
        element, index = _parse(tokens, index)
        elements.append(element)
    if index >= len(tokens) or not elements:
        raise ValueError("unclosed or empty PeTTa expression")
    return tuple(elements), index + 1


def _render(expression: Expression) -> str:
    if isinstance(expression, str):
        return expression
    return "(" + " ".join(_render(element) for element in expression) + ")"


def _alpha_normalize(expression: Expression) -> Expression:
    variables: dict[str, str] = {}

    def visit(value: Expression) -> Expression:
        if isinstance(value, tuple):
            return tuple(visit(element) for element in value)
        if value.startswith("$"):
            return variables.setdefault(value, f"$v{len(variables)}")
        return value

    return visit(expression)


def canonicalize_pattern(pattern: str) -> str:
    """Normalize variable names and commutative top-level conjunction order."""

    tokens = _TOKEN.findall(pattern)
    expression, end = _parse(tokens, 0)
    if end != len(tokens) or not isinstance(expression, tuple):
        raise ValueError("pattern must contain one PeTTa expression")
    if expression[0] != ",":
        return _render(_alpha_normalize(expression))
    clauses = expression[1:]
    if not clauses or len(clauses) > _MAX_COMMUTATIVE_CLAUSES:
        raise ValueError("unsupported conjunction size")
    return min(
        _render(_alpha_normalize((",", *ordered))) for ordered in permutations(clauses)
    )
