"""Research and diagnostic modules that the CONVERTER does not import.

Everything here was written to investigate a defect and is kept because the
investigation is worth reproducing -- but `src/` should mean "what actually
converts", and these were sitting in it while nothing in `src/` imported them.
Each has a passing test, which proved only that it still parses.

Moved out 2026-08-05 after a path-parity review. If one of these is ever wired
into the pipeline, move it back to `src/` in the same change that wires it.
"""
