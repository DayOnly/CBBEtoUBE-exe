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
    for name in ("pre-commit", "commit-msg", "pre-push", "pre-merge-commit"):
        assert (PROJ / ".githooks" / name).is_file(), f"missing hook: {name}"
    for body in ("hook_precommit.py", "hook_commitmsg.py", "hook_prepush.py",
                 "repo_hygiene.py"):
        assert (PROJ / "scripts" / body).is_file(), f"missing hook body: {body}"
    # A hook file that runs nothing exists and guards nothing. #prepush-range
    wired = {"pre-commit": "scripts/hook_precommit.py",
             "commit-msg": "scripts/hook_commitmsg.py",
             "pre-push": "scripts/hook_prepush.py",
             "pre-merge-commit": ".githooks/pre-commit"}
    for name, target in wired.items():
        text = (PROJ / ".githooks" / name).read_text(encoding="utf-8")
        assert target in text, f".githooks/{name} does not run {target}"


# --- #prepush-range: every commit a push would publish, whatever made it ------

def _prepush_repo(tmp_path):
    """A throwaway repository holding the hook scripts and a synthetic denylist.
    Identity is passed per command, so a CI runner that has none still runs this."""
    import os
    import shutil

    import pytest
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    for name in ("__init__.py", "hook_precommit.py", "hook_prepush.py",
                 "repo_hygiene.py", "release_gate.py", "build_identity.py"):
        shutil.copy2(PROJ / "scripts" / name, repo / "scripts" / name)
    (repo / H.DENYLIST_FILE).write_text("re:sure\\s+heart\n", encoding="utf-8")

    def git(*args, email="noreply@example.invalid"):
        env = dict(os.environ, GIT_AUTHOR_NAME="hook test", GIT_AUTHOR_EMAIL=email,
                   GIT_COMMITTER_NAME="hook test",
                   GIT_COMMITTER_EMAIL="noreply@example.invalid")
        # core.longpaths: under a deep tmp_path, `git init` and `git rebase` hit the
        # 260-character path limit ("Filename too long"), and this file failed a full
        # suite run on 2026-09-15 for a reason unrelated to the hooks.
        return subprocess.run(["git", "-C", str(repo), "-c", "core.autocrlf=false",
                               "-c", "core.longpaths=true", *args],
                              check=True, capture_output=True, text=True, env=env).stdout

    def commit(path, text, msg, email="noreply@example.invalid"):
        (repo / path).write_bytes(text.encode("utf-8"))
        git("add", path)
        git("commit", "-q", "-m", msg, email=email)

    git("init", "-q")
    return repo, git, commit


def _push(repo, git, lines=None):
    tip = git("rev-parse", "HEAD").strip()
    stdin = lines if lines is not None else f"refs/heads/lane {tip} refs/heads/lane {'0' * 40}\n"
    return subprocess.run([sys.executable, str(repo / "scripts" / "hook_prepush.py"),
                           "origin", "https://example.invalid/repo.git"],
                          input=stdin, cwd=str(repo), capture_output=True, text=True)


def test_prepush_refuses_a_rebased_commit_whose_message_names_an_asset(tmp_path):
    """A rebase replays commits without pre-commit or commit-msg (measured, plan
    J-04), so a lane committed on a machine without the denylist reaches the push
    unchecked. The pre-push scan reads the range, whatever produced it."""
    repo, git, commit = _prepush_repo(tmp_path)
    commit("a.txt", "plain\n", "base")
    trunk = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    git("checkout", "-q", "-b", "lane")
    commit("b.txt", "plain too\n", "fix: the Sure heart cuirass")
    git("checkout", "-q", trunk)
    commit("c.txt", "other\n", "unrelated")
    git("checkout", "-q", "lane")
    git("rebase", "-q", trunk)
    r = _push(repo, git)
    assert r.returncode == 1, r.stderr[-1500:]
    assert "PUSH BLOCKED" in r.stderr and "denylisted asset name" in r.stderr, r.stderr[-1500:]


