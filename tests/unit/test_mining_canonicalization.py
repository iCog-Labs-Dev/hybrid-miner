"""Canonical forms for candidate patterns returned by PeTTa."""

import pytest

from hybrid_miner.mining.canonicalize_patterns import canonicalize_pattern


def test_alpha_normalizes_variables_and_conjunction_order() -> None:
    first = "(, (Event@upper-v1 $_123) (Agent@upper-v1 $_123 $_456))"
    reordered = "(, (Agent@upper-v1 $_77 $_88) (Event@upper-v1 $_77))"

    assert canonicalize_pattern(first) == canonicalize_pattern(reordered)
    assert canonicalize_pattern(first) == (
        "(, (Agent@upper-v1 $v0 $v1) (Event@upper-v1 $v0))"
    )


def test_preserves_distinct_variable_equalities() -> None:
    shared = "(, (Agent@upper-v1 $_1 $_2) (Agent@upper-v1 $_1 $_3))"
    separate = "(, (Agent@upper-v1 $_1 $_2) (Agent@upper-v1 $_3 $_2))"

    assert canonicalize_pattern(shared) != canonicalize_pattern(separate)


@pytest.mark.parametrize("pattern", ["", "(Event@upper-v1 $x", "Event@upper-v1"])
def test_rejects_incomplete_patterns(pattern: str) -> None:
    with pytest.raises(ValueError):
        canonicalize_pattern(pattern)
