"""The rename retry in `atomic_io._swap_into_place`.

A full pack reconvert lost 24 `.tri` writes to a transient Windows lock and the
old code gave up on the first refusal. Several NIFs regenerate the SAME `.tri`,
so with a worker pool two collide on the swap; antivirus and the search indexer
do the same thing to a file we just created. These pin the retry, and -- more
importantly -- pin that it still FAILS on a permanently held file rather than
hanging a batch.
"""
import time

import pytest

from src import atomic_io


def test_retries_a_transient_lock_and_succeeds(tmp_path, monkeypatch):
    dst = tmp_path / "out.tri"
    dst.write_bytes(b"old")
    calls = {"n": 0}
    real = atomic_io.os.replace

    def flaky(a, b):
        calls["n"] += 1
        if calls["n"] < 3:           # locked for the first two attempts
            raise PermissionError(32, "locked")
        return real(a, b)

    monkeypatch.setattr(atomic_io.os, "replace", flaky)
    monkeypatch.setattr(atomic_io.time, "sleep", lambda s: None)
    atomic_io.atomic_write_bytes(dst, b"new")
    assert dst.read_bytes() == b"new"
    assert calls["n"] == 3, "should have retried, not given up on the first lock"


def test_still_raises_on_a_permanently_locked_file(tmp_path, monkeypatch):
    dst = tmp_path / "out.tri"
    dst.write_bytes(b"old")

    def always(a, b):
        raise PermissionError(32, "locked")

    monkeypatch.setattr(atomic_io.os, "replace", always)
    monkeypatch.setattr(atomic_io.time, "sleep", lambda s: None)
    with pytest.raises(atomic_io.OutputLockedError) as e:
        atomic_io.atomic_write_bytes(dst, b"new")
    # the existing file must survive, and the advice must not tell the user to
    # close Mod Organizer -- MO2 LAUNCHES this tool, so it is always running.
    assert dst.read_bytes() == b"old"
    assert "Mod Organizer" in str(e.value)
    assert "does NOT need closing" in str(e.value)


def test_retry_is_bounded(tmp_path, monkeypatch):
    """A held file must not hang a 3907-NIF batch."""
    dst = tmp_path / "out.tri"
    slept = []
    monkeypatch.setattr(atomic_io.os, "replace",
                        lambda a, b: (_ for _ in ()).throw(PermissionError(32, "x")))
    monkeypatch.setattr(atomic_io.time, "sleep", slept.append)
    with pytest.raises(atomic_io.OutputLockedError):
        atomic_io.atomic_write_bytes(dst, b"new")
    assert len(slept) == atomic_io._SWAP_RETRIES - 1
    assert sum(slept) < 10.0, f"worst-case wait {sum(slept)}s is too long per file"


def test_no_temp_files_left_behind(tmp_path, monkeypatch):
    dst = tmp_path / "out.tri"
    monkeypatch.setattr(atomic_io.os, "replace",
                        lambda a, b: (_ for _ in ()).throw(PermissionError(32, "x")))
    monkeypatch.setattr(atomic_io.time, "sleep", lambda s: None)
    with pytest.raises(atomic_io.OutputLockedError):
        atomic_io.atomic_write_bytes(dst, b"new")
    assert list(tmp_path.glob("*.tmp")) == [], "a failed swap left its temp behind"
