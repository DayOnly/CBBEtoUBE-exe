"""Records appended by several processes at once must land whole.

Every pool worker appends to the run's one `standoff_audit.jsonl`. A plain
append-mode write is seek-then-write in the C runtime, so two workers could
write at the same offset: a full run tore 14-16 lines and lost ~30 records,
differently each run. #atomic-audit-append writes each record as ONE write
under a cross-process lock; CBBE2UBE_NO_ATOMIC_AUDIT_APPEND=1 restores the old
writer. The readers still meet torn lines in sinks written before, and must
count them rather than drop them silently.
"""
import json
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import atomic_io, fit_metrics  # noqa: E402
from scripts.analysis import physics_rest_depth as prd  # noqa: E402
from scripts.analysis import survival_report as sr  # noqa: E402

WORKERS, RECORDS = 8, 500

# One writer process: waits for the start file so all eight overlap, then
# appends RECORDS records of varying length through the converter's own sink.
_CHILD = r"""
import os, sys, time
sys.path.insert(0, sys.argv[1])
from src import fit_metrics
wid, go = int(sys.argv[2]), sys.argv[3]
while not os.path.exists(go):
    time.sleep(0.001)
for i in range(%d):
    pad = "x" * ((i * 7919 + wid * 104729) %% 20000)
    fit_metrics._append("out/meshes/set/piece_1.nif",
                        {"kind": "stress", "w": wid, "i": i, "pad": pad})
""" % RECORDS


@pytest.fixture
def sink(tmp_path, monkeypatch):
    p = tmp_path / "standoff_audit.jsonl"
    monkeypatch.setenv("CBBE2UBE_STANDOFF_LOG", str(p))
    monkeypatch.delenv("CBBE2UBE_NO_STANDOFF_AUDIT", raising=False)
    monkeypatch.delenv("CBBE2UBE_NO_ATOMIC_AUDIT_APPEND", raising=False)
    return p


def test_eight_processes_append_every_record_whole(sink, tmp_path):
    """8 processes x 500 records of 0-20 KB: every line parses, none is lost,
    none is doubled. Unlocked, the same run tore ~800 lines per trial."""
    go = tmp_path / "go"
    env = dict(os.environ)
    procs = [subprocess.Popen([sys.executable, "-B", "-c", _CHILD, str(_REPO),
                               str(w), str(go)], env=env)
             for w in range(WORKERS)]
    time.sleep(1.0)                 # let every child reach the start line
    go.write_bytes(b"")
    for p in procs:
        assert p.wait(timeout=300) == 0
    seen, torn = Counter(), 0
    with open(sink, "rb") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                torn += 1
                continue
            assert len(rec["pad"]) == (rec["i"] * 7919
                                       + rec["w"] * 104729) % 20000
            seen[(rec["w"], rec["i"])] += 1
    assert torn == 0, f"{torn} torn line(s)"
    assert sum(seen.values()) == WORKERS * RECORDS
    assert set(seen) == {(w, i) for w in range(WORKERS)
                         for i in range(RECORDS)}
    assert max(seen.values()) == 1


def test_the_bytes_are_the_old_writers(tmp_path, monkeypatch):
    """Same UTF-8, same line ending as the text-mode append it replaces: a
    reader of the sink or the glow log sees no difference but the tears."""
    texts = [json.dumps({"kind": "frame", "s": "é"}) + "\n",
             "\n=== block\n    two\n", "lone \udc80 surrogate\n"]
    got = {}
    for arm in ("new", "old"):
        if arm == "old":
            monkeypatch.setenv("CBBE2UBE_NO_ATOMIC_AUDIT_APPEND", "1")
        else:
            monkeypatch.delenv("CBBE2UBE_NO_ATOMIC_AUDIT_APPEND", raising=False)
        p = tmp_path / f"{arm}.log"
        for t in texts:
            atomic_io.append_whole(p, t, errors="replace")
        got[arm] = p.read_bytes()
    assert got["new"] == got["old"]
    assert got["new"].count(os.linesep.encode()) == 5


