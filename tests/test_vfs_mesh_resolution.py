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

"""Coverage broadening: the converter must find an armour's meshes even when
they live in a DIFFERENT mod than the armour's ESP (BodySlide output, mesh/
texture replacers, patches) — resolved through the full MO2 VFS in priority
order, exactly how the game resolves them.

Regression guard for the gap that left ~70% of converted armatures pointing at
the original CBBE mesh (female bodies built into a separate Bodyslide-output
mod were never seen by the source-folder-only walk).
"""
from pathlib import Path

from src import discovery, auto_convert


def _touch(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")


# A full-height body vert cloud (>= the 500-vert / 60u-z-range bespoke-body floor)
# and a tiny exposed-skin slice (below it), for the body-provenance check.
_FULL_BODY_VERTS = [(0.0, 0.0, float(i) * 0.15) for i in range(700)]   # z 0..~105
_SKIN_SLICE_VERTS = [(0.0, 0.0, float(i) * 0.1) for i in range(46)]    # 46 verts, z ~4.5


class _FakeShape:
    """Minimal stand-in for a pynifly shape: a name, a diffuse texture path, and
    verts -- all that build_mesh_index's body-provenance check reads."""
    def __init__(self, name, diffuse="", verts=None):
        self.name = name
        self.textures = {"Diffuse": diffuse} if diffuse else {}
        self.verts = verts if verts is not None else []


class _FakeNif:
    def __init__(self, shapes):
        self.shapes = shapes


def test_build_mesh_index_priority_winner(tmp_path):
    mods = tmp_path / "mods"
    # Same path provided by two mods; the higher-priority (listed first) wins.
    _touch(mods / "HighPrio" / "meshes" / "armor" / "foo" / "bar_1.nif")
    _touch(mods / "LowPrio" / "meshes" / "armor" / "foo" / "bar_1.nif")
    # A mesh only the BodySlide-output mod provides (the armour's own folder
    # would NOT have it) — this is the case the broadening exists to catch.
    _touch(mods / "BodyslideOut" / "meshes" / "armor" / "foo" / "baz_1.nif")
    enabled = ["HighPrio", "BodyslideOut", "LowPrio"]  # priority: top first
    idx = discovery.build_mesh_index(
        mods, enabled,
        target_keys={"armor/foo/bar_1.nif", "armor/foo/baz_1.nif"})
    assert set(idx) == {"armor/foo/bar_1.nif", "armor/foo/baz_1.nif"}
    assert idx["armor/foo/bar_1.nif"].parents[3].name == "HighPrio"
    assert idx["armor/foo/baz_1.nif"].parents[3].name == "BodyslideOut"


def test_build_mesh_index_target_scoping(tmp_path):
    mods = tmp_path / "mods"
    _touch(mods / "M" / "meshes" / "armor" / "want_1.nif")
    _touch(mods / "M" / "meshes" / "armor" / "ignore_1.nif")
    idx = discovery.build_mesh_index(
        mods, ["M"], target_keys={"armor/want_1.nif"})
    assert set(idx) == {"armor/want_1.nif"}  # ignore_1 excluded


def test_build_mesh_index_skip_mods(tmp_path):
    mods = tmp_path / "mods"
    _touch(mods / "Output" / "meshes" / "armor" / "x_1.nif")
    _touch(mods / "Real" / "meshes" / "armor" / "x_1.nif")
    idx = discovery.build_mesh_index(
        mods, ["Output", "Real"], target_keys={"armor/x_1.nif"},
        skip_mods={"Output"})
    assert idx["armor/x_1.nif"].parents[2].name == "Real"


def test_build_mesh_index_skip_mods_case_insensitive(tmp_path):
    """skip_mods must match the enabled-mod folder name case-insensitively.
    `_find_armor_mod_dirs` passes a LOWERCASED skip set (the output mod name),
    while the enabled list keeps original case (e.g. 'CBBEtoUBE Auto') — a
    case-sensitive compare would fail to skip the output mod and could resolve
    armour meshes to our own already-converted output."""
    mods = tmp_path / "mods"
    _touch(mods / "CBBEtoUBE Auto" / "meshes" / "armor" / "x_1.nif")  # output
    _touch(mods / "Real" / "meshes" / "armor" / "x_1.nif")
    idx = discovery.build_mesh_index(
        mods, ["CBBEtoUBE Auto", "Real"], target_keys={"armor/x_1.nif"},
        skip_mods={"cbbetoube auto"})  # lowercased, as _find_armor_mod_dirs sends
    assert idx["armor/x_1.nif"].parents[2].name == "Real"


def test_build_mesh_index_deprioritizes_bodyslide_output_source(tmp_path):
    """A 3BA/HIMBO BodySlide OUTPUT must NOT win the armour SOURCE over the base
    mod, even at higher MO2 priority. It's the mesh morphed to the wrong body
    PRESET; feeding it into a UBE conversion bakes that body's shape in and
    squashes the cloth layers together -> clipping (the 2026-07-08 New Leather
    Armor bug: 26 armours sourced from 'Bodyslide Output - 3BA'). Tier order:
    (0) base/replacers, (1) UBE outputs, (2) other-body outputs. A BodySlide
    output still wins a mesh nothing else provides (no invisibility regression).
    #bodyslide-source"""
    mods = tmp_path / "mods"
    a = "armor/foo"
    # bar: base + both outputs; the 3BA output is HIGHEST priority but must lose.
    _touch(mods / "Authoria - Bodyslide Output - 3BA" / "meshes" / a / "bar_1.nif")
    _touch(mods / "Authoria - Bodyslide Output - UBE" / "meshes" / a / "bar_1.nif")
    _touch(mods / "New Leather Armor" / "meshes" / a / "bar_1.nif")
    # ube: no base; the UBE output must beat the other-body 3BA output.
    _touch(mods / "Authoria - Bodyslide Output - 3BA" / "meshes" / a / "ube_1.nif")
    _touch(mods / "Authoria - Bodyslide Output - UBE" / "meshes" / a / "ube_1.nif")
    # only: ONLY the 3BA output provides it — must still resolve (fills the gap).
    _touch(mods / "Authoria - Bodyslide Output - 3BA" / "meshes" / a / "only_1.nif")
    enabled = ["Authoria - Bodyslide Output - 3BA",   # highest MO2 priority
               "Authoria - Bodyslide Output - UBE",
               "New Leather Armor"]                    # lowest
    idx = discovery.build_mesh_index(mods, enabled, target_keys={
        "armor/foo/bar_1.nif", "armor/foo/ube_1.nif", "armor/foo/only_1.nif"})
    assert idx["armor/foo/bar_1.nif"].parents[3].name == "New Leather Armor", \
        "base mod must win the source over ANY BodySlide output"
    assert idx["armor/foo/ube_1.nif"].parents[3].name \
        == "Authoria - Bodyslide Output - UBE", \
        "UBE output must beat an other-body (3BA) output when there's no base"
    assert idx["armor/foo/only_1.nif"].parents[3].name \
        == "Authoria - Bodyslide Output - 3BA", \
        "a BodySlide-output-only mesh must still resolve (no invisibility regression)"


def test_build_mesh_index_prefers_canonical_body_within_tier(tmp_path, monkeypatch):
    """Within a tier, a source bundling the canonical 3BA body must beat one
    bundling a BESPOKE body (e.g. an HDT-SMP "vanilla armours" mod ships its own
    slim/large physics body), even at higher MO2 priority. The armour is authored
    flush on whatever body it bundles; a mismatched bundled body leaves soft-body
    cloth standing off the UBE target (the 2026-07-09 Fur Cuirass bug: HDT-SMP body
    bust +9.88u -> +1.77u gap; the 3BA-body source +5.70u ~= UBE +5.74u -> ~flush).
    #body-match-source"""
    mods = tmp_path / "mods"
    a = "armor/bandit"
    # Both tier 0 (neither name has 'bodyslide output'). HDT-SMP is HIGHEST priority.
    _touch(mods / "HDT SMP Vanilla Armors" / "meshes" / a / "body1f_1.nif")
    _touch(mods / "CBBE 3BA Vanilla Outfits" / "meshes" / a / "body1f_1.nif")

    def _fake_open(path_str, *a, **k):
        # HDT-SMP source bundles a bespoke full-body 'BanditBody1'; 3BA source has '3BA'.
        if "HDT SMP" in str(path_str):
            return _FakeNif([_FakeShape("BanditBody1", "textures/actors/femalebody_1.dds",
                                        _FULL_BODY_VERTS),
                             _FakeShape("Top", "textures/armor/fur.dds")])
        return _FakeNif([_FakeShape("BaseArmor", "textures/armor/fur.dds"),
                         _FakeShape("3BA", "textures/actors/femalebody_1.dds", _FULL_BODY_VERTS)])

    monkeypatch.setattr(discovery.nif_io, "open_nif_retry", _fake_open)

    enabled = ["HDT SMP Vanilla Armors",     # highest MO2 priority, BESPOKE body
               "CBBE 3BA Vanilla Outfits"]    # lower priority, CANONICAL 3BA body
    idx = discovery.build_mesh_index(
        mods, enabled, target_keys={"armor/bandit/body1f_1.nif"})
    assert idx["armor/bandit/body1f_1.nif"].parents[3].name == "CBBE 3BA Vanilla Outfits", \
        "canonical-3BA-body source must win the source over a bespoke-body source in-tier"

    # Opt-out: CBBE2UBE_NO_BODYMATCH_SELECT=1 restores pure MO2 priority.
    monkeypatch.setenv("CBBE2UBE_NO_BODYMATCH_SELECT", "1")
    idx2 = discovery.build_mesh_index(
        mods, enabled, target_keys={"armor/bandit/body1f_1.nif"})
    assert idx2["armor/bandit/body1f_1.nif"].parents[3].name == "HDT SMP Vanilla Armors", \
        "opt-out must fall back to pure MO2 priority"


def test_build_mesh_index_bodymatch_leaves_no_body_physics_source(tmp_path, monkeypatch):
    """The swap must fire ONLY on a BESPOKE-BODY incumbent. A physics source that
    bundles NO body (e.g. an HDT-SMP robe: cloth + collision, no body-skin shape) is
    NOT the bundled-body-mismatch bug -> it must KEEP winning so its SMP physics
    survives, even though a canonical-body source also provides the mesh. Guards the
    2026-07-09 over-swap that would have dropped SMP physics from mage robes.
    #body-match-source"""
    mods = tmp_path / "mods"
    a = "clothes/archmage"
    _touch(mods / "HDT-SMP College Mage Robes" / "meshes" / a / "archmagerobesf_1.nif")
    _touch(mods / "CBBE 3BA Vanilla Outfits" / "meshes" / a / "archmagerobesf_1.nif")

    def _fake_open(path_str, *a, **k):
        # The HDT-SMP robe bundles NO body: robe cloth + collision + a tiny exposed-
        # skin SLICE ('robes_skin', body-tex'd but only 46 verts) that must NOT be
        # mistaken for a bundled body.
        if "HDT-SMP" in str(path_str):
            return _FakeNif([_FakeShape("robes", "textures/clothes/archmage.dds"),
                             _FakeShape("robes_skin", "textures/actors/femalebody_1.dds",
                                        _SKIN_SLICE_VERTS),
                             _FakeShape("col", "")])
        return _FakeNif([_FakeShape("BaseArmor", "textures/clothes/archmage.dds"),
                         _FakeShape("3BA", "textures/actors/femalebody_1.dds", _FULL_BODY_VERTS)])

    monkeypatch.setattr(discovery.nif_io, "open_nif_retry", _fake_open)
    enabled = ["HDT-SMP College Mage Robes", "CBBE 3BA Vanilla Outfits"]
    idx = discovery.build_mesh_index(
        mods, enabled, target_keys={"clothes/archmage/archmagerobesf_1.nif"})
    assert idx["clothes/archmage/archmagerobesf_1.nif"].parents[3].name \
        == "HDT-SMP College Mage Robes", \
        "a no-body physics source must keep winning (SMP physics preserved)"


def test_build_mesh_index_bodymatch_does_not_override_tier(tmp_path, monkeypatch):
    """The body-match preference acts WITHIN a tier only -- it must not promote a
    canonical-body BodySlide OUTPUT (tier 2) over a bespoke-body base mod (tier 0).
    Guards the New-Leather tier fix from the body-match rule. #body-match-source"""
    mods = tmp_path / "mods"
    a = "armor/foo"
    _touch(mods / "Authoria - Bodyslide Output - 3BA" / "meshes" / a / "bar_1.nif")
    _touch(mods / "Base Armor Mod" / "meshes" / a / "bar_1.nif")

    def _fake_open(path_str, *a, **k):
        # The tier-2 output bundles a canonical 3BA body; the tier-0 base bundles a
        # bespoke body -> without the tier guard the body-match rule would wrongly
        # promote the output. The tier must still win.
        if "Bodyslide Output" in str(path_str):
            return _FakeNif([_FakeShape("3BA", "textures/actors/femalebody_1.dds",
                                        _FULL_BODY_VERTS)])
        return _FakeNif([_FakeShape("SomeBody", "textures/actors/femalebody_1.dds",
                                    _FULL_BODY_VERTS)])

    monkeypatch.setattr(discovery.nif_io, "open_nif_retry", _fake_open)
    enabled = ["Authoria - Bodyslide Output - 3BA", "Base Armor Mod"]
    idx = discovery.build_mesh_index(mods, enabled, target_keys={"armor/foo/bar_1.nif"})
    assert idx["armor/foo/bar_1.nif"].parents[3].name == "Base Armor Mod", \
        "tier must dominate: a canonical-body tier-2 output must NOT beat a tier-0 base"


def test_build_mesh_index_bodymatch_no_swap_without_canonical_challenger(tmp_path, monkeypatch):
    """A bespoke-body incumbent stays put unless a SAME-TIER challenger bundles the
    CANONICAL body. If the only other provider also lacks a canonical body, MO2
    priority stands -- the rule never swaps to a merely-different bespoke source.
    #body-match-source"""
    mods = tmp_path / "mods"
    a = "armor/bandit"
    _touch(mods / "HDT SMP Vanilla Armors" / "meshes" / a / "body1f_1.nif")
    _touch(mods / "Some Other Physics Mod" / "meshes" / a / "body1f_1.nif")

    def _fake_open(path_str, *a, **k):
        # Neither source bundles a canonical '3BA' body -> no swap basis.
        return _FakeNif([_FakeShape("SomeBody", "textures/actors/femalebody_1.dds",
                                    _FULL_BODY_VERTS)])

    monkeypatch.setattr(discovery.nif_io, "open_nif_retry", _fake_open)
    enabled = ["HDT SMP Vanilla Armors", "Some Other Physics Mod"]
    idx = discovery.build_mesh_index(mods, enabled, target_keys={"armor/bandit/body1f_1.nif"})
    assert idx["armor/bandit/body1f_1.nif"].parents[3].name == "HDT SMP Vanilla Armors", \
        "no canonical challenger -> MO2 priority must stand (no swap)"


def test_build_mesh_index_bodymatch_no_swap_on_open_failure(tmp_path, monkeypatch):
    """If a candidate NIF can't be opened (corrupt / locked), the body-provenance
    check returns None and the rule must NOT swap on that basis -- the incumbent
    (MO2 priority winner) is kept. Guards against a bad challenger hijacking a mesh.
    #body-match-source"""
    mods = tmp_path / "mods"
    a = "armor/bandit"
    _touch(mods / "HDT SMP Vanilla Armors" / "meshes" / a / "body1f_1.nif")
    _touch(mods / "CBBE 3BA Vanilla Outfits" / "meshes" / a / "body1f_1.nif")

    def _fake_open(path_str, *a, **k):
        raise RuntimeError("Could not open as nif")   # every open fails

    monkeypatch.setattr(discovery.nif_io, "open_nif_retry", _fake_open)
    enabled = ["HDT SMP Vanilla Armors", "CBBE 3BA Vanilla Outfits"]
    idx = discovery.build_mesh_index(mods, enabled, target_keys={"armor/bandit/body1f_1.nif"})
    assert idx["armor/bandit/body1f_1.nif"].parents[3].name == "HDT SMP Vanilla Armors", \
        "open failure -> provenance unknown -> keep the priority winner (no swap)"


def test_build_mesh_index_bodymatch_keeps_priority_among_canonical(tmp_path, monkeypatch):
    """When the incumbent ALREADY bundles a canonical body, a lower-priority
    canonical challenger must NOT displace it -- MO2 priority decides among equally
    body-standard sources (the swap only rescues a bespoke-body incumbent).
    #body-match-source"""
    mods = tmp_path / "mods"
    a = "armor/iron"
    _touch(mods / "High Prio 3BA" / "meshes" / a / "cuirass_1.nif")
    _touch(mods / "Low Prio 3BA" / "meshes" / a / "cuirass_1.nif")

    def _fake_open(path_str, *a, **k):
        # Both bundle a canonical 3BA body.
        return _FakeNif([_FakeShape("BaseArmor", "textures/armor/iron.dds"),
                         _FakeShape("3BA", "textures/actors/femalebody_1.dds", _FULL_BODY_VERTS)])

    monkeypatch.setattr(discovery.nif_io, "open_nif_retry", _fake_open)
    enabled = ["High Prio 3BA", "Low Prio 3BA"]
    idx = discovery.build_mesh_index(mods, enabled, target_keys={"armor/iron/cuirass_1.nif"})
    assert idx["armor/iron/cuirass_1.nif"].parents[3].name == "High Prio 3BA", \
        "among canonical-body sources, MO2 priority must win (no needless swap)"


def test_resolve_prefers_vfs_when_local_missing(tmp_path):
    """The female mesh isn't in the source mod's folder, but the VFS index has
    it (BodySlide output). It must be resolved + converted, not missed."""
    src_meshes = tmp_path / "src" / "meshes"
    src_meshes.mkdir(parents=True)
    female = tmp_path / "mods" / "BodyslideOut" / "meshes" / "armor" / "foo" / "bar_1.nif"
    _touch(female)
    idx = {"armor/foo/bar_1.nif": female}
    pairs = auto_convert._resolve_armor_meshes({"armor/foo/bar"}, idx, src_meshes, [])
    assert len(pairs) == 1
    abs_src, rel = pairs[0]
    assert abs_src == female
    assert rel == "armor/foo/bar_1.nif"


def test_resolve_source_local_fallback_without_index(tmp_path):
    src_meshes = tmp_path / "src" / "meshes"
    f = src_meshes / "armor" / "foo" / "Bar_1.nif"
    _touch(f)
    pairs = auto_convert._resolve_armor_meshes({"armor/foo/bar"}, None, src_meshes, [f])
    assert [p[0] for p in pairs] == [f]
    assert pairs[0][1] == "armor/foo/Bar_1.nif"  # original case preserved


def test_resolve_legacy_no_bases_converts_all_local(tmp_path):
    src_meshes = tmp_path / "src" / "meshes"
    a = src_meshes / "a_1.nif"; _touch(a)
    b = src_meshes / "b_1.nif"; _touch(b)
    pairs = auto_convert._resolve_armor_meshes(set(), None, src_meshes, [a, b])
    assert {p[0] for p in pairs} == {a, b}


def test_find_armor_mod_dirs_selects_bodyslide_only_mod(tmp_path, monkeypatch):
    """SOURCE-SELECTION coverage: a mod whose ESP equips armour meshes that
    live ONLY in another mod (BodySlide output) — zero in its own folder — must
    still be selected as a source. This is the gate that previously dropped
    DDV Ruby Flower before conversion could even run."""
    mods = tmp_path / "mods"
    # armour mod: has an ESP, but ships NO armour meshes in its own folder
    armor_mod = mods / "DDV Ruby"
    (armor_mod / "meshes").mkdir(parents=True)
    (armor_mod / "ruby.esp").write_bytes(b"TES4")  # presence only (gate is stubbed)
    # the BodySlide-output mod is where the built armour mesh actually lives
    built = mods / "Bodyslide Output" / "meshes" / "armory" / "ruby" / "top_1.nif"
    _touch(built)
    # stub the ESP gate so we don't need a hand-built ARMA record
    monkeypatch.setattr(
        auto_convert, "_player_armor_mesh_bases",
        lambda d, **kw: {"armory/ruby/top"} if d.name == "DDV Ruby" else set())

    # WITHOUT a VFS list -> dropped (legacy: 0 own-folder meshes)
    legacy = auto_convert._find_armor_mod_dirs(mods, require_arma=True)
    assert "DDV Ruby" not in {c["name"] for c in legacy}

    # WITH the enabled-mods list -> resolved via VFS, selected
    sel = auto_convert._find_armor_mod_dirs(
        mods, require_arma=True,
        enabled_ordered=["DDV Ruby", "Bodyslide Output"])
    assert "DDV Ruby" in {c["name"] for c in sel}


def test_bodyslide_output_excluded_as_source_still_resolves_meshes(tmp_path, monkeypatch):
    """REGRESSION GUARD: the BodySlide-output mod is in the SOURCE-exclude set
    (it hosts the UBE body ref, so it must not be CONVERTED as a source). But it
    is ALSO where most armours' built female meshes live, so the mesh-resolution
    index must STILL see it. The VFS-index-share perf change wrongly fed the
    source-exclude set as the index skip set, so every armour with BodySlide-
    built meshes resolved to nothing (DDV Ruby produced 4 NIFs instead of 14).
    The index must skip ONLY the output mod (`index_skip_mods`), never the
    body/BodySlide mods."""
    mods = tmp_path / "mods"
    armor_mod = mods / "DDV Ruby"
    (armor_mod / "meshes").mkdir(parents=True)
    (armor_mod / "ruby.esp").write_bytes(b"TES4")
    built = mods / "Bodyslide Output" / "meshes" / "armory" / "ruby" / "top_1.nif"
    _touch(built)
    monkeypatch.setattr(
        auto_convert, "_player_armor_mesh_bases",
        lambda d, **kw: {"armory/ruby/top"} if d.name == "DDV Ruby" else set())

    # "Bodyslide Output" is excluded AS A SOURCE (extra_exclude_names) — exactly
    # what _cmd_auto does for body mods — yet DDV Ruby must STILL be selected,
    # because its mesh resolves FROM that excluded mod.
    sel = auto_convert._find_armor_mod_dirs(
        mods, require_arma=True,
        extra_exclude_names={"Bodyslide Output"},
        enabled_ordered=["DDV Ruby", "Bodyslide Output"])
    assert "DDV Ruby" in {c["name"] for c in sel}, \
        "body-mod source-exclude must not remove it as a mesh provider"

    # Negative: if the index DID skip Bodyslide Output (the bug), the mesh can't
    # resolve and DDV Ruby is dropped.
    sel_bug = auto_convert._find_armor_mod_dirs(
        mods, require_arma=True,
        enabled_ordered=["DDV Ruby", "Bodyslide Output"],
        index_skip_mods={"Bodyslide Output"})
    assert "DDV Ruby" not in {c["name"] for c in sel_bug}


def test_find_armor_mod_dirs_dedups_duplicate_plugin_by_load_order(tmp_path, monkeypatch):
    """When the SAME plugin filename ships in multiple enabled mods, only the
    LOAD-ORDER-WINNING copy (first in enabled_ordered) is selected as a source.
    Patching a lower-priority copy mis-targets the loaded ESP's FormID/record set
    -> records unique to the winning copy get no UBE armature -> INVISIBLE armor
    (the Helga 'Unarmored Pants' bug: the Pants ARMOs live only in the winning
    'My fixes' copy). #168"""
    mods = tmp_path / "mods"
    for name in ("Helga HiPrio", "Helga LoPrio"):
        d = mods / name
        (d / "meshes" / "armor" / "helga").mkdir(parents=True)
        (d / "_Fuse00_ArmorHelga.esp").write_bytes(b"TES4")
        _touch(d / "meshes" / "armor" / "helga" / "body_1.nif")
    # a DIFFERENT mod with a UNIQUE plugin must NOT be dropped
    other = mods / "Ruby"
    (other / "meshes" / "armory" / "ruby").mkdir(parents=True)
    (other / "ruby.esp").write_bytes(b"TES4")
    _touch(other / "meshes" / "armory" / "ruby" / "top_1.nif")

    def _bases(d, **kw):
        if d.name.startswith("Helga"):
            return {"armor/helga/body"}
        if d.name == "Ruby":
            return {"armory/ruby/top"}
        return set()
    monkeypatch.setattr(auto_convert, "_player_armor_mesh_bases", _bases)

    sel = auto_convert._find_armor_mod_dirs(
        mods, require_arma=True,
        enabled_ordered=["Helga HiPrio", "Ruby", "Helga LoPrio"])
    names = {c["name"] for c in sel}
    assert "Helga HiPrio" in names, "load-order winner must be kept"
    assert "Helga LoPrio" not in names, "lower-priority duplicate plugin must be dropped"
    assert "Ruby" in names, "a mod with a unique plugin must never be dropped"


def test_write_conversion_summary(tmp_path):
    """The coverage report aggregates per-mod counts and — most importantly —
    flags selected mods that produced ZERO meshes (the likely-still-missing
    set the user cares about)."""
    from src import nif_convert

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    # mod A: one body-swap NIF, 2 meshes resolved from other mods
    a = auto_convert.AutoConvertResult(source_dir=tmp_path / "ModA",
                                       output_dir=out_dir)
    a.source_esps = [tmp_path / "ModA" / "a.esp"]
    a.output_esps = [out_dir / "a UBE patch.esp"]
    a.nif_results = [nif_convert.ConvertResult(
        src_path=tmp_path / "x.nif", dst_path=out_dir / "x.nif",
        status="converted (body-swap)")]
    a.vfs_other_mod_count = 2
    a.textures_copied = 5

    # mod B: ZERO meshes (selected but produced nothing) -> must be flagged
    b = auto_convert.AutoConvertResult(source_dir=tmp_path / "ModB",
                                       output_dir=out_dir)
    b.source_esps = [tmp_path / "ModB" / "b.esp"]
    b.output_esps = [out_dir / "b UBE patch.esp"]

    # mod D: ZERO meshes but a DUPLICATE SOURCE — every mesh collision-skipped
    # (converted under an earlier sibling). Must NOT be reported as "missing".
    d = auto_convert.AutoConvertResult(source_dir=tmp_path / "ModD",
                                       output_dir=out_dir)
    d.source_esps = [tmp_path / "ModD" / "d.esp"]
    d.output_esps = [out_dir / "d UBE patch.esp"]
    d.notes = ["NIF collisions skipped: 8 (earlier source mod won the output path)"]

    # mod C: hard failure
    results = [
        (tmp_path / "ModA", a, None),
        (tmp_path / "ModB", b, None),
        (tmp_path / "ModD", d, None),
        (tmp_path / "ModC", None, RuntimeError("boom")),
    ]
    path = auto_convert.write_conversion_summary(out_dir, results)
    assert path is not None and path.is_file()
    text = path.read_text(encoding="utf-8")
    # ZERO-mesh mod is called out by name
    assert "ZERO meshes" in text
    assert "ModB" in text
    # hard-failure mod surfaced
    assert "ModC" in text and "boom" in text
    # VFS broadening total reflected
    assert "resolved from OTHER mods (VFS broadening): 2" in text
    # batch totals
    assert "source mods processed : 4" in text
    # collision-skipped duplicate must be in the "NOT missing" bucket, never
    # in the genuine "ZERO meshes ... still missing" list.
    assert "duplicate source" in text
    missing_block = text.split("duplicate source")[0]
    assert "ModD" not in missing_block, "ModD wrongly flagged as missing"
