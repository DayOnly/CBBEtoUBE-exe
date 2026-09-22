"""The Reference bodies dialog: its wiring into a conversion, and the dialog itself.

Before a conversion the user confirms, or changes, the CBBE and UBE bodies the
fit uses (#zeroed-body-refs). What must hold:
  * the choice reaches THIS run's child environment -- merged after
    apply_env (which pops registry variables at their default) and before the
    child starts -- and never os.environ, which would leak it into later runs;
  * nothing is locked before the dialog, so Cancel has nothing to undo, and
    both Cancel and OK re-arm Convert;
  * the dialog starts on the verified default, offers the others, confirms a
    non-default pick, re-checks the files before launching, and hands back
    the overrides.
The child env and the skip rule are module-level functions, tested by running
them; the closures inside launch_gui that only pass values along are pinned by
their source. The widget tests build the real dialog in a child process
(in-process Tk was flaky across the suite -- see test_gui_smoke).
"""
import inspect
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src import gui  # noqa: E402
from tests.test_gui_progress_and_cancel import _body  # noqa: E402
from tests.test_gui_smoke import needs_display  # noqa: E402

SRC = inspect.getsource(gui.launch_gui)


def test_the_child_env_carries_the_choice_over_settings_and_inherited_env(tmp_path):
    before = dict(os.environ)
    base = {"CBBE2UBE_UBE_BODY_1": "old_1.nif", "CBBE2UBE_UBE_BODY": "inherited.nif",
            "UNRELATED": "kept"}
    body_env = {"CBBE2UBE_UBE_BODY_1": "picked_1.nif", "CBBE2UBE_UBE_BODY_0": "picked_0.nif",
                "CBBE2UBE_UBE_BODY": ""}
    env = gui.child_env({"ube_body": "from_settings.nif"}, body_env,
                        str(tmp_path / "run.log"), base_env=base)
    assert env["CBBE2UBE_UBE_BODY_1"] == "picked_1.nif"
    assert env["CBBE2UBE_UBE_BODY"] == "", "the choice must win over the settings"
    assert env["UNRELATED"] == "kept"
    assert env["CBBE2UBE_NO_PAUSE"] == "1"
    assert env["CBBE2UBE_RUN_LOG"] == str(tmp_path / "run.log")
    assert dict(os.environ) == before, "the choice must never touch os.environ"
    # no choice at all (dry run, off-switch): exactly the settings env
    plain = gui.child_env({}, None, "x.log", base_env=base)
    assert plain["CBBE2UBE_UBE_BODY_1"] == "old_1.nif"


def test_the_worker_starts_the_child_with_that_env():
    w = _body(SRC, "def _worker(")
    i_env = w.index("env = child_env(state.get(\"settings\") or {}, body_env, log_path)")
    assert i_env < w.index("subprocess.Popen(")
    assert '"env": env' in w


def test_the_pick_travels_from_ok_to_the_worker():
    """Each hop only passes the value along; dropping it at any one silently
    converted with the resolver's body while the pane still listed the pick."""
    assert "on_ok(env, lines)" in _body(SRC, "def _accepted(")
    assert "args=(argv_run, body_env)" in _body(SRC, "def _launch(")
    assert "_open_body_dialog(_launch)" in _body(SRC, "def _start(")


def test_the_choice_never_goes_through_os_environ():
    writes = re.compile(r"os\.environ(?:\[([^\]]+)\]\s*=[^=]|\.(?:update|setdefault|pop)\()")
    bodies = [_body(SRC, h) for h in ("def _worker(", "def _launch(",
                                      "def _open_body_dialog(", "def _start(")]
    bodies += [inspect.getsource(gui.child_env), inspect.getsource(gui.build_body_dialog)]
    for body in bodies:
        for m in writes.finditer(body):
            assert m.group(1) == '"CBBE2UBE_NO_PAUSE"', f"os.environ write: {m.group(0)!r}"


def test_nothing_is_locked_before_the_bodies_are_confirmed():
    start = _body(SRC, "def _start(")
    assert 'state["running"] = True' not in start
    assert 'state["running"] = True' in _body(SRC, "def _launch(")


def test_cancel_and_ok_both_rearm_convert():
    """A Cancel that left the open-dialog guard set made every later Convert
    press do nothing for the rest of the session."""
    assert 'state["_body_dialog"] = False' in _body(SRC, "def _cancelled(")
    assert 'state["_body_dialog"] = False' in _body(SRC, "def _accepted(")
    assert 'if state.get("_body_dialog"):' in _body(SRC, "def _open_body_dialog(")


def test_the_off_switch_and_a_dry_run_skip_the_dialog():
    assert gui.body_dialog_wanted({}, False) is True
    assert gui.body_dialog_wanted({}, True) is False
    assert gui.body_dialog_wanted({"zeroed_body_refs": False}, False) is False
    start = _body(SRC, "def _start(")
    assert 'body_dialog_wanted(state.get("settings") or {}, dry.get())' in start
    assert "_launch({})" in start


