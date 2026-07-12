#!/usr/bin/env python3
"""PreToolUse guard: deny direct Write/Edit access to the PMO database files
and to the generated view files it renders.

The database has exactly one writer, the CLI; the rendered views (files whose
first line carries the generated-by-pmo header) are written only by the
render subcommand. Any agent or session trying to edit either directly is
blocked with exit 2, which cancels the tool call and feeds the reason back
to the model."""

from __future__ import annotations

import sys
from pathlib import Path

import hook_common
import pmo_cli


def main() -> int:
    payload = hook_common.read_payload()
    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return 0
    file_path = str(tool_input.get("file_path", "") or tool_input.get("path", ""))
    if not file_path:
        return 0
    try:
        target = Path(file_path).resolve()
        db = pmo_cli.db_path().resolve()
    except Exception:
        return 0
    if target == db or (target.parent == db.parent and target.name.startswith(db.name)):
        print(
            "pmo guard: the PMO database is written only through the PMO CLI;"
            " direct file writes are not allowed. Use the CLI subcommands.",
            file=sys.stderr,
        )
        return 2
    try:
        if target.is_file():
            with target.open("r", encoding="utf-8", errors="replace") as fh:
                first_line = fh.readline()
            if pmo_cli.GENERATED_HEADER in first_line:
                print(
                    "pmo guard: this file is a GENERATED view of the PMO"
                    " database; hand edits are not allowed and would be lost."
                    " Change the data through the CLI (item update, ledger"
                    " checkpoint, ...) and re-run the render subcommand.",
                    file=sys.stderr,
                )
                return 2
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
