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

"""The guard that stops the 2026-08-02 leak happening again.

Three things reached main's history and needed a full rewrite to remove: a
working-note file that names mods, absolute paths identifying a machine, and
personal email addresses in commit metadata. The suite already checked the tip,
but a commit is too late -- a deleted file is still fetched by every clone and
still served by SHA afterwards.

So the rules moved into `scripts/repo_hygiene.py` and the git hooks enforce them
BEFORE the object exists. These tests cover the rules and the wiring; the hooks
were also verified end-to-end by planting each violation and watching the commit
be refused.
"""
import subprocess
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from scripts import repo_hygiene as H  # noqa: E402


def test_hook_files_exist_and_are_wired():
    for name in ("pre-commit", "commit-msg"):
        assert (PROJ / ".githooks" / name).is_file(), f"missing hook: {name}"
    for body in ("hook_precommit.py", "hook_commitmsg.py", "repo_hygiene.py"):
        assert (PROJ / "scripts" / body).is_file(), f"missing hook body: {body}"


def test_hooks_and_tests_share_one_rule_set():
    """Two copies of one rule drift. The test module must not restate them."""
    src = (PROJ / "tests" / "test_public_repo_hygiene.py").read_text(
        encoding="utf-8")
    assert "from scripts import repo_hygiene" in src
    assert "NEVER_TRACKED = (" not in src, "rules restated instead of imported"


def test_never_track_rule_catches_the_files_that_actually_leaked():
    for name in ("CLIPPING_LOG.md", "CONVERTER_AUDIT_2026-07-04.md",
                 "ARMOR_WORKLIST.md", "ube_providers.txt",
                 "CBBEtoUBE_settings.json", "golden/pieces.json"):
        assert H.path_is_never_tracked(name), f"{name} would slip through"
    for ok in ("src/nif_convert.py", "docs/DESIGN.md", "tests/test_x.py"):
        assert H.path_is_never_tracked(ok) is None, f"false positive on {ok}"


def test_content_rule_catches_a_machine_path_but_not_a_generic_one():
    assert H.scan_text("scripts/x.py", r'root = r"<MODLIST_ROOT>\mods"')
    assert H.scan_text("scripts/x.py", r'p = "C:/Users/realname/Downloads/a"')
    assert not H.scan_text("docs/a.md", r"set it to C:\Games\Skyrim\Data")
    assert not H.scan_text("docs/a.md", r"e.g. <MO2Root>\mods")
    assert not H.scan_text("tests/t.py", r'f(r"C:\Users\someone\.ssh\id_rsa")')


def test_content_rule_catches_a_personal_email():
    """The audit document itself published two addresses as 'findings'."""
    assert H.scan_text("docs/a.md", "contact person@gmail.com for details")
    assert not H.scan_text("docs/a.md", "DayOnly@users.noreply.github.com")
    assert not H.scan_text("docs/a.md", "REDACTED@example.invalid")


def test_message_rule_catches_a_deploy_path():
    assert H.scan_message(r"dist: deploy to <MODLIST_ROOT>\tools\CBBEtoUBE")
    assert H.scan_message("ping person@gmail.com about it")
    assert not H.scan_message("dist: rebuild and deploy to the tools folder")
    # a Co-Authored-By trailer is a service address, not a person -- the first
    # version of this rule refused its own commit message over one
    assert not H.scan_message(
        "feat: x\n\nCo-Authored-By: A B <noreply@anthropic.com>")
    # git's own comment lines are not the author's message
    assert not H.scan_message("# On branch main\n" + r"# path: <MODLIST_ROOT>\x")


def test_identity_rule_rejects_a_personal_address():
    assert H.check_identity("someone@gmail.com")
    assert H.check_identity("") 
    assert H.check_identity("DayOnly@users.noreply.github.com") is None
    assert H.check_identity("noreply@github.com") is None
    assert H.check_identity("noreply@anthropic.com") is None


def test_a_configured_identity_is_not_a_personal_address():
    """If this checkout CAN commit, its identity must not be personal.

    Skips when no identity is set: that is a CI runner, which never commits, and
    the first version of this test failed all three matrix jobs by asserting a
    developer-machine property in an environment that has none. The hook still
    rejects an unset identity at commit time, where it actually matters.
    """
    email = subprocess.run(["git", "-C", str(PROJ), "config", "user.email"],
                           capture_output=True, text=True).stdout.strip()
    if not email:
        import pytest
        pytest.skip("no commit identity configured (CI runner)")
    assert H.check_identity(email) is None, (
        f"this checkout would publish {email!r} on every commit")


def test_content_exemptions_stay_short_and_are_all_controls():
    """Every exemption is a hole in the rule. Each is only safe because the
    file's whole job is to hold the strings the rule rejects -- so the list must
    stay tiny and must never grow to cover ordinary source."""
    assert len(H.CONTENT_EXEMPT) <= 4, "exemption list is growing; justify each"
    for path in H.CONTENT_EXEMPT:
        assert "hygiene" in path, f"{path} is not a hygiene control file"
        assert (PROJ / path).is_file(), f"exemption names a missing file: {path}"
