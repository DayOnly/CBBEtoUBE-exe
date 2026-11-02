"""Deterministic work counters for the per-unit run table. #unit-timing

Wall and CPU seconds on a busy machine swing by tens of percent between identical runs; these counts do not. A pass
adds to `WORK` where it does the work (candidate ray/triangle pairs, zap views rendered, NIF saves, full rebuilds);
`_run_unit` clears it before a unit and reports what the unit added. Never read by any fit decision.
"""
import os
import sys
from collections import Counter

WORK: Counter = Counter()


def add_attributed(key: str, n: int, skip=("fit_metrics", "work_counts")) -> None:
    """`WORK[key] += n`, and the same under `key@<module>.<function>` for the nearest caller outside `skip` -- the
    pass that asked for the work, so the run table can say which pass the ray pairs went to. #unit-timing"""
    WORK[key] += n
    f = sys._getframe(1)
    while f is not None:
        mod = os.path.splitext(os.path.basename(f.f_code.co_filename))[0]
        if mod not in skip:
            WORK[f"{key}@{mod}.{f.f_code.co_name}"] += n
            return
        f = f.f_back
