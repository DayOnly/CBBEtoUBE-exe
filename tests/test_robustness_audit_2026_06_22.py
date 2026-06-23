# CBBEtoUBE - CBBE/3BA to UBE armor converter
# Copyright (C) 2026 DayOnly
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Regression tests for the 2026-06-22 robustness/failure-handling audit fixes.

Each test locks in an escalation/surfacing decision that the audit found missing,
so a future change can't silently revert it back to a swallowed failure / exit-0
broken build. See ROBUSTNESS_AUDIT_2026-06-22.md for the findings these cover."""
from argparse import Namespace
from pathlib import Path


# --- M2: unmappable-master-ref is now CTD-class (fails the build) -----------

def test_unmappable_master_ref_is_ctd_class():
    # validate_patch documents unmappable-master-ref as a silent FormID misroute /
    # startup crash, yet postflight classified it as a soft warning (exit 0). It
    # must be in the CTD-prefix set so a hit on the final Combined fails the build.
    from src.ube_patcher import _POSTFLIGHT_CTD_PREFIXES
    assert "unmappable-master-ref" in _POSTFLIGHT_CTD_PREFIXES


def test_postflight_routes_unmappable_master_ref_to_ctd(tmp_path, monkeypatch):
    # A validate_patch warning with the unmappable-master-ref prefix must land in
    # the postflight "ctd" bucket, not "soft".
    import struct
    from src import ube_patcher
    from src.esp import ESP, TES4Header, Group, Record, encode_subrecord, \
        encode_zstring

    arma = Record(sig=b"ARMA", flags=0, formid=0x01000800, timestamp_vc=0,
                  version_unk=0x2C,
                  payload=(encode_subrecord(b"EDID", encode_zstring("XA"))
                           + encode_subrecord(b"RNAM", struct.pack("<I", 0x19))))
    combined = tmp_path / "Combined.esp"
    ESP(header=TES4Header(masters=["Skyrim.esm"]),
        groups=[Group(label=b"ARMA", records=[arma])]).save(combined)

    # Force validate_patch to report an unmappable-master-ref so we exercise the
    # postflight classification (not the detection, which needs real masters).
    monkeypatch.setattr(ube_patcher, "validate_patch", lambda *a, **k: [
        "unmappable-master-ref: 1 master(s) ... (silent FormID misroute)"])
    pf = ube_patcher.postflight_validate_combined(combined)
    assert any("unmappable-master-ref" in w for _, w in pf["ctd"]), pf
    assert not pf["soft"], pf


# --- M3: the standalone `merge` subcommand now runs postflight + can fail ----

def _merge_args(tmp_path):
    a = tmp_path / "a.esp"
    b = tmp_path / "b.esp"
    a.write_bytes(b"x")            # content irrelevant: merge is stubbed below
    b.write_bytes(b"x")
    return Namespace(
        patches=[str(a), str(b)],
        output=str(tmp_path / "Combined.esp"),
        no_esl_flag=False, author="t", description="d")


def test_cmd_merge_fails_on_postflight_ctd(tmp_path, monkeypatch):
    # A CTD-class finding on the merged output must make `merge` return non-zero,
    # not the old unconditional return 0.
    from src import auto_convert, ube_patcher
    monkeypatch.setattr(ube_patcher, "merge_patches_split",
                        lambda *a, **k: {"output": str(tmp_path / "Combined.esp")})
    monkeypatch.setattr(ube_patcher, "postflight_validate_combined",
                        lambda *a, **k: {"ctd": [("Combined.esp",
                                                  "formid-zero: ...")],
                                         "soft": [], "pieces": ["Combined.esp"]})
    rc = auto_convert._cmd_merge(_merge_args(tmp_path))
    assert rc == 2


def test_cmd_merge_ok_when_postflight_clean(tmp_path, monkeypatch):
    # No CTD findings -> the happy path still returns 0.
    from src import auto_convert, ube_patcher
    monkeypatch.setattr(ube_patcher, "merge_patches_split",
                        lambda *a, **k: {"output": str(tmp_path / "Combined.esp")})
    monkeypatch.setattr(ube_patcher, "postflight_validate_combined",
                        lambda *a, **k: {"ctd": [], "soft": [],
                                         "pieces": ["Combined.esp"]})
    rc = auto_convert._cmd_merge(_merge_args(tmp_path))
    assert rc == 0


# --- H1: equip-CTD per-NIF invariants are failures, not warnings -------------

def test_nif_invariant_issues_still_flags_ctd_classes():
    # The escalation in _cmd_convert relies on _nif_invariant_issues returning a
    # non-empty list for the CTD classes; lock that contract here (the routing to
    # overall_failures lives in the big CLI fn and is covered by manual review).
    from src.auto_convert import _nif_invariant_issues

    class _S:
        def __init__(self, name, nverts, nbones, nparts):
            self.name = name
            self.verts = [0] * nverts
            self.bone_names = ["b"] * nbones
            self.partitions = [object()] * nparts

    over = _nif_invariant_issues("x_1.nif", [_S("Over", 100, 90, 1)], cap=78)
    zero = _nif_invariant_issues("x_1.nif", [_S("Z", 0, 4, 1)], cap=78)
    assert over and "split failed" in over[0]
    assert zero and "ZERO-vertex" in zero[0]


# --- M4: esp parse hardening (clean errors on malformed input, no infinite loop)

def test_record_parse_rejects_truncated_header():
    # A truncated record header must raise a clean ValueError, not a cryptic
    # struct.error or an `assert` that python -O strips.
    import pytest
    from src.esp import Record
    with pytest.raises(ValueError):
        Record.parse(b"ARMA\x00\x00", 0)        # 6 bytes, header needs 24


def test_group_parse_rejects_non_grup():
    import pytest
    from src.esp import Group
    with pytest.raises(ValueError):
        Group.parse(b"ARMA" + b"\x00" * 24, 0)  # not a GRUP where one is required


def test_esp_roundtrip_still_clean_after_hardening(tmp_path):
    # The happy path must be byte-identical: a saved ESP reloads to equal groups.
    import struct
    from src.esp import ESP, TES4Header, Group, Record, encode_subrecord, \
        encode_zstring
    arma = Record(sig=b"ARMA", flags=0, formid=0x01000800, timestamp_vc=0,
                  version_unk=0x2C,
                  payload=(encode_subrecord(b"EDID", encode_zstring("XA"))
                           + encode_subrecord(b"RNAM", struct.pack("<I", 0x19))))
    p = tmp_path / "x.esp"
    ESP(header=TES4Header(masters=["Skyrim.esm"]),
        groups=[Group(label=b"ARMA", records=[arma])]).save(p)
    re = ESP.load(p)
    assert re.group(b"ARMA") is not None
    assert re.group(b"ARMA").records[0].formid == 0x01000800


# --- H3: self-healing worker pool (recover from a worker PROCESS death) ------

class _FakeFuture:
    def __init__(self, value=None, exc=None):
        self._v, self._e = value, exc

    def result(self):
        if self._e is not None:
            raise self._e
        return self._v


class _FakePool:
    """submit() runs innocents inline and returns a broken future for poison
    items, so _run_isolated's per-item logic is testable without subprocesses."""
    def __init__(self, crash_on):
        self.crash_on = set(crash_on)
        self.shutdown_called = False

    def submit(self, fn, it):
        from concurrent.futures.process import BrokenProcessPool
        if it[0] in self.crash_on:
            return _FakeFuture(exc=BrokenProcessPool("simulated crash"))
        return _FakeFuture(value=fn(it))

    def shutdown(self, wait=True):
        self.shutdown_called = True