_CHILD = textwrap.dedent("""
    import json, sys, tkinter as tk
    from pathlib import Path
    sys.path.insert(0, __PROJ__)
    from src import gui
    from src.zeroed_body import BodyCandidate, BodyListing
    tmp = Path(__TMP__)

    def pair(name):
        d = tmp / name
        d.mkdir(parents=True, exist_ok=True)
        for w in ("0", "1"):
            (d / ("femalebody_%s.nif" % w)).write_bytes(b"nif")
        return d / "femalebody_1.nif", d / "femalebody_0.nif"

    def cand(kind, prov, status, loads, sel=True, dev=0.0):
        p1, p0 = pair(kind + prov)
        reason = "" if status == "zeroed" else "not the zeroed build -- off by up to 1.971u"
        return BodyCandidate(kind, prov, p1, p0, loads, status, sel,
                             "Set" if status == "zeroed" else "", dev, reason)

    out = cand("cbbe", "Out", "zeroed", True)
    mod = cand("cbbe", "BodyMod", "not-zeroed", False, dev=1.971)
    other = cand("cbbe", "OtherFamily", "other-family", False, sel=False)
    ube = cand("ube", "Ube", "zeroed", True)
    listings = {"cbbe": BodyListing("cbbe", "CBBE 3BA", (out, mod, other), out, ()),
                "ube": BodyListing("ube", "UBE", (ube,), ube, ())}
    root = tk.Tk()
    root.withdraw()
    rec = {"ok": [], "asked": [], "errors": [], "cancelled": 0}

    def dialog(answer=True, base_env=None):
        h = gui.build_body_dialog(
            root, base_env=base_env or {}, resolve=lambda env: listings, run_async=False,
            on_ok=lambda env, lines: rec["ok"].append({"env": env, "lines": lines}),
            on_cancel=lambda: rec.__setitem__("cancelled", rec["cancelled"] + 1),
            confirm=lambda title, text, parent: rec["asked"].append(text) or answer,
            show_error=lambda title, text, parent: rec["errors"].append(text))
        root.update()
        return h

    def pick(h, i):
        cb, var, labels = h["combos"]["cbbe"]
        var.set(labels[i])

    report = {"mod_1": str(mod.path_1), "mod_0": str(mod.path_0), "ube_1": str(ube.path_1)}
    __SCENARIO__
    print("REPORT=" + json.dumps(report))
    root.destroy()
""")


def _run(tmp_path, scenario):
    code = (_CHILD.replace("__PROJ__", repr(str(PROJ)))
            .replace("__TMP__", repr(str(tmp_path)))
            .replace("__SCENARIO__", textwrap.dedent(scenario).strip("\n")))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       timeout=120, cwd=str(PROJ))
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("REPORT=")), None)
    assert line, f"dialog did not report (rc {r.returncode}):\n{r.stdout}\n{r.stderr}"
    return json.loads(line[len("REPORT="):])


@needs_display
def test_the_dialog_starts_on_the_default_and_hands_back_the_users_pick(tmp_path):
    rep = _run(tmp_path, """
        h = dialog()
        cb, var, labels = h["combos"]["cbbe"]
        report.update(start=var.get(), labels=labels,
                      ube_start=h["combos"]["ube"][1].get())
        pick(h, 1)                          # the preset build instead
        h["ok"].invoke()
        report.update(rec)
    """)
    assert rep["start"] == "[zeroed] Out  (the game loads it)"
    assert rep["labels"] == ["[zeroed] Out  (the game loads it)",
                             "[NOT zeroed, off by up to 1.97u] BodyMod"], \
        "the other-family body must not be offered"
    assert rep["ube_start"] == "[zeroed] Ube  (the game loads it)"
    assert len(rep["asked"]) == 1 and "off by up to 1.971u" in rep["asked"][0], \
        "a non-default pick is confirmed, naming its deviation"
    (ok,) = rep["ok"]
    assert ok["env"]["CBBE2UBE_CBBE_BODY_1"] == rep["mod_1"]
    assert ok["env"]["CBBE2UBE_UBE_BODY_1"] == rep["ube_1"]
    assert set(ok["env"]) == {"CBBE2UBE_CBBE_BODY_0", "CBBE2UBE_CBBE_BODY_1",
                              "CBBE2UBE_UBE_BODY_0", "CBBE2UBE_UBE_BODY_1"}


@needs_display
def test_answering_no_to_the_second_look_launches_nothing(tmp_path):
    rep = _run(tmp_path, """
        h = dialog(answer=False)
        pick(h, 1)
        h["ok"].invoke()
        report.update(rec, still_open=bool(h["win"].winfo_exists()))
    """)
    assert len(rep["asked"]) == 1
    assert rep["ok"] == [] and rep["still_open"]


@needs_display
def test_cancel_launches_nothing_and_says_so(tmp_path):
    rep = _run(tmp_path, """
        h = dialog()
        h["cancel"].invoke()
        report.update(rec)
    """)
    assert rep["ok"] == [] and rep["cancelled"] == 1


@needs_display
def test_a_body_deleted_since_the_check_stops_the_launch(tmp_path):
    rep = _run(tmp_path, """
        h = dialog()
        pick(h, 1)
        Path(report["mod_0"]).unlink()
        h["ok"].invoke()
        report.update(rec)
    """)
    assert rep["ok"] == []
    assert len(rep["errors"]) == 1 and rep["mod_0"] in rep["errors"][0]


@needs_display
def test_a_settings_body_the_run_will_not_use_is_named_before_launch(tmp_path):
    """The default needs no second look -- unless it replaces a body the user
    set in Settings, which the run would otherwise drop without a word."""
    rep = _run(tmp_path, """
        h = dialog(base_env={"CBBE2UBE_UBE_BODY": str(tmp / "mine" / "femalebody_tangent_1.nif")})
        h["ok"].invoke()
        report.update(rec)
    """)
    assert len(rep["asked"]) == 1
    assert "CBBE2UBE_UBE_BODY = " in rep["asked"][0]
    assert "is not used for this run" in rep["asked"][0]
    assert len(rep["ok"]) == 1