def test_prepush_reads_every_commit_not_just_the_tip(tmp_path):
    """A file added in one commit and deleted in the next is still published."""
    repo, git, commit = _prepush_repo(tmp_path)
    commit("a.txt", "plain\n", "base")
    commit("notes.txt", "the Sure heart piece\n", "add notes")
    git("rm", "-q", "notes.txt")
    git("commit", "-q", "-m", "remove notes")
    r = _push(repo, git)
    assert r.returncode == 1 and "notes.txt" in r.stderr, r.stderr[-1500:]


def test_prepush_passes_a_clean_range_and_skips_what_the_remote_has(tmp_path):
    repo, git, commit = _prepush_repo(tmp_path)
    commit("a.txt", "plain\n", "fix: the Sure heart cuirass")      # already published
    git("update-ref", "refs/remotes/origin/lane", "HEAD")
    commit("b.txt", "plain\n", "a clean change")
    r = _push(repo, git)
    assert r.returncode == 0, r.stderr[-1500:]
    deleted = _push(repo, git, lines=f"(delete) {'0' * 40} refs/heads/gone {'1' * 40}\n")
    assert deleted.returncode == 0, deleted.stderr[-1500:]


def test_prepush_refuses_a_personal_author_address(tmp_path):
    repo, git, commit = _prepush_repo(tmp_path)
    personal = "someone.personal" + "@" + "gmail.com"          # built, not a literal
    commit("a.txt", "plain\n", "base", email=personal)
    r = _push(repo, git)
    assert r.returncode == 1 and "personal address" in r.stderr, r.stderr[-1500:]


def test_prepush_blocks_when_git_cannot_read_the_range(tmp_path):
    """A git failure is not an empty range. The hooks discarded every git exit
    code, so under a checkout path past the 260-character limit -- where each call
    exited 128, "Filename too long" -- pre-push returned 0 on a commit it had to
    refuse (measured 2026-09-15). A sha git cannot resolve fails the same way at
    any depth. #hook-fail-closed"""
    repo, git, commit = _prepush_repo(tmp_path)
    commit("a.txt", "plain\n", "base")
    r = _push(repo, git, lines=f"refs/heads/lane {'1' * 40} refs/heads/lane {'0' * 40}\n")
    assert r.returncode == 1, r.stderr[-1500:]
    assert "PUSH BLOCKED" in r.stderr and "NOTHING was checked" in r.stderr, r.stderr[-1500:]


def test_precommit_blocks_when_git_fails_but_not_on_an_unset_email(tmp_path):
    """Outside a repository `git diff --cached` exits 129, and its empty output
    read as nothing staged, so the commit passed. `git config user.email` exits 1
    when the address is unset -- check_identity's finding, not a git failure.
    #hook-fail-closed"""
    import os
    import shutil
    hook = tmp_path / "hook"
    (hook / "scripts").mkdir(parents=True)
    for name in ("__init__.py", "hook_precommit.py", "repo_hygiene.py",
                 "release_gate.py", "build_identity.py"):
        shutil.copy2(PROJ / "scripts" / name, hook / "scripts" / name)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(tmp_path / "no-global"),
               GIT_CEILING_DIRECTORIES=str(tmp_path))

    def precommit(cwd, **extra):
        return subprocess.run([sys.executable, str(hook / "scripts" / "hook_precommit.py")],
                              cwd=str(cwd), env=dict(env, **extra),
                              capture_output=True, text=True)

    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    r = precommit(outside, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="user.email",
                  GIT_CONFIG_VALUE_0="noreply@example.invalid")
    assert r.returncode == 1, r.stderr[-1500:]
    assert "COMMIT BLOCKED" in r.stderr and "NOTHING was checked" in r.stderr, r.stderr[-1500:]

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "-c", "core.longpaths=true", "init", "-q"],
                   check=True, capture_output=True, env=env)
    r = precommit(repo)
    assert r.returncode == 1 and "git user.email is unset" in r.stderr, r.stderr[-1500:]
    assert "NOTHING was checked" not in r.stderr, r.stderr[-1500:]


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


