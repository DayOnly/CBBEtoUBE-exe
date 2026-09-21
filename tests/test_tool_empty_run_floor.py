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

"""Tools that reported SUCCESS having examined nothing.

An AST census over the 101 script paths in docs/TOOL_MAP.md found six runnable
tools with no nonzero exit path at all (the sixth, build_body_collider_proxy,
has only an argparse usage guard). Pointed at an empty pack, every one of them
returned 0:

    scan_output_health          rc=0  "=== SCAN DONE ==="
    disable_unconstrained_smp   rc=0  "(dry-run; pass --apply to rename)"
    build_body_collider_proxy   rc=0  "processed 0 NIFs"
    strip_nude_handfeet         rc=0  "need esp path"
    scan_nude_skin_chain        rc=0  "FATAL: no UBE_AllRace.esp found"

The last one prints the word FATAL and exits 0. The first prints an
affirmative all-clear over zero NIFs -- the same shape as the bust_verdict
false clean, where a human read "clean at rest" off a tool that had measured
nine vertices of a garment 42u from the body.

These tests spawn the real tools and assert on the REAL exit code, because the
exit code is the thing that was wrong. Asserting on a sliced-out predicate
would pass while the tool still returned 0.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


def _run(rel_script, *args, env_extra=None, cwd=None):
    """Run a tool as a subprocess; return (rc, combined output)."""
    env = dict(os.environ)
    env.pop("CBBE2UBE_MODS_ROOT", None)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(
        [sys.executable, str(_REPO / rel_script), *[str(a) for a in args]],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=str(cwd or _REPO), timeout=300)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


@pytest.fixture()
def empty_pack(tmp_path):
    """An output-mod layout with no meshes and no ESP -- the wrong-dir case."""
    (tmp_path / "meshes").mkdir()
    return tmp_path


# --------------------------------------------- the stdout-wrapping landmine
def test_wrapping_a_buffer_hands_over_ownership_and_closes_it():
    """THE MECHANISM the ratchet below exists for -- proven, not assumed.

    io.TextIOWrapper takes ownership of the buffer it is given and closes it
    when collected. Applied to `sys.stdout.buffer` under pytest that closes
    the global capture file, and every later test dies in teardown with
    "I/O operation on closed file".

    If CPython ever stops doing this, this test fails and the ratchet's
    reason is gone -- which is the point of pinning the mechanism rather
    than just banning the string.
    """
    import gc
    import io as _io
    buf = _io.BytesIO()
    wrapper = _io.TextIOWrapper(buf, encoding="utf-8")
    del wrapper
    gc.collect()
    assert buf.closed, "the wrapper no longer closes the buffer it was given"


def test_no_tool_wraps_sys_stdout_buffer():
    """RATCHET. Six tools carried this; all six are converted.

    `sys.stdout.reconfigure(...)` mutates the existing stream instead and is
    a no-op on one that does not support it, so there is no reason to
    reintroduce the wrapper. Frozen by absence: the count may go down.
    """
    # mutation_pairs.py is a CATALOGUE of deliberately-broken code: CAC-k arms
    # this very ratchet by carrying the banned idiom as a replacement string.
    # Any source-scanning ratchet collides with it, so it is skipped -- but
    # only after checking it really is the pair catalogue, so the exemption
    # cannot silently widen to a file that merely gets renamed into place.
    catalogue = _REPO / "scripts" / "mutation_pairs.py"
    assert "from scripts.mutation_gate import Pair" in catalogue.read_text(
        encoding="utf-8"), "the skipped file is no longer the pair catalogue"

    bad = []
    for d in ("scripts", "src"):
        for p in sorted((_REPO / d).rglob("*.py")):
            if p == catalogue:
                continue
            if "TextIOWrapper(sys.stdout.buffer" in p.read_text(
                    encoding="utf-8", errors="replace"):
                bad.append(str(p.relative_to(_REPO)))
    assert bad == [], (
        "these wrap sys.stdout.buffer, which closes it on GC under whoever "
        f"else holds it -- use sys.stdout.reconfigure(): {bad}")


# ------------------------------------------------------- scan_output_health
def test_health_scan_over_zero_nifs_is_not_a_pass(empty_pack):
    """THE MEASURED DEFECT: rc=0 and "=== SCAN DONE ===" over an empty dir."""
    rc, out = _run("scripts/scan_output_health.py", empty_pack)
    assert rc != 0, "a health scan that read no NIFs must not report success"
    assert rc == 3, "3 = measured nothing, distinct from 1 = found defects"
    assert "0/0 is not a pass" in out


def test_health_scan_never_prints_a_bare_all_clear(empty_pack):
    """The old line said DONE whether it had read 2064 NIFs or none.

    Any verdict line must now carry the population, so "clean" cannot be read
    off a run that had nothing to be clean about.
    """
    rc, out = _run("scripts/scan_output_health.py", empty_pack)
    assert "=== SCAN DONE ===" not in out
    assert "SCAN CLEAN" not in out


def test_a_real_defect_outranks_an_incomplete_scope(tmp_path):
    """A skipped INVISIBLE pass must not downgrade a LOAD-FAIL to "fix paths".

    Precedence matters: this exact ordering was wrong in the first draft of
    the fix and reported a genuine corrupt NIF as exit 3.
    """
    meshes = tmp_path / "meshes" / "!UBE"
    meshes.mkdir(parents=True)
    (meshes / "broken_1.nif").write_bytes(b"not a nif")   # forces LOAD-FAIL
    rc, out = _run("scripts/scan_output_health.py", tmp_path)
    assert rc == 1, "found a defect -> 1, even though INVISIBLE could not run"
    assert "SCAN FOUND DEFECTS" in out
    assert "INVISIBLE pass NOT RUN" in out, "the skipped pass is still named"


def test_the_verdict_line_states_the_population(tmp_path):
    meshes = tmp_path / "meshes" / "!UBE"
    meshes.mkdir(parents=True)
    (meshes / "broken_1.nif").write_bytes(b"not a nif")
    _rc, out = _run("scripts/scan_output_health.py", tmp_path)
    assert "1 NIF(s)" in out, "the count must appear in the verdict, not just the log"


# ---------------------------------------------------- scan_nude_skin_chain
def test_fatal_does_not_exit_zero(tmp_path):
    """It printed FATAL and `return`ed. A shell read that as success."""
    (tmp_path / "mods").mkdir()
    (tmp_path / "profiles").mkdir()
    rc, out = _run("scripts/analysis/scan_nude_skin_chain.py",
                   env_extra={"CBBE2UBE_MODS_ROOT": str(tmp_path)})
    assert "FATAL" in out, "fixture no longer reaches the FATAL path"
    assert rc != 0, "a tool that prints FATAL must not report success"
    assert rc == 3


def test_the_fatal_guard_is_reachable_and_not_dead(tmp_path):
    """Self-check on the fixture: a guard that never fires proves nothing.

    If the tool ever stops looking under CBBE2UBE_MODS_ROOT this fixture would
    silently stop exercising the guard, and the test above would pass for the
    wrong reason.
    """
    (tmp_path / "mods").mkdir()
    (tmp_path / "profiles").mkdir()
    _rc, out = _run("scripts/analysis/scan_nude_skin_chain.py",
                    env_extra={"CBBE2UBE_MODS_ROOT": str(tmp_path)})
    assert "UBE_AllRace.esp providers on disk: 0" in out


# ------------------------------------------------- disable_unconstrained_smp
_GOOD_XML = ('<system><per-vertex-shape name="a">'
             '<generic-constraint/></per-vertex-shape></system>')


def test_smp_patcher_separates_bad_path_from_nothing_found(tmp_path):
    """rglob on a missing dir yields nothing and raises nothing.

    A typo therefore read exactly like a pack with no crash pattern left.
    Three outcomes must be three exit codes.
    """
    rc_bad, _ = _run("scripts/disable_unconstrained_smp.py", tmp_path / "nope")
    assert rc_bad == 2, "a path that does not exist is a usage error"

    empty = tmp_path / "empty"
    empty.mkdir()
    rc_empty, out = _run("scripts/disable_unconstrained_smp.py", empty)
    assert rc_empty == 3, "examined nothing is not a clean verdict"
    assert "examined NO xml" in out


_CRASH_XML = ('<system><per-vertex-shape name="a"/>'
              '<per-triangle-shape name="BaseShape"/></system>')


def test_smp_patcher_reports_renames_that_failed(tmp_path):
    """Every rename failing printed ERR lines and still exited 0.

    The armors that were NOT renamed are exactly the ones that still
    OOB-crash FSMP, so a partial patch reading as a complete one is the
    dangerous direction. Collision with an existing target makes rename
    raise deterministically, with no reliance on file permissions.
    """
    (tmp_path / "crash.xml").write_text(_CRASH_XML, encoding="utf-8")
    (tmp_path / "crash.xml.nosmp").write_text("blocks the rename",
                                              encoding="utf-8")
    rc, out = _run("scripts/disable_unconstrained_smp.py", tmp_path, "--apply")
    assert rc == 1, out
    assert "FAILED to rename 1 of 1" in out
    assert "STILL the crash pattern" in out


def test_smp_patcher_still_exits_zero_on_a_genuinely_clean_pack(tmp_path):
    """THE CONTROL FOR THIS WHOLE LANE.

    A guard that turns healthy runs red is worse than the bug. A directory
    holding a properly constrained XML must still be a pass.
    """
    (tmp_path / "ok.xml").write_text(_GOOD_XML, encoding="utf-8")
    rc, out = _run("scripts/disable_unconstrained_smp.py", tmp_path)
    assert rc == 0, out
    assert "examined=1" in out, "the population is stated even when clean"


# ------------------------------------------------------- strip_nude_handfeet
def test_strip_usage_error_is_not_success():
    rc, out = _run("scripts/strip_nude_handfeet.py")
    assert rc == 2
    assert "need esp path" in out


def _esp_with(tmp_path, name, armas):
    """Write a minimal ESP; `armas` of None means NO ARMA group at all."""
    sys.path.insert(0, str(_REPO))
    import struct as _struct
    from src import esp
    from src.esp import encode_subrecord, encode_zstring

    def _arma(fid, edid, mesh):
        payload = (encode_subrecord(b"EDID", encode_zstring(edid))
                   + encode_subrecord(b"BOD2", _struct.pack("<II", 1 << 2, 0))
                   + encode_subrecord(b"MOD3", encode_zstring(mesh)))
        return esp.Record(sig=b"ARMA", flags=0, formid=fid, timestamp_vc=0,
                          version_unk=0x002C, payload=payload)

    groups = ([] if armas is None
              else [esp.Group(label=b"ARMA",
                              records=[_arma(*a) for a in armas])])
    e = esp.ESP(header=esp.TES4Header(masters=["Skyrim.esm"]), groups=groups)
    e.save(tmp_path / name)
    return tmp_path / name


def test_strip_on_a_plugin_with_no_arma_group_is_not_a_clean_strip(tmp_path):
    """Every loop ran over an empty list and reported 0/0/0 as a success."""
    p = _esp_with(tmp_path, "NoArma.esp", None)
    rc, out = _run("scripts/strip_nude_handfeet.py", p, "--apply")
    assert rc == 3
    assert "no ARMA group" in out


def test_strip_does_not_rewrite_a_plugin_it_changed_nothing_in(tmp_path):
    """--apply used to re-serialise a deployed ESP for a no-op edit and print
    "saved", which reads as a strip that happened."""
    p = _esp_with(tmp_path, "NoNude.esp",
                  [(0x801, "ArmorGauntlet", "Armor\\Steel\\Gauntlets_1.nif")])
    before = p.read_bytes()
    rc, out = _run("scripts/strip_nude_handfeet.py", p, "--apply")
    assert rc == 3
    assert "NOTHING MATCHED" in out
    assert "saved" not in out
    assert p.read_bytes() == before, "a no-op run must not rewrite the plugin"


def test_strip_still_succeeds_when_there_is_something_to_strip(tmp_path):
    """THE CONTROL: the new guards must not block the real job."""
    p = _esp_with(tmp_path, "HasNude.esp", [
        (0x801, "NakedHands",
         "Actors\\Character\\Character Assets\\femalehands_1.nif"),
        (0x802, "ArmorGauntlet", "Armor\\Steel\\Gauntlets_1.nif"),
    ])
    rc, out = _run("scripts/strip_nude_handfeet.py", p, "--apply")
    assert rc == 0, out
    assert "nude/actor-skin ARMAs to remove: 1" in out
    assert "saved" in out


# -------------------------------------------------- build_body_collider_proxy
def test_collider_batch_over_no_candidates_is_not_success(tmp_path):
    """"batch: 0 armors ... processed 0 NIFs" used to exit 0."""
    rc_bad, _ = _run("scripts/build_body_collider_proxy.py",
                     "--batch", tmp_path / "nope")
    assert rc_bad == 2
    (tmp_path / "ok.xml").write_text(_GOOD_XML, encoding="utf-8")
    rc, out = _run("scripts/build_body_collider_proxy.py", "--batch", tmp_path)
    assert rc == 3
    assert "Not a verdict about that pack" in out


def test_half_applied_armor_is_detected():
    """A NIF with a proxy whose XML still names BaseShape still OOB-crashes.

    Both halves must land together or the armor is in the exact state the
    tool exists to remove, while the tool reports having done its job.

    Deliberately NOT parametrized: pytest reports a parametrized case as
    `name[PARAM]` and the mutation gate matches test ids exactly, so a pair
    anchored on the bare name reads MISSED while the mutation is in fact
    breaking every case. That cost a gate round on GFS-a.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_bcp", _REPO / "scripts" / "build_body_collider_proxy.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    cases = [
        ("ok: VirtualBody 2500v/4800t", "xml-repointed", False),
        ("ok: VirtualBody 2500v/4800t", "xml-not-baseshape-collider", True),
        ("ok: VirtualBody 2500v/4800t", "no-xml", True),
        ("skip: no BaseShape", "xml-repointed", True),
        # the ordinary re-run: both halves already done, neither changes
        ("skip: already has VirtualBody", "xml-not-baseshape-collider", False),
    ]
    for nif_msg, xml_msg, broken in cases:
        assert m.pair_is_broken(nif_msg, xml_msg) is broken, (nif_msg, xml_msg)


