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

"""The tip HARNESS and the tip FIX must not silently disagree about the region.

THE BUG THIS PINS. `nipple_clearance.py` scores `_body_nipple_weight >= 0.75 *
max` and its docstring asserted that this "is the SAME mask
`#authored-nipple-exempt` exempts in the converter ... this harness has to score
the exact region the fix acts on". It is not the same mask, three ways over:

    harness     TIP_WEIGHT_FRAC        0.75 * max
    converter   AUTHORED_NIPPLE_EXEMPT 0.50 * max
    converter   + AUTHORED_NIPPLE_RADIUS 2.0u and AUTHORED_NIPPLE_RINGS 2,
                dilated over the garment's own topology

Measured on the UBE body (peak weight 0.5635): 1020 verts scored against 1294
where the exemption starts -- a 274-vertex band (+27%) that the harness never
sees, before any dilation. A change confined to it moves the fix and not the row,
and the docstring said the opposite, so a null result there would have been read
as "the fix did nothing".

WHY THIS IS A TEST AND NOT A ONE-LINE DOC EDIT. The two constants live in
different files with no reference between them. Nothing made them move together
and nothing noticed when they did not. This is the `test_flag_default_prose`
shape: the prose is checked against the code that contradicts it.

DELIBERATELY NOT "ASSERT THEY ARE EQUAL". Setting the harness to 0.5 would void
every tip figure on record, all of which were taken at 0.75. The invariant is
that the difference stays DOCUMENTED, not that it stays zero.
"""
import re
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

HARNESS = REPO / "scripts" / "analysis" / "nipple_clearance.py"
SRC = HARNESS.read_text(encoding="utf-8")


def _scrubbed(expr: str) -> str:
    """Resolve in a subprocess with every CBBE2UBE_* dropped, so a developer's
    exported override cannot be mistaken for a default."""
    import os
    env = {k: v for k, v in os.environ.items() if not k.startswith("CBBE2UBE_")}
    env["PYTHONHASHSEED"] = "1"
    code = textwrap.dedent("""
        import sys
        sys.path.insert(0, %r)
        from src import nif_convert as nc
        from scripts.analysis import nipple_clearance as ncl
        print(repr(%s))
    """ % (str(REPO), expr))
    out = subprocess.run([sys.executable, "-c", code], env=env,
                         capture_output=True, text=True, cwd=str(REPO))
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_both_constants_resolve_to_what_the_docstring_says():
    assert _scrubbed("ncl.TIP_WEIGHT_FRAC") == "0.75"
    assert _scrubbed("nc.AUTHORED_NIPPLE_EXEMPT") == "0.5"
    assert _scrubbed("nc.AUTHORED_NIPPLE_RADIUS") == "2.0"
    assert _scrubbed("nc.AUTHORED_NIPPLE_RINGS") == "2"


def test_the_harness_does_not_claim_to_share_the_converters_mask():
    """The exact false sentence, and any close restatement of it."""
    lowered = " ".join(SRC.lower().split())
    assert "which is the same mask" not in lowered, (
        "the docstring is claiming the harness mask IS the converter's "
        "exemption mask again; they differ by 0.75 vs 0.50 of max plus a "
        "radius and a ring dilation")
    assert "score the exact region the fix acts on" not in lowered, (
        "the docstring is claiming this harness scores the exact region the "
        "fix acts on; it scores a strictly narrower one")


def test_the_harness_states_the_difference_it_has(self=None):
    """A silent narrower mask is the trap; a documented one is a limitation.
    Require the numbers, not just a hedge."""
    assert "NARROWER" in SRC, "the docstring no longer says it is narrower"
    for token in ("0.75", "0.50", "AUTHORED_NIPPLE_EXEMPT",
                  "AUTHORED_NIPPLE_RADIUS", "AUTHORED_NIPPLE_RINGS"):
        assert token in SRC, "the docstring stopped naming %s" % token


def test_the_prose_check_can_fail():
    """MUTATION CONTROL. A substring test that never matches anything would
    pass whatever the docstring said, so prove the needle is findable."""
    needle = "which is the same mask"
    fake = "The tip mask is X, %s the converter exempts." % needle
    assert needle in " ".join(fake.lower().split()), (
        "the matcher cannot find the phrase even when it is present")


def test_the_paired_block_cannot_re_gate_the_run():
    """`acceptance.tip()` greps `tighter\\s+(\\d+)` over this harness's WHOLE
    output and takes the FIRST match. The paired block added 2026-09-09 reports
    the same kind of count over a different population, so if it used the word
    "tighter" the gate could silently start judging the wrong number -- and the
    row it feeds (`pieces tighter at the tip`) is gated at zero."""
    assert "PAIRED on rays in front" in SRC, "the paired block is gone"
    # Only what it PRINTS can collide; the comment above it explains the hazard
    # and naturally has to name the word.
    block = SRC[SRC.index("# ---- PAIRED"):]
    emitted = "\n".join(ln for ln in block.splitlines()
                        if not ln.strip().startswith("#"))
    for word in ("tighter", "looser"):
        assert word not in emitted, (
            "the paired block EMITS the word %r, which `acceptance.tip()` "
            "greps for; it must stay distinct (closer/further/level)" % word)


def test_the_gate_still_reads_the_unpaired_count():
    """Control for the test above: prove the regex the gate uses still finds the
    ORIGINAL line, and finds it first."""
    import re
    sample = (
        "per piece, candidate minus control (negative = LESS room):\n"
        "  tighter 28   looser 32   flat 16   median delta +0.0013u\n"
        "   -0.1547u  a.nif\n"
        "\nPAIRED on rays in front in BOTH arms -- INFO, not the gated row:\n"
        "  per piece: closer 12   further 3   level 61   median paired -0.3u\n"
        "  tip rays with garment IN FRONT: control 371  candidate 473\n")
    m = re.search(r"tighter\s+(\d+)", sample)
    assert m and m.group(1) == "28", (
        "the gate's regex no longer resolves to the unpaired count")


def test_clearance_still_filters_and_full_does_not():
    """`clearance()` is what every recorded tip figure was taken with. It must
    keep dropping misses; `clearance_full` is the paired-safe sibling."""
    src = HARNESS.read_text(encoding="utf-8")
    assert "def clearance_full(" in src
    assert "return d, hit" in src, "clearance_full stopped returning the mask"
    assert "return d[hit] if int(hit.sum()) >= MIN_TIP_HITS else None" in src, (
        "clearance() changed what it filters -- every tip figure on record was "
        "taken with the old behaviour")


def test_the_constants_are_read_from_source_not_hardcoded_here():
    """If the harness stops defining TIP_WEIGHT_FRAC at module level, the
    subprocess assertions above would still pass off a stale import. Pin the
    binding itself."""
    assert re.search(r"^TIP_WEIGHT_FRAC\s*=\s*0\.75\s*$", SRC, re.M), (
        "TIP_WEIGHT_FRAC is no longer a module-level literal 0.75 -- if it "
        "moved, every recorded tip figure needs relabelling")