# The sample modlist is deliberately NOT the real one. These controls must hold
# a path the rule MATCHES, so a history scrub targeting the real name would
# rewrite them and silently turn the control into an assertion about a
# placeholder -- which is exactly what happened on the first scrub attempt.
def test_content_rule_catches_a_machine_path_but_not_a_generic_one():
    assert H.scan_text("scripts/x.py", r'root = r"D:\Modlists\Somelist\mods"')
    assert H.scan_text("scripts/x.py", r'p = "C:/Users/realname/Downloads/a"')
    assert not H.scan_text("docs/a.md", r"set it to C:\Games\Skyrim\Data")
    assert not H.scan_text("docs/a.md", r"e.g. <MO2Root>\mods")
    assert not H.scan_text("tests/t.py", r'f(r"C:\Users\someone\.ssh\id_rsa")')


def test_a_placeholder_elsewhere_on_the_line_does_not_exempt_a_real_path():
    """The placeholder exemption looked at the whole line, so a worklog line that
    quoted a real instance path scanned clean because the same line also named
    `$Mo2Root` and a `<local-path>` marker. It looks inside the matched path now.
    #placeholder-token"""
    real = r'dest D:\Modlists\Somelist\tools -- Join-Path $Mo2Root "tools" -> <local-path>'
    assert H.scan_text("docs/a.md", real)
    assert H.scan_message(real)
    assert H.scan_text("docs/a.md", r'basedir = "D:\Modlists\Somelist\mods"<MO2Root>'), (
        "the path token ends at the quote")
    # a placeholder INSIDE the path still exempts it, spaces in a marker included
    assert not H.scan_text("docs/a.md", r'basedir = "D:\Modlists\<real list name>\mods\x"')
    assert not H.scan_message(r"deploy to D:\Modlists\<list>\tools")
    assert not H.scan_text("docs/a.md", r"C:\Users\you\AppData\Local")


def test_content_rule_catches_a_personal_email():
    """The audit document itself published two addresses as 'findings'."""
    assert H.scan_text("docs/a.md", "contact person@gmail.com for details")
    assert not H.scan_text("docs/a.md", "DayOnly@users.noreply.github.com")
    assert not H.scan_text("docs/a.md", "REDACTED@example.invalid")


def test_message_rule_catches_a_deploy_path():
    assert H.scan_message(r"dist: deploy to D:\Modlists\Somelist\tools\CBBEtoUBE")
    assert H.scan_message("ping person@gmail.com about it")
    assert not H.scan_message("dist: rebuild and deploy to the tools folder")
    # a Co-Authored-By trailer is a service address, not a person -- the first
    # version of this rule refused its own commit message over one
    assert not H.scan_message(
        "feat: x\n\nCo-Authored-By: A B <noreply@anthropic.com>")
    # git's own comment lines are not the author's message
    assert not H.scan_message("# On branch main\n" + r"# path: D:\Modlists\Somelist\x")


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


# --- BUG-05(a): dist/ was un-exempted but the SUFFIX GATE kept it invisible ---
# The rules reached 6 of 1127 tracked dist/ files. PyInstaller writes most
# members with no suffix at all, so "dist/ is not exempt" was true and
# meaningless at the same time. These tests are the mutation proof the bug entry
# demanded: plant the violation, prove the guard names it.

BAKED_PATH = r'basedir = "D:\Modlists\Somelist\mods\CBBEtoUBE Auto"'


def test_a_baked_build_path_in_dist_is_caught_whatever_the_suffix():
    """The real leak risk for our committed build output: a build-machine path.
    Every one of these returned [] before the gate stopped being suffix-based."""
    for rel in ("dist/CBBEtoUBE/_internal/base_library.zip.manifest",
                "dist/CBBEtoUBE/CBBEtoUBE.exe.manifest",
                "dist/CBBEtoUBE/_internal/no_suffix_member"):
        assert H.scan_text(rel, BAKED_PATH), (
            f"a baked build-machine path in {rel} is not caught")


def test_the_vendor_email_exemption_is_scoped_to_emails_only():
    """The exemption exists so a rebuild does not fail hygiene on upstream's own
    author addresses. It must NOT switch off the path rule, which is the rule
    that actually protects dist/."""
    vendor = "dist/CBBEtoUBE/_internal/_tk_data/console.tcl"
    assert not H.scan_text(vendor, "contact someone@gmail.com"), (
        "vendor email should be exempt")
    assert H.scan_text(vendor, BAKED_PATH), (
        "the vendor exemption must not disable the LOCAL PATH rule")


