"""#unit-peak: a unit's peak is its own, not the worker's lifetime peak left by an earlier, heavier unit."""
import sys
import time

import numpy as np
import pytest

from src import auto_convert as ac

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="commit figures are Windows-only")


def _hold(mb: int, secs: float = 0.3):
    a = np.ones(mb * 2**20 // 8)          # touched, so it is committed
    time.sleep(secs)
    return float(a[-1])


def test_a_unit_that_raises_the_lifetime_peak_reports_it_exactly():
    """In a fresh interpreter: in the suite's own process an earlier, heavier test may already hold the lifetime
    peak above 600 MB, and then this unit (correctly) reports "sampled"."""
    import json
    import subprocess
    from pathlib import Path
    code = (f"import sys, json; sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})\n"
            "import numpy as np, time\n"
            "from src import auto_convert as ac\n"
            "p = ac._UnitPeak()\n"
            "a = np.ones(600 * 2**20 // 8); time.sleep(0.3)\n"
            "mb, how = p.finish()\n"
            "print(json.dumps([mb, how, ac._process_private_mb(peak=True)]))\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    mb, how, peak = json.loads(out.stdout.strip().splitlines()[-1])
    assert how == "new-high"
    assert mb == peak


def test_a_lighter_unit_after_a_heavy_one_reports_its_own_peak():
    p = ac._UnitPeak()
    _hold(800)
    heavy, _ = p.finish()
    base = ac._process_private_mb()
    p = ac._UnitPeak()
    _hold(100)
    light, how = p.finish()
    assert how == "sampled"
    assert light < heavy - 400              # not the lifetime peak the heavy unit left
    assert light >= base + 80               # and it did see its own 100 MB


def test_the_poll_thread_stops():
    p = ac._UnitPeak()
    p.finish()
    assert not p._t.is_alive()
