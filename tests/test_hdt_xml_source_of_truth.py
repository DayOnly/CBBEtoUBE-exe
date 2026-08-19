"""BUG-00: the physics-XML lookup must not FAIL OPEN.

A piece's collider / soft-body protections are all membership tests
(`if s.name in collider_names: continue`), so when the lookup returns an EMPTY
set every one of them disengages at once and the jiggle passes graft onto the
registered collision proxies. Those bones are not XML-declared, FSMP has no
rigid body for them, and the cloth free-falls off the actor (reported in game
2026-08-19 on the bandit cuirass; the empty-set cause was confirmed by an exact
bone-for-bone fingerprint reproduction).

Two layers are under test here:
  1. `_hdt_xml_bind_piece_source` -- capture the XML from the SOURCE at the top
     of the conversion, so a later DESTINATION-side lookup can recover.
  2. `_hdt_protect_all_or_none` -- when nothing can be read at all, answer with
     EVERY shape name rather than none, so the protections stay engaged.

The first version of layer 1 was INERT (it was handed `nif_io.load_nif`'s
dataclass, which has no `rootNode`, so the read threw and bound nothing) while
looking perfectly wired up. `test_bind_populates_from_source` exists so that
cannot happen again -- it asserts the bind actually captured text, not merely
that it was called.
"""
import pytest

from pathlib import Path

from src import nif_convert as nc


class _FakeExtra:
    def __init__(self, name, string_data):
        self.name = name
        self.string_data = string_data


class _FakeRoot:
    def __init__(self, extras):
        self._extras = extras

    def extra_data(self):
        return self._extras


class _FakeShape:
    def __init__(self, name):
        self.name = name


class _FakeNif:
    """Only what the lookups touch: the physics declaration and the shape list.

    Deliberately a real object with a real `rootNode`, because the bug this
    file guards was caused by an object WITHOUT one being accepted silently."""

    def __init__(self, declares=True, shapes=("Cloth", "ClothCol", "Proxy")):
        extras = ([_FakeExtra("HDT Skinned Mesh Physics Object",
                              r"meshes\mod\xml\piece.xml")] if declares else [])
        self.rootNode = _FakeRoot(extras)
        self.shapes = [_FakeShape(n) for n in shapes]


XML = ('<system><per-triangle-shape name="ClothCol"/>'
       '<per-vertex-shape name="Proxy"/>'
       '<bone name="NPC Spine2 [Spn2]"/></system>')


@pytest.fixture(autouse=True)
def _clean_binding():
    """Never let a bound text leak between tests -- that would make a failing
    assertion pass for the wrong reason."""
    nc._PIECE_HDT_XML_TEXT = None
    yield
    nc._PIECE_HDT_XML_TEXT = None


def test_bind_populates_from_source(monkeypatch, tmp_path):
    """The bind must actually CAPTURE text. Guards the inert-bind regression."""
    src = tmp_path / "piece_1.nif"
    src.write_bytes(b"nif")
    monkeypatch.setattr(nc, "_read_source_hdt_xml_text", lambda p, nif=None: XML)
    nc._hdt_xml_bind_piece_source(src, nif=_FakeNif())
    assert nc._PIECE_HDT_XML_TEXT == XML


def test_bind_ignores_a_non_pynifly_nif(monkeypatch, tmp_path):
    """`convert_nif` has an `nif_io.load_nif` dataclass to hand, which has no
    `rootNode`. Passing it must NOT be forwarded into the reader (that is what
    made the first version bind nothing) -- the bind falls back to the path."""
    src = tmp_path / "piece_1.nif"
    src.write_bytes(b"nif")
    seen = {}

    def _reader(p, nif=None):
        seen["nif"] = nif
        return XML

    monkeypatch.setattr(nc, "_read_source_hdt_xml_text", _reader)

    class _NoRootNode:
        shapes = []

    nc._hdt_xml_bind_piece_source(src, nif=_NoRootNode())
    assert seen["nif"] is None, "a non-pynifly nif must not reach the reader"
    assert nc._PIECE_HDT_XML_TEXT == XML


def test_destination_lookup_recovers_from_the_bound_source(monkeypatch, tmp_path):
    """The real failure: the destination copy does not resolve. With the source
    text bound, the collider set must still come out CORRECT -- not empty, and
    not the fail-closed everything."""
    dst = tmp_path / "out" / "piece_1.nif"
    dst.parent.mkdir(parents=True)
    dst.write_bytes(b"nif")
    monkeypatch.setattr(nc, "_read_source_hdt_xml_disk", lambda p, nif=None: None)
    monkeypatch.setattr(nc, "_find_hdt_xml_for_armor", lambda p: None)
    nc._PIECE_HDT_XML_TEXT = XML
    nc._hdt_xml_cache_clear()

    fake = _FakeNif()
    assert nc._hdt_collider_shape_names(dst, nif=fake) == {"ClothCol"}
    assert nc._hdt_softbody_shape_names(dst, nif=fake) == {"Proxy"}


