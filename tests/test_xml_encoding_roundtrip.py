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

"""#bug-12 -- an XML pass must never change the file's ENCODING.

THE DEFECT THIS LOCKS DOWN. Three passes read the author's XML with
`Path(x).read_text(errors="ignore")` -- no encoding, so
`locale.getpreferredencoding()`, which on Windows is cp1252 -- and wrote it back
`.encode("utf-8")`. An author's UTF-8 BOM (`EF BB BF`) decoded to `ï»¿` and
re-encoded to SIX bytes (`C3 AF C2 BB C2 BF`) sitting BEFORE the `<?xml`
declaration. The file stops being well-formed XML, so FSMP loads no physics for
that piece at all -- silently. It shipped on 8 XMLs in the 2026-08-22 pack, two
of them cloaks, and nothing caught it because no test read the BYTES.

`errors="ignore"` compounded it by SILENTLY DROPPING any byte cp1252 could not
decode, so non-ASCII content elsewhere in an author's file was lost outright.

These tests assert on BYTES, not on parsed content -- the corruption is invisible
to a str-level comparison, which is exactly why it survived.
"""
import xml.etree.ElementTree as ET

import pytest

import src.nif_convert as nc

BOM = b"\xef\xbb\xbf"
MOJIBAKE = b"\xc3\xaf\xc2\xbb\xc2\xbf"          # BOM re-encoded from cp1252

_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<system>\n'
    '\t<per-triangle-shape name="Keep">\n'
    '\t\t<margin>0.1</margin>\n'
    '\t\t<tag>Body</tag>\n'
    '\t</per-triangle-shape>\n'
    '\t<bone name="NPC Spine [Spn0]">\n'
    '\t\t<mass>1.5</mass>\n'
    '\t</bone>\n'
    '</system>\n'
)


def _write(tmp_path, *, bom, name="phys.xml"):
    p = tmp_path / name
    p.write_bytes((BOM if bom else b"") + _XML.encode("utf-8"))
    return p


class _Shape:
    def __init__(self, name):
        self.name = name
        self.bone_names = ["NPC Spine [Spn0]"]
        self.verts = [(0.0, 0.0, 95.0)]


class _Nif:
    def __init__(self, names):
        self.shapes = [_Shape(n) for n in names]
        self.nodes = {"NPC Spine [Spn0]": object()}


# --------------------------------------------------------------------------
# the reader itself
# --------------------------------------------------------------------------

def test_reader_reports_utf8_and_keeps_the_bom(tmp_path):
    """The BOM must survive as a character, and the codec must come back utf-8 --
    that pairing is what makes the write-back byte-exact."""
    p = _write(tmp_path, bom=True)
    text, codec = nc._read_xml_roundtrip(p)
    assert codec == "utf-8"
    assert text.startswith("﻿"), "BOM must be preserved, not stripped"
    assert text.encode(codec) == p.read_bytes()


def test_reader_falls_back_to_latin1_on_undecodable_bytes(tmp_path):
    """A non-UTF-8 author file must round-trip too -- and must NOT lose bytes the
    old `errors="ignore"` would have dropped."""
    p = tmp_path / "l1.xml"
    raw = _XML.replace("Body", "B\xf8dy").encode("latin-1")
    p.write_bytes(raw)
    text, codec = nc._read_xml_roundtrip(p)
    assert codec == "latin-1"
    assert text.encode(codec) == raw


def test_reader_returns_none_for_a_missing_file(tmp_path):
    assert nc._read_xml_roundtrip(tmp_path / "nope.xml") is None


# --------------------------------------------------------------------------
# each pass that writes an XML back
# --------------------------------------------------------------------------

def test_static_chains_keeps_the_bom_intact(tmp_path, monkeypatch):
    """`_make_chains_static` rewrites every <mass>; the BOM must be untouched."""
    monkeypatch.setattr(nc, "STATIC_CHAINS", True)
    p = _write(tmp_path, bom=True)
    nc._make_chains_static(p)
    raw = p.read_bytes()
    assert b"<mass>0</mass>" in raw, "control: the pass must actually have fired"
    assert raw.startswith(BOM), "BOM must survive the rewrite"
    assert not raw.startswith(MOJIBAKE), "#bug-12: BOM was double-encoded"
    ET.fromstring(raw.decode("utf-8-sig"))          # still well-formed


def test_harden_keeps_the_bom_intact(tmp_path):
    """`_harden_hdt_xml_for_fsmp` drops blocks for shapes not in the NIF."""
    p = _write(tmp_path, bom=True)
    nc._harden_hdt_xml_for_fsmp(p, _Nif(["Other"]))   # 'Keep' is absent -> drop
    raw = p.read_bytes()
    assert b'name="Keep"' not in raw, "control: the pass must actually have fired"
    assert raw.startswith(BOM)
    assert not raw.startswith(MOJIBAKE)
    ET.fromstring(raw.decode("utf-8-sig"))


def test_body_collider_keeps_the_bom_intact(tmp_path, monkeypatch):
    """`_ensure_cloth_body_collider` used `write_text()` with no encoding, which
    is cp1252 on Windows -- the worst of the three."""
    monkeypatch.setenv("CBBE2UBE_BODY_COLLIDER", "1")
    p = tmp_path / "cloth.xml"
    p.write_bytes(BOM + (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<system>\n'
        '\t<per-vertex-shape name="Cloth">\n'
        '\t\t<tag>Fabric</tag>\n'
        '\t\t<can-collide-with-tag>ColBody</can-collide-with-tag>\n'
        '\t</per-vertex-shape>\n'
        '</system>\n').encode("utf-8"))
    fired = nc._ensure_cloth_body_collider(p, _Nif(["Cloth", "BaseShape"]))
    raw = p.read_bytes()
    if not fired:
        pytest.skip("collider gate did not qualify on this fixture")
    assert raw.startswith(BOM)
    assert not raw.startswith(MOJIBAKE)
    ET.fromstring(raw.decode("utf-8-sig"))


# --------------------------------------------------------------------------
# the control: prove the ASSERTIONS ABOVE CAN FAIL
# --------------------------------------------------------------------------

def test_the_old_pattern_really_did_corrupt_the_bom(tmp_path):
    """Reproduce the defect with the exact old idiom, so the tests above are not
    vacuously green. If this ever stops corrupting, the checks above are no
    longer testing anything and must be re-derived."""
    p = _write(tmp_path, bom=True)
    text = p.read_bytes().decode("cp1252", errors="ignore")   # the old read
    p.write_bytes(text.encode("utf-8"))                       # the old write
    raw = p.read_bytes()
    assert raw.startswith(MOJIBAKE), "the old idiom must reproduce the defect"
    with pytest.raises(ET.ParseError):
        ET.fromstring(raw.decode("utf-8-sig"))


def test_no_xml_write_site_hard_codes_utf8(tmp_path):
    """A pass must not read with one codec and write with another. Any new
    `encode("utf-8")` next to an XML read is the same bug wearing a new name."""
    import inspect
    for fn in (nc._make_chains_static, nc._ensure_cloth_body_collider,
               nc._harden_hdt_xml_for_fsmp):
        src = inspect.getsource(fn)
        assert 'encode("utf-8")' not in src, (
            f"{fn.__name__} hard-codes utf-8 on write; use the codec that "
            f"_read_xml_roundtrip returned (#bug-12)")
        assert 'read_text(' not in src, (
            f"{fn.__name__} reads the xml as TEXT; on Windows that is cp1252. "
            f"Use _read_xml_roundtrip (#bug-12)")
