"""Bound exact-query subprocess output without certifying partial results."""

import subprocess
import sys

import pytest

from hybrid_miner.mining import exact_matches


def _collect(tmp_path, source, **limits):
    return exact_matches._collect_bindings(
        [sys.executable, "-u", "-c", source],
        {0: ("(Tagged $v0)", ("$v0",))},
        cwd=str(tmp_path),
        timeout_seconds=limits.get("timeout_seconds", 10),
        max_bindings=limits.get("max_bindings", 2),
        max_output_bytes=limits.get("max_output_bytes", 1024),
    )


def test_exact_query_accepts_complete_output_at_binding_limit(tmp_path):
    assert _collect(
        tmp_path,
        "print('runtime diagnostic'); "
        "print('(CandidateBinding 0 a)'); print('(CandidateBinding 0 b)')",
    ) == {0: [("a",), ("b",)]}
    assert _collect(tmp_path, "pass") == {}


def test_exact_query_accepts_exact_byte_limit(tmp_path):
    row = b"(CandidateBinding 0 a)\n"
    assert _collect(
        tmp_path,
        f"import sys; sys.stdout.buffer.write({row!r})",
        max_output_bytes=len(row),
    ) == {0: [("a",)]}


@pytest.mark.parametrize(
    ("source", "limits", "message"),
    [
        (
            "print('(CandidateBinding 0 a)'); print('(CandidateBinding 0 b)'); "
            "print('(CandidateBinding 0 c)'); import time; time.sleep(30)",
            {},
            "exceeds 2 exact bindings",
        ),
        (
            "import sys, time; sys.stdout.write('x' * 65); "
            "sys.stdout.flush(); time.sleep(30)",
            {"max_output_bytes": 64},
            "exceeds 64 output bytes",
        ),
        (
            "import sys, time; sys.stderr.write('x' * 1048577); "
            "sys.stderr.flush(); time.sleep(30)",
            {},
            "exceeds 1048576 stderr bytes",
        ),
        ("print('(CandidateBinding 1 a)')", {}, "unexpected PeTTa binding"),
        ("print('(CandidateBinding x a)')", {}, "invalid PeTTa binding"),
        ("print('(CandidateBinding 0 (a))')", {}, "invalid PeTTa binding"),
    ],
)
def test_exact_query_discards_partial_results_on_failure(
    tmp_path, source, limits, message
):
    with pytest.raises(ValueError, match=message):
        _collect(tmp_path, source, **limits)


def test_exact_query_timeout_after_partial_output(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        _collect(
            tmp_path,
            "print('(CandidateBinding 0 a)'); import time; time.sleep(30)",
            timeout_seconds=1,
        )


def test_exact_query_failure_retains_bounded_stderr_diagnostic(tmp_path):
    with pytest.raises(RuntimeError, match="exited 3: runtime failed"):
        _collect(
            tmp_path,
            "import sys; print('runtime failed', file=sys.stderr); sys.exit(3)",
        )
