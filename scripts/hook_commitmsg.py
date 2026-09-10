"""commit-msg body: block a message that names a person, path, address
or a real third-party asset.

The fourth of those was added after the commit INTRODUCING the asset-name
rule leaked a name in its own message: the rule had been wired into content
scanning and not into this one. A message cannot be edited afterwards
without rewriting history, so this is the more expensive of the two.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import repo_hygiene as H  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return 0
    msg = Path(argv[1]).read_text(encoding="utf-8", errors="replace")
    denylist, _n = H.load_denylist(Path(__file__).resolve().parent.parent)
    problems = H.scan_message(msg, denylist)
    if problems:
        sys.stderr.write("\nCOMMIT BLOCKED -- message would leak\n\n")
        for p in problems:
            sys.stderr.write(f"  {p}\n")
        sys.stderr.write(
            "\nCommit messages are public and cannot be edited without "
            "rewriting history.\nName the destination generically.\n\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
