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

"""`#hdt-xml-sanitise` -- repair an authored physics XML that is malformed
OUTSIDE the root element, and repair NOTHING else.

TEN authored XMLs in the reference modlist end with junk after the root close
(`</system>undefined</xml>` or `</system></xml>`). XML forbids non-whitespace
after the document element, so a strict parser rejects the whole file; the
converter then copies it VERBATIM, and 94 shipped NIFs referenced an XML that
nothing could read. An empty collider set is the condition BUG-00 recorded as
disarming every physics guard at once.

WHAT THESE TESTS HOLD, in order of what would hurt most if it broke:
  * a well-formed file is returned UNCHANGED and unflagged -- the pass must be
    a true no-op on the 170 XMLs that were already fine;
  * a file it cannot fix is returned UNCHANGED rather than half-mangled;
  * the repair NEVER touches the declarations (bones / colliders / cloth), so
    it cannot change what the physics says;
  * it works on BYTES with no transcode. Decoding and re-encoding one of these
    is exactly how BUG-12 double-encoded a BOM and made eight XMLs unparseable,
    so a non-UTF-8 payload must survive byte-for-byte.
"""
import xml.etree.ElementTree as ET

from src.hdt_xml_gen import sanitise_hdt_xml_bytes

GOOD = (b'<system>\n  <bone name="A"/>\n  <per-triangle-shape name="Col"/>\n'
        b'</system>\n')


def test_wellformed_is_untouched():
    out, note = sanitise_hdt_xml_bytes(GOOD)
    assert out == GOOD
    assert note is None


def test_undefined_tail_is_repaired():
    bad = GOOD.rstrip(b"\n") + b"undefined</xml>\n"
    out, note = sanitise_hdt_xml_bytes(bad)
    assert note is not None
    ET.fromstring(out)                      # parses
    assert b"undefined" not in out
    assert b"</xml>" not in out


def test_stray_close_tag_tail_is_repaired():
    bad = GOOD.rstrip(b"\n") + b"\n</xml>\n  \n"
    out, note = sanitise_hdt_xml_bytes(bad)
    assert note is not None
    ET.fromstring(out)


def test_declarations_are_preserved_exactly():
    """The repair may only remove bytes AFTER the root close, so everything the
    XML declares must survive untouched."""
    bad = GOOD.rstrip(b"\n") + b"undefined</xml>\n"
    out, _ = sanitise_hdt_xml_bytes(bad)
    before = ET.fromstring(GOOD)
    after = ET.fromstring(out)
    assert [e.tag for e in before] == [e.tag for e in after]
    assert ([e.get("name") for e in before]
            == [e.get("name") for e in after])


def test_damage_inside_the_root_is_NOT_touched():
    """Only out-of-root damage is in scope. A file broken INSIDE the document
    cannot be repaired by truncation, and half-fixing it would be worse than
    leaving it -- so it must come back byte-identical."""
    broken = b'<system>\n  <bone name="A">\n</system>\n'   # unclosed <bone>
    out, note = sanitise_hdt_xml_bytes(broken)
    assert out == broken
    assert note is None


def test_no_transcode_on_non_utf8_payload():
    """BUG-12: decoding then re-encoding these files double-encoded a BOM and
    made eight of them unparseable. Truncation needs no decode, so a cp1252
    byte must survive verbatim."""
    latin = ('<system>\n  <bone name="café"/>\n</system>\n'
             ).encode("cp1252")
    bad = latin.rstrip(b"\n") + b"undefined</xml>\n"
    out, note = sanitise_hdt_xml_bytes(bad)
    assert note is not None
    assert out == latin.rstrip(b"\n") + b"\n"
    assert bytes([0xE9]) in out             # the cp1252 e-acute, unchanged


def test_flag_is_default_off():
    from src import nif_convert as nc
    assert nc.HDT_XML_SANITISE is False


def test_shim_never_raises():
    """A sanitiser that throws must not take a conversion with it."""
    from src import nif_convert as nc
    for junk in (b"", b"not xml at all", b"<", b"\x00\x01\x02"):
        out, note = nc._hdt_sanitise(junk)
        assert out == junk
        assert note is None