def test_unresolvable_and_unbound_fails_CLOSED(monkeypatch, tmp_path):
    """Nothing readable anywhere. The lookup must protect EVERY shape, because
    an empty set silently un-protects all of them."""
    dst = tmp_path / "piece_1.nif"
    dst.write_bytes(b"nif")
    monkeypatch.setattr(nc, "_read_source_hdt_xml_disk", lambda p, nif=None: None)
    monkeypatch.setattr(nc, "_find_hdt_xml_for_armor", lambda p: None)
    nc._PIECE_HDT_XML_TEXT = None
    nc._hdt_xml_cache_clear()

    fake = _FakeNif(shapes=("Cloth", "ClothCol", "Proxy"))
    assert nc._hdt_collider_shape_names(dst, nif=fake) == {
        "Cloth", "ClothCol", "Proxy"}
    assert nc._hdt_softbody_shape_names(dst, nif=fake) == {
        "Cloth", "ClothCol", "Proxy"}


def test_a_piece_with_no_physics_xml_still_answers_EMPTY(monkeypatch, tmp_path):
    """Fail-closed must not fire on pieces that simply have no physics -- that
    would freeze the skin passes on most of the pack. The discriminator is the
    NIF's own declaration, nothing else."""
    dst = tmp_path / "piece_1.nif"
    dst.write_bytes(b"nif")
    monkeypatch.setattr(nc, "_read_source_hdt_xml_disk", lambda p, nif=None: None)
    monkeypatch.setattr(nc, "_find_hdt_xml_for_armor", lambda p: None)
    nc._PIECE_HDT_XML_TEXT = None
    nc._hdt_xml_cache_clear()

    fake = _FakeNif(declares=False)
    assert nc._hdt_collider_shape_names(dst, nif=fake) == set()
    assert nc._hdt_softbody_shape_names(dst, nif=fake) == set()


def test_unresolved_declaration_is_RECORDED(monkeypatch, tmp_path):
    """The old warning printed to a pool worker's stderr, which the frozen exe
    discards -- so the one condition that disarms every physics protection was
    invisible in exactly the runs that mattered. It must reach the pass log."""
    dst = tmp_path / "piece_1.nif"
    dst.write_bytes(b"nif")
    monkeypatch.setattr(nc, "_read_source_hdt_xml_disk", lambda p, nif=None: None)
    monkeypatch.setattr(nc, "_find_hdt_xml_for_armor", lambda p: None)
    nc._PIECE_HDT_XML_TEXT = None
    nc._hdt_xml_cache_clear()
    noted = []
    monkeypatch.setattr(nc, "_note_pass_failure",
                        lambda name, exc, *a, **k: noted.append((name, exc)))

    nc._read_source_hdt_xml_text_uncached(dst, nif=_FakeNif())
    assert noted, "an unresolved physics declaration must be recorded"
    assert noted[0][0] == "hdt_xml_unresolved"


def test_convert_nif_binds_before_it_dispatches():
    """The bind must sit in `convert_nif` -- THE single entry point, which phase
    2 is reached through -- and before the paths diverge, or one path converts
    with the protections still able to fail open."""
    import inspect
    src = inspect.getsource(nc.convert_nif)
    assert "_hdt_xml_bind_piece_source(" in src
    bind_at = src.index("_hdt_xml_bind_piece_source(")
    dispatch_at = src.index("convert_nif_phase2(")
    assert bind_at < dispatch_at, (
        "the source XML must be bound BEFORE the phase-2 dispatch")


# --- the backstop guard -----------------------------------------------------

class _WeightedShape:
    def __init__(self, name, bones):
        self.name = name
        self.bone_weights = {b: [(0, 1.0)] for b in bones}


class _ShapeNif:
    def __init__(self, shapes):
        self.rootNode = _FakeRoot(
            [_FakeExtra("HDT Skinned Mesh Physics Object", r"meshes\x\p.xml")])
        self.shapes = shapes


def _guard(monkeypatch, tmp_path, ours, author):
    """Run the guard with a fixed XML and a chosen ours/author bone split."""
    dst = tmp_path / "out_1.nif"
    src = tmp_path / "src_1.nif"
    dst.write_bytes(b"n")
    src.write_bytes(b"n")
    monkeypatch.setattr(nc, "_read_source_hdt_xml_text", lambda p, nif=None: XML)
    monkeypatch.setattr(nc, "_hdt_collider_shape_names",
                        lambda p, nif=None: {"ClothCol"})
    monkeypatch.setattr(nc, "_hdt_softbody_shape_names",
                        lambda p, nif=None: set())

    class _Pyn:
        @staticmethod
        def NifFile(filepath=None):
            return _ShapeNif([_WeightedShape(
                "ClothCol", ours if str(filepath) == str(dst) else author)])

    monkeypatch.setattr(nc, "_pynifly", lambda: _Pyn)
    noted = []
    monkeypatch.setattr(nc, "_note_pass_failure",
                        lambda name, exc, *a, **k: noted.append(name))
    n = nc._audit_registered_shape_declared_bones(dst, src)
    return n, noted


DECL_BONE = "NPC Spine2 [Spn2]"


