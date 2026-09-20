"""The glow diagnostic must not record nothing while looking switched on.

`CBBE2UBE_GLOW_LOG` was pointed at a `tools/CBBEtoUBE/` directory that does
does not exist -- the tool's real folder is `CBBE to UBE`, with spaces. Every
append raised FileNotFoundError inside the caller's `except Exception: pass`,
so the diagnostic was on, cost work on every save, and caught nothing. Silence
from a diagnostic reads as evidence of absence, which is why this is a defect
and not a cosmetic one.
"""
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src import atomic_io  # noqa: E402


def _unwritable(base, name):
    """A path whose PARENT is a regular file, so mkdir cannot create it.

    Portable across versions, unlike an embedded NUL: Python 3.11 rejects a NUL
    in `os.environ[...]` before the code under test ever runs, so a NUL fixture
    passed on 3.10 and failed CI on 3.11/3.12 while testing nothing.
    """
    blocker = base / name
    blocker.write_text("not a directory", encoding="utf-8")
    return blocker / "glow.log"


@pytest.fixture(autouse=True)
def _reset_warned():
    atomic_io._GLOW_LOG_WARNED = False
    yield
    atomic_io._GLOW_LOG_WARNED = False


def test_a_missing_directory_is_created_rather_than_swallowed(tmp_path, monkeypatch):
    """THE MEASURED CASE: the configured parent does not exist."""
    target = tmp_path / "CBBEtoUBE" / "glowdebug.log"
    assert not target.parent.exists()
    monkeypatch.setenv("CBBE2UBE_GLOW_LOG", str(target))
    atomic_io._glow_log_write("hello\n")
    assert target.is_file(), "the diagnostic silently wrote nothing again"
    assert "hello" in target.read_text(encoding="utf-8")


def test_it_appends_rather_than_truncating(tmp_path, monkeypatch):
    target = tmp_path / "g.log"
    monkeypatch.setenv("CBBE2UBE_GLOW_LOG", str(target))
    atomic_io._glow_log_write("one\n")
    atomic_io._glow_log_write("two\n")
    assert target.read_text(encoding="utf-8") == "one\ntwo\n"


def test_an_unusable_path_falls_back_to_the_tool_folder(tmp_path, monkeypatch):
    """A path that cannot be created must not lose the record."""
    monkeypatch.setattr(atomic_io, "_GLOW_LOG_WARNED", False)
    monkeypatch.setenv("CBBE2UBE_GLOW_LOG", "\x00:/nope/glow.log")
    monkeypatch.setattr("src.paths.tool_dir", lambda: tmp_path)
    atomic_io._glow_log_write("rescued\n")
    out = tmp_path / "CBBEtoUBE_glowdebug.log"
    assert out.is_file() and "rescued" in out.read_text(encoding="utf-8")


def test_when_nothing_can_be_written_it_says_so(tmp_path, monkeypatch, capsys):
    """The failure this exists to prevent: on is not the same as recording."""
    monkeypatch.setenv("CBBE2UBE_GLOW_LOG", "\x00:/nope/glow.log")
    monkeypatch.setattr("src.paths.tool_dir", lambda: Path("\x00:/also-nope"))
    atomic_io._glow_log_write("lost\n")
    err = capsys.readouterr().err
    assert "recording NOTHING" in err, err


def test_it_warns_only_once_per_process(tmp_path, monkeypatch, capsys):
    """It runs after EVERY save; a warning per save buries the run log."""
    monkeypatch.setenv("CBBE2UBE_GLOW_LOG", "\x00:/nope/glow.log")
    monkeypatch.setattr("src.paths.tool_dir", lambda: Path("\x00:/also-nope"))
    for _ in range(3):
        atomic_io._glow_log_write("lost\n")
    assert capsys.readouterr().err.count("recording NOTHING") == 1


def test_a_successful_fallback_is_not_warned_about(tmp_path, monkeypatch, capsys):
    """Warning when the record WAS kept trains the reader to ignore this."""
    monkeypatch.setenv("CBBE2UBE_GLOW_LOG", "\x00:/nope/glow.log")
    monkeypatch.setattr("src.paths.tool_dir", lambda: tmp_path)
    atomic_io._glow_log_write("kept\n")
    assert "recording NOTHING" not in capsys.readouterr().err
