"""A DIAGNOSTIC flag must not disable the golden regression harness.

`golden_output.check` refuses when the recorded flag set differs from the
baseline's -- correct, because output is then not comparable. But the refusal
counts a flag that CANNOT change output, and two such flags are persisted in
the Windows USER scope, so they enter every new shell. Measured 2026-09-20: the
baseline recorded `{}`, any run since records those two, and `check` returned 2
before comparing a single vertex.

That is the failure the function's own comment already describes for `GOLDEN_*`
-- "a control that cannot be exercised is not a control" -- and three refusals
in a row look exactly like three identical clean runs.
"""
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))


@pytest.fixture(scope="module")
def go():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "golden_output_under_test", _REPO / "scripts" / "golden_output.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:           # the module guards its own CLI
        pass
    return mod


# Each is output-neutral: they pin a log path, or print a traceback on an
# already-failed path, or read the NIF after it is saved.
DIAGNOSTIC = ("CBBE2UBE_DEBUG_GLOW_CTRL", "CBBE2UBE_GLOW_LOG",
              "CBBE2UBE_DEBUG_FINALIZE", "CBBE2UBE_RUN_LOG",
              "CBBE2UBE_STANDOFF_LOG")


@pytest.mark.parametrize("var", DIAGNOSTIC)
def test_a_diagnostic_flag_does_not_enter_the_recorded_set(go, monkeypatch, var):
    monkeypatch.setenv(var, "1")
    assert var not in go._flags(), (
        f"{var} cannot change converter output, so recording it only makes "
        f"`check` refuse -- which reads like a clean run, not a broken one")


def test_a_flag_that_changes_output_is_still_recorded(go, monkeypatch):
    """THE OTHER DIRECTION. Skipping too much makes the baseline blind, which
    is worse than making it refuse -- assert the skip list did not widen into
    the flags that actually move vertices."""
    for var in ("CBBE2UBE_NO_PHASE2_CONFORM", "CBBE2UBE_TARGET_BODY",
                "CBBE2UBE_NO_COVERED_SKIN_TARGET"):
        monkeypatch.setenv(var, "1")
        assert var in go._flags(), f"{var} CAN change output and must be recorded"


def test_the_persisted_pair_no_longer_blocks_a_comparison(go, monkeypatch):
    """The measured case, end to end: a baseline captured without them stays
    comparable against a shell that has them."""
    for k in list(os.environ):
        if k.startswith("CBBE2UBE_"):
            monkeypatch.delenv(k, raising=False)
    baseline = go._flags()
    monkeypatch.setenv("CBBE2UBE_DEBUG_GLOW_CTRL", "1")
    monkeypatch.setenv("CBBE2UBE_GLOW_LOG", r"D:\somewhere\glowdebug.log")
    assert go._flags() == baseline