def _echo(item):
    from src.nif_convert import ConvertResult
    return ConvertResult(src_path=item[0], dst_path=str(item[0]) + ".out",
                         status="converted (copy)")


def test_nifpool_isolated_drops_only_the_crasher():
    # One poison item among innocents: the crasher is errored (surfaced), every
    # innocent still converts, and the pool is rebuilt exactly once.
    from src.auto_convert import _NifPool
    mgr = _NifPool(2, pool_factory=lambda: _FakePool({"POISON"}))
    results = []
    items = [("ok0",), ("POISON",), ("ok1",), ("ok2",)]
    mgr._run_isolated(items, results.append, _echo)
    by = {r.src_path: r for r in results}
    assert by["ok0"].status.startswith("converted")
    assert by["ok1"].status.startswith("converted")
    assert by["ok2"].status.startswith("converted")
    assert by["POISON"].status == "error" and "died" in by["POISON"].reason
    assert mgr.rebuilds == 1


def test_nifpool_isolated_gives_up_on_systemic_crashes():
    # Every item crashes -> systemic failure. Stop rebuilding after GIVE_UP_AFTER
    # consecutive crashes; the rest are surfaced as 'not attempted' (still errors).
    from src.auto_convert import _NifPool
    crash_all = {f"P{i}" for i in range(7)}
    mgr = _NifPool(2, pool_factory=lambda: _FakePool(crash_all))
    results = []
    items = [(f"P{i}",) for i in range(7)]
    mgr._run_isolated(items, results.append, _echo)
    assert all(r.status == "error" for r in results)
    assert mgr.rebuilds == mgr.GIVE_UP_AFTER
    not_attempted = [r for r in results if "not attempted" in r.reason]
    assert len(not_attempted) == 7 - mgr.GIVE_UP_AFTER


def test_nifpool_real_subprocess_crash_recovery():
    # End-to-end with REAL subprocesses: a worker that os._exit()s (genuine
    # BrokenProcessPool) must not lose the innocents, and the SAME pool must
    # still work afterwards (no cross-mod cascade).
    from src.auto_convert import _NifPool
    from tests._crash_worker import crash_or_echo
    mgr = _NifPool(2)
    try:
        results = []
        items = [("ok0", 1), ("ok1", 1), ("ok2", 1), ("POISON", 1),
                 ("ok3", 1), ("ok4", 1), ("ok5", 1)]
        mgr.run_batch(items, results.append, fn=crash_or_echo)
        by = {r.src_path: r for r in results}
        for n in ("ok0", "ok1", "ok2", "ok3", "ok4", "ok5"):
            assert by[n].status.startswith("converted"), (n, by)
        assert by["POISON"].status == "error"
        assert mgr.rebuilds >= 1
        # The shared pool survives the crash -> a later mod still converts.
        results2 = []
        mgr.run_batch([("after", 1)], results2.append, fn=crash_or_echo)
        assert results2 and results2[0].status.startswith("converted")
    finally:
        mgr.shutdown()