# ------------------------------------------------------------ augment_nude_tri
class _Morph:
    def __init__(self, name, offsets):
        self.name, self.offsets = name, offsets


class _Shape:
    def __init__(self, name, morphs):
        self.name, self.morphs = name, morphs


class _Tri:
    def __init__(self, shapes):
        self.shapes = shapes
        self.saved = False

    def save(self, _p):
        self.saved = True


@pytest.fixture()
def ant_mod():
    """Import the tool WITHOUT letting it keep pytest's stdout.

    Function-scoped on purpose: the save/restore has to pair with ONE test.
    At module scope it restored the FIRST test's capture object, which pytest
    closes at that test's end, and every later test then wrote to a closed
    file ("I/O operation on closed file" during teardown).

    augment_nude_tri rebinds `sys.stdout` to a TextIOWrapper at import. Left
    alone that silently disables output capture for the rest of the session --
    which is how the first version of these tests came to assert on an empty
    string while only the exit code was really being checked. Both the
    no-seam path and the nothing-augmented path exit 3, so without the
    message the test could not tell which guard had fired.
    """
    import sys as _sys
    saved = _sys.stdout
    try:
        from scripts import augment_nude_tri as ant
    finally:
        _sys.stdout = saved
    return ant


def _stub_augment(monkeypatch, tmp_path, part_offset, ant):
    """Wire the tool onto fake meshes `part_offset` units from the body."""
    import io as _io
    import sys as _sys
    import numpy as np

    pdir = tmp_path / "Hands"
    pdir.mkdir()
    (pdir / "h.tri").write_bytes(b"x")     # only existence is checked
    (pdir / "h_1.nif").write_bytes(b"x")

    body = _Tri([_Shape("Body", [_Morph("SliderA", [(0, 1.0, 0.0, 0.0),
                                                    (1, 1.0, 0.0, 0.0)])])])
    part = _Tri([_Shape("Hand", [])])
    body_v = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    part_v = body_v + np.array([0.0, 0.0, float(part_offset)])

    monkeypatch.setattr(ant.tri_mod.TriFile, "load",
                        staticmethod(lambda p: body if "h.tri" not in str(p)
                                     else part))
    monkeypatch.setattr(ant, "nif_verts",
                        lambda p, want=None: ("Body", body_v)
                        if "h_1" not in str(p) else ("Hand", part_v))
    monkeypatch.setattr(ant, "PARTS", {"HANDS": (pdir, "h")})
    monkeypatch.setattr(ant, "BODY_TRI", tmp_path / "b.tri")
    monkeypatch.setattr(ant, "BODY_NIF", tmp_path / "b_1.nif")
    monkeypatch.setattr(ant.sys, "argv", ["augment_nude_tri.py", "--apply"])
    # print() resolves sys.stdout at call time, so this captures the tool's
    # output despite the import-time rebinding.
    buf = _io.StringIO()
    monkeypatch.setattr(_sys, "stdout", buf)
    return part, buf


def test_no_seam_coincidence_refuses_to_write(tmp_path, monkeypatch, ant_mod):
    """seam_n WAS PRINTED AND IGNORED -- the control this method rests on.

    With the part 40u from the body nothing is seam-coincident, every
    transferred delta is ~0, every slider prunes, and --apply rewrote the
    .tri unchanged and printed WROTE. That reads as a fix.
    """
    part, buf = _stub_augment(monkeypatch, tmp_path, 40.0, ant_mod)
    with pytest.raises(SystemExit) as ex:
        ant_mod.main()
    assert ex.value.code == 3
    out = buf.getvalue()
    assert out, "captured nothing -- the test cannot tell which guard fired"
    assert "NO seam-coincident vert" in out, out
    assert "WROTE" not in out
    assert part.saved is False, "must not rewrite the .tri on a no-op transfer"


def test_a_coincident_seam_still_transfers_and_writes(tmp_path, monkeypatch,
                                                      ant_mod):
    """The control: the guard must not block the case it exists to protect."""
    part, buf = _stub_augment(monkeypatch, tmp_path, 0.0, ant_mod)
    ant_mod.main()
    out = buf.getvalue()
    assert "WROTE" in out, out
    assert part.saved is True
    assert "parts augmented: 1/1" in out
