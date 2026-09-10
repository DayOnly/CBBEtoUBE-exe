"""#phase1-nipple-map: the copy path's conform gets the same nipple map and
margin the body-swap conform passes -- opt-in, off by default, and a no-op
at defaults by construction (empty kwargs)."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from src import gui_settings as gs
from src import nif_convert as nc
from tests import _converter_sources as _cs  # entry source with the lifted loop spliced back


def test_default_off_and_gui_row_agrees():
    assert nc.PHASE1_NIPPLE_MAP is False
    row = gs.by_key()["phase1_nipple_map"]
    assert row.env == "CBBE2UBE_PHASE1_NIPPLE_MAP" and row.invert is False
    assert row.default is False


def _conform_calls():
    src = Path(inspect.getfile(nc)).read_text(encoding="utf8")
    tree = ast.parse(src)
    out = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "conform_to_source_standoff"):
            kw = {k.arg for k in n.keywords if k.arg}
            splat = [k for k in n.keywords if k.arg is None]
            out.append((n.lineno, kw, [ast.unparse(s.value) for s in splat]))
    return sorted(out)


def test_both_copy_path_sites_take_the_map_and_phase2_is_untouched():
    calls = _conform_calls()
    assert len(calls) == 3, calls
    copy_sites = [c for c in calls if "_nip_kw" in c[2]]
    swap_sites = [c for c in calls if "ube_body_nipple" in c[1]]
    assert len(copy_sites) == 2, "both copy-path conform sites must splat _nip_kw"
    assert len(swap_sites) == 1 and not swap_sites[0][2], (
        "the body-swap site passes the map directly and must not change")


def test_kwargs_are_empty_at_defaults():
    """The flag off => `_nip_kw` stays `{}` => the call is the old call."""
    src = _cs.orchestrator_source(nc.convert_nif)
    assert "_nip_kw: dict = {}" in src
    assert "if PHASE1_NIPPLE_MAP and ube_base_for_reskin is not None:" in src
