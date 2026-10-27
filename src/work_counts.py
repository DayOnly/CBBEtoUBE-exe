"""Deterministic work counters for the per-unit run table. #unit-timing

Wall and CPU seconds on a busy machine swing by tens of percent between identical runs; these counts do not. A pass
adds to `WORK` where it does the work (candidate ray/triangle pairs, zap views rendered, NIF saves, full rebuilds);
`_run_unit` clears it before a unit and reports what the unit added. Never read by any fit decision.
"""
from collections import Counter

WORK: Counter = Counter()