def test_the_bundles_licence_texts_are_email_exempt_but_still_path_checked():
    """The build ships CPython's, SciPy's and Tcl/Tk's licence texts, and CPython's
    names the authors of what it bundles -- 9 address lines, which refused the
    bundle at the pre-commit hook. Upstream licence text is what the vendor
    exemption is for; the path rule, which is what actually protects dist/, stays
    on. #bundled-licences"""
    lic = "dist/CBBEtoUBE/_internal/licenses/CPython-LICENSE.txt"
    assert not H.scan_text(lic, "contact someone@gmail.com"), (
        "an upstream licence's author addresses must not refuse the bundle")
    assert H.scan_text(lic, BAKED_PATH), (
        "the local-path rule must still apply inside the licence folder")


def test_our_own_dist_files_are_still_email_checked():
    """Only vendored trees are exempt. Anything else under dist/ is ours."""
    assert H.scan_text("dist/CBBEtoUBE/_internal/ours.dat",
                       "contact someone@gmail.com"), (
        "a personal address in OUR build output must still be caught")


def test_binaries_are_dropped_before_the_rules_see_them():
    """Scanning is no longer gated on suffix, so .pyd/.dll members reach the
    readers. Decoding those with errors='replace' would match rules on noise."""
    assert H.is_binary(b"MZ\x90\x00\x03\x00\x00\x00")
    assert not H.is_binary(b"plain text, no NULs")


def test_the_hook_and_the_test_share_one_gate():
    """The hook kept its own copy of the suffix test, so widening coverage in
    repo_hygiene left the hook behind -- in the one place it matters most."""
    src = (PROJ / "scripts" / "hook_precommit.py").read_text(encoding="utf8")
    assert "should_scan" in src, "the hook must use the shared gate"
    assert "TEXT_SUFFIXES" not in src, (
        "the hook is deciding scannability on its own again")


def test_a_name_wrapped_across_a_line_break_is_still_caught(tmp_path):
    """#bundle-names. The per-line scan passed a name split by a line wrap, while
    the compiled docstring holding it is one string -- and shipped in the exe."""
    (tmp_path / H.DENYLIST_FILE).write_text("re:sure\\s+heart\n", encoding="utf-8")
    pattern, _count = H.load_denylist(tmp_path)
    hits = H.scan_names("src/x.py", "first line\nthe Sure\nheart cuirass\n", pattern)
    assert len(hits) == 1 and hits[0].startswith("src/x.py:2:"), hits
    assert H.scan_message("fix: the Sure\nheart piece", pattern)
    # One comment line holding the whole name: unblanked it WOULD match. (Two
    # `#` lines wrapping it never could -- the `# ` between the words is not
    # whitespace -- so that version passed with the blanking removed.)
    assert not H.scan_message("fix: x\n# the Sure heart piece", pattern), (
        "git's comment lines are not the author's message")


def test_the_bundle_scan_reads_docstrings_nested_in_functions():
    import re

    from scripts import release_gate as rg
    code = compile('def f():\n    """the Sure\n    heart cuirass"""\n', "src/fake.py", "exec")
    pattern = re.compile(r"sure\s+heart", re.IGNORECASE)
    assert rg.string_constant_hits({"src.fake": code, "src.absent": None}, pattern) == [
        ("src.fake", "Sure\n    heart")]