def _hold_lock(path):
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT
                 | getattr(os, "O_BINARY", 0))
    atomic_io._append_lock(fd, True)
    return fd


def _release(fd):
    atomic_io._append_lock(fd, False)
    os.close(fd)


def _write_in_thread(fn):
    t = threading.Thread(target=fn, daemon=True)
    t.start()
    t.join(0.5)
    return t


def test_a_held_lock_makes_the_writer_wait(sink):
    fd = _hold_lock(sink)
    try:
        t = _write_in_thread(lambda: fit_metrics._append(
            "m/meshes/a/b_1.nif", {"kind": "frame"}))
        assert t.is_alive(), "the writer did not wait for the lock"
    finally:
        _release(fd)
    t.join(10)
    assert not t.is_alive()
    assert json.loads(sink.read_bytes())["kind"] == "frame"


def test_switched_off_the_writer_does_not_wait(sink, monkeypatch):
    monkeypatch.setenv("CBBE2UBE_NO_ATOMIC_AUDIT_APPEND", "1")
    fd = _hold_lock(sink)
    try:
        t = _write_in_thread(lambda: fit_metrics._append(
            "m/meshes/a/b_1.nif", {"kind": "frame"}))
        assert not t.is_alive(), "switched off, the old writer took the lock"
    finally:
        _release(fd)
    assert json.loads(sink.read_bytes())["kind"] == "frame"


def test_the_glow_log_waits_for_the_lock_too(tmp_path, monkeypatch):
    monkeypatch.delenv("CBBE2UBE_NO_ATOMIC_AUDIT_APPEND", raising=False)
    log = tmp_path / "glow.log"
    monkeypatch.setenv("CBBE2UBE_GLOW_LOG", str(log))
    fd = _hold_lock(log)
    try:
        t = _write_in_thread(lambda: atomic_io._glow_log_write("block\n"))
        assert t.is_alive(), "the glow log did not wait for the lock"
    finally:
        _release(fd)
    t.join(10)
    assert log.read_text(encoding="utf-8").strip() == "block"


def test_a_lock_that_cannot_be_taken_still_writes_the_record(sink,
                                                            monkeypatch):
    def refuse(fd, lock):
        raise OSError("no byte-range locks here")
    monkeypatch.setattr(atomic_io, "_append_lock", refuse)
    fit_metrics._append("m/meshes/a/b_1.nif", {"kind": "frame"})
    assert json.loads(sink.read_bytes())["kind"] == "frame"


def _old_sink_with_a_tear(path):
    recs = [{"pass_": "chain-rest-lift", "moved": True, "root": "Skirt 1_00",
             "lift": 0.9, "path": "meshes/set/a/cuirass_1.nif",
             "kind": "survival", "nif": "cuirass_1.nif", "shape": "s"}]
    path.write_bytes((json.dumps(recs[0]) + "\r\n"
                      + '{"pass_": "chain-rest{"kind": "frame"}\r\n'
                      + "\r\n").encode())


def test_the_lift_log_reader_counts_a_torn_line(tmp_path, capsys):
    log = tmp_path / "standoff_audit.jsonl"
    _old_sink_with_a_tear(log)
    got = prd.read_lift_log(log)
    assert got.torn == 1                       # the blank line is not a tear
    assert dict(got[prd.log_key("set/a/cuirass_1.nif")]["moved"]) == {
        "Skirt 1_00": {0.9}}
    prd.lift_log_check([], got, 5)
    assert "1 torn line(s)" in capsys.readouterr().out


def test_the_survival_reader_says_it_skipped_a_torn_line(tmp_path, capsys):
    log = tmp_path / "standoff_audit.jsonl"
    _old_sink_with_a_tear(log)
    assert len(sr.load(log)) == 1
    assert "1 torn line(s)" in capsys.readouterr().err
