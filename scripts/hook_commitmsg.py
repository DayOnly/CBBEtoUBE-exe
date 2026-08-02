"""commit-msg body: block a message that names a person, path or address."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import repo_hygiene as H  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return 0
    msg = Path(argv[1]).read_text(encoding="utf-8", errors="replace")
    problems = H.scan_message(msg)
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