def test_the_hook_refuses_a_staged_exe_whose_bundle_names_an_asset(tmp_path):
    """End to end on a copy of the tracked exe. Its bundled blas_env module's
    docstring says OpenBLAS, so a synthetic denylist naming that must stop the
    commit, and one naming nothing in the bundle must not."""
    import shutil

    import pytest
    exe = PROJ / "dist" / "CBBEtoUBE" / "CBBEtoUBE.exe"
    if shutil.which("git") is None or not exe.is_file():
        pytest.skip("needs git and the tracked exe")
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    for name in ("__init__.py", "hook_precommit.py", "repo_hygiene.py",
                 "release_gate.py", "build_identity.py"):
        shutil.copy2(PROJ / "scripts" / name, repo / "scripts" / name)
    (repo / "dist" / "CBBEtoUBE").mkdir(parents=True)
    shutil.copy2(exe, repo / "dist" / "CBBEtoUBE" / "CBBEtoUBE.exe")

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    def hook():
        return subprocess.run([sys.executable, str(repo / "scripts" / "hook_precommit.py")],
                              cwd=str(repo), capture_output=True, text=True)

    git("init", "-q")
    git("config", "core.autocrlf", "false")
    git("config", "user.email", "noreply@example.invalid")
    git("config", "user.name", "hook test")
    git("add", "dist")
    (repo / H.DENYLIST_FILE).write_text("OpenBLAS\n", encoding="utf-8")
    r = hook()
    assert r.returncode == 1 and "bundled module src.blas_env" in r.stderr, r.stderr[-1500:]
    (repo / H.DENYLIST_FILE).write_text("NoSuchAssetNameAnywhere\n", encoding="utf-8")
    r = hook()
    assert r.returncode == 0, r.stderr[-1500:]


def test_the_hook_refuses_a_staged_crlf_file(tmp_path):
    """#lf-only, end to end on a throwaway repository: the hook judges the
    STAGED bytes, refuses a CRLF, leaves dist/ alone, and passes once fixed."""
    import shutil

    import pytest
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    for name in ("__init__.py", "hook_precommit.py", "repo_hygiene.py"):
        shutil.copy2(PROJ / "scripts" / name, repo / "scripts" / name)

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    def hook():
        return subprocess.run([sys.executable, str(repo / "scripts" / "hook_precommit.py")],
                              cwd=str(repo), capture_output=True, text=True)

    git("init", "-q")
    # A Windows runner defaults to autocrlf=true, which would convert on `add`
    # and hand the hook LF bytes: the test would pass without the rule.
    git("config", "core.autocrlf", "false")
    git("config", "user.email", "noreply@example.invalid")
    git("config", "user.name", "hook test")
    (repo / "tool.py").write_bytes(b"a = 1\r\nb = 2\r\n")
    (repo / "dist" / "CBBEtoUBE").mkdir(parents=True)
    (repo / "dist" / "CBBEtoUBE" / "LICENSE.txt").write_bytes(b"upstream text\r\n")
    git("add", "tool.py", "dist")
    r = hook()
    assert r.returncode == 1 and "tool.py: 2 CRLF" in r.stderr, r.stderr
    assert "LICENSE.txt" not in r.stderr, "dist/ keeps its upstream endings"
    (repo / "tool.py").write_bytes(b"a = 1\nb = 2\n")      # fixed on disk, NOT staged
    r = hook()
    assert r.returncode == 1 and "tool.py: 2 CRLF" in r.stderr, (
        "the hook judged the working tree, not what is about to be committed")
    git("add", "tool.py")
    r = hook()
    assert r.returncode == 0, r.stderr


def test_the_message_rule_checks_asset_names_too(tmp_path):
    """The commit that INTRODUCED the asset-name rule leaked a real name in its
    own message. The rule had been wired into content scanning and not here, and
    a message is the more expensive of the two to fix -- it cannot be changed
    afterwards without rewriting history.

    A synthetic denylist, so this file names nothing real.
    """
    (tmp_path / H.DENYLIST_FILE).write_text(
        "sure\n", encoding="utf-8")
    pattern, count = H.load_denylist(tmp_path)
    assert count == 1

    assert H.scan_message("catches SureheartCuirass and not measured", pattern)
    # ...and the same message is clean when no denylist was supplied, which is
    # exactly how the leak got through
    assert not H.scan_message("catches SureheartCuirass and not measured")
    assert not H.scan_message("an ordinary message about a measured value",
                              pattern)


def test_the_commit_msg_hook_actually_loads_the_denylist():
    """A rule wired into `scan_message` but not passed by the hook is a rule
    that never fires on a real commit -- the same duplicate/omission shape as
    the pre-commit gate above."""
    src = (PROJ / "scripts" / "hook_commitmsg.py").read_text(encoding="utf8")
    assert "load_denylist" in src, (
        "the commit-msg hook must load the denylist and pass it to scan_message")
    assert "scan_message(msg, denylist)" in src, (
        "the commit-msg hook loads the denylist but does not pass it on")