def test_guard_fires_on_a_bone_WE_added(monkeypatch, tmp_path):
    n, noted = _guard(monkeypatch, tmp_path,
                      ours=[DECL_BONE, "L Breast02"], author=[DECL_BONE])
    assert n == 1
    assert "registered_shape_undeclared_bones" in noted


def test_guard_is_SILENT_on_the_authors_own_undeclared_bone(monkeypatch, tmp_path):
    """336 registered shapes ship undeclared bones from their own author --
    counting those would condemn the pack and drown the real signal."""
    n, noted = _guard(monkeypatch, tmp_path,
                      ours=[DECL_BONE, "L Breast02"],
                      author=[DECL_BONE, "L Breast02"])
    assert n == 0
    assert noted == []


def test_guard_is_SILENT_when_nothing_undeclared(monkeypatch, tmp_path):
    n, noted = _guard(monkeypatch, tmp_path, ours=[DECL_BONE], author=[DECL_BONE])
    assert n == 0
    assert noted == []


def test_phase2_is_never_entered_directly():
    """`_PIECE_HDT_XML_TEXT` is a MODULE GLOBAL rebound at the top of
    `convert_nif`. That is only safe while `convert_nif` is the sole way in --
    a direct call to `convert_nif_phase2` would skip the bind and let the
    PREVIOUS piece's XML answer for this one, which is the cross-armour
    contamination the memo on `_HDT_XML_TEXT_CACHE` exists to prevent.

    True today by there being exactly one call site. Pinned so it stays true."""
    import ast
    import inspect
    from src import nif_convert as _nc

    tree = ast.parse(inspect.getsource(_nc))
    callers = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call)
                    and getattr(sub.func, "id", None) == "convert_nif_phase2"):
                callers.append(node.name)
    assert callers == ["convert_nif"], (
        f"convert_nif_phase2 must be reachable ONLY through convert_nif "
        f"(which binds the piece's physics XML first); callers found: {callers}")


def test_guard_reads_declared_bones_from_the_SAME_side_as_registered(
        monkeypatch, tmp_path):
    """The guard must not FAIL OPEN the way the bug it catches does.

    It computes `registered` from the DESTINATION. Reading declared bones from
    the SOURCE instead meant that on a piece whose XML resolves only from the
    output side, the source read returned None and the guard reported "0
    violations" -- 44 real offenders unreported. Declared bones must come from
    the same side, and an unreadable XML must be RECORDED, not swallowed."""
    dst = tmp_path / "out_1.nif"
    src = tmp_path / "src_1.nif"
    dst.write_bytes(b"n")
    src.write_bytes(b"n")

    seen_paths = []

    def _reader(p, nif=None):
        seen_paths.append(Path(p).name)
        return XML if Path(p).name == dst.name else None   # source side is dead

    monkeypatch.setattr(nc, "_read_source_hdt_xml_text", _reader)
    monkeypatch.setattr(nc, "_hdt_collider_shape_names",
                        lambda p, nif=None: {"ClothCol"})
    monkeypatch.setattr(nc, "_hdt_softbody_shape_names",
                        lambda p, nif=None: set())

    class _Pyn:
        @staticmethod
        def NifFile(filepath=None):
            bones = ([DECL_BONE, "L Breast02"] if str(filepath) == str(dst)
                     else [DECL_BONE])
            return _ShapeNif([_WeightedShape("ClothCol", bones)])

    monkeypatch.setattr(nc, "_pynifly", lambda: _Pyn)
    noted = []
    monkeypatch.setattr(nc, "_note_pass_failure",
                        lambda name, exc, *a, **k: noted.append(name))

    n = nc._audit_registered_shape_declared_bones(dst, src)
    assert dst.name in seen_paths, "declared bones must be read from the DESTINATION"
    assert n == 1, "the guard must still see the violation when the source read fails"
    assert "registered_shape_undeclared_bones" in noted


def test_guard_records_when_it_CANNOT_check(monkeypatch, tmp_path):
    """No readable XML at all + registered shapes = the invariant was not
    verified. That must be logged, never reported as a clean 0."""
    dst = tmp_path / "out_1.nif"
    src = tmp_path / "src_1.nif"
    dst.write_bytes(b"n")
    src.write_bytes(b"n")
    monkeypatch.setattr(nc, "_read_source_hdt_xml_text", lambda p, nif=None: None)
    monkeypatch.setattr(nc, "_hdt_collider_shape_names",
                        lambda p, nif=None: {"ClothCol"})
    monkeypatch.setattr(nc, "_hdt_softbody_shape_names",
                        lambda p, nif=None: set())

    class _Pyn:
        @staticmethod
        def NifFile(filepath=None):
            return _ShapeNif([_WeightedShape("ClothCol", [DECL_BONE])])

    monkeypatch.setattr(nc, "_pynifly", lambda: _Pyn)
    noted = []
    monkeypatch.setattr(nc, "_note_pass_failure",
                        lambda name, exc, *a, **k: noted.append(name))
    nc._audit_registered_shape_declared_bones(dst, src)
    assert "registered_shape_bones_UNCHECKED" in noted
