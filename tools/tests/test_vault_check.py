"""Builder registry for the vault checker (plugins/software-engineering-team/
scripts/vault_check.py), the doctrine test_ba_compile.py applies to ba_compile.

VAULT_BUILDERS holds one builder per id in vault_check.CHECK_IDS; each builder
mutates a copy of one valid project vault so that its check fires and no other;
a meta-test keeps the registry in lockstep with CHECK_IDS; and the untouched
vault stays silent. A check that cannot fire alone names the checks it always
fires with in COMPANIONS, and its builder is held to exactly that set."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins" / "software-engineering-team" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import vault_check  # noqa: E402
from tools.tests.git_fixture import init_repository  # noqa: E402

MAP = "maps/solution-design.md"
LANDSCAPE = "solution-design/landscape.md"
DECISION = "solution-design/decisions/queue-decision.md"
PROSE = "Orders flow through one queue."

# One Solution Design subtree on top of a set-up project vault: its map, the
# landscape hub and one decision, which the rendered decision index lists.
SUBTREE = {
    MAP: """---
type: moc
title: Solution Design
tags:
  - doc/moc
---

# Solution Design

- [[solution-design/landscape|Solution landscape]]
- [[solution-design/decisions/queue-decision|Queue choice]]
""",
    LANDSCAPE: f"""---
type: landscape
title: Solution landscape
status: draft
tags:
  - doc/landscape
  - status/draft
---

# Solution landscape

{PROSE}

## Navigation <!-- sec: nav -->

[[maps/solution-design|Solution Design]]
""",
    DECISION: """---
type: decision
title: Queue choice
status: proposed
tags:
  - doc/decision
  - status/proposed
aliases:
  - SD-001
---

# Queue choice

Use one durable queue.

## Navigation <!-- sec: nav -->

[[solution-design/landscape|Solution landscape]]
""",
}


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def append(path: Path, text: str) -> None:
    write(path, path.read_text(encoding="utf-8") + text)


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"fixture edit target not found in {path}: {old[:60]}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def map_note(title: str, body: str = "") -> str:
    return f"---\ntype: moc\ntitle: {title}\ntags:\n  - doc/moc\n---\n\n# {title}\n{body}"


def run_vault_check(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = vault_check.main(list(argv))
    return code, out.getvalue()


def findings(docs: Path) -> list[dict]:
    """Every finding one full check of the vault emits."""
    _code, out = run_vault_check("check", "--vault", str(docs), "--json")
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


def make_valid_vault(project: Path) -> Path:
    init_repository(project)
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "setup_project.py"), "--project-root", str(project)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    docs = project / "workspace" / "docs"
    for rel, text in SUBTREE.items():
        write(docs / rel, text)
    append(docs / "home.md", "\n- [[maps/solution-design|Solution Design]]\n")
    code, out = run_vault_check("render-decisions", "--vault", str(docs))
    assert code == 0, out
    return docs


def break_vault_layout(docs: Path) -> None:
    write(docs / "scratch.txt", "Working notes.\n")


def break_wikilink_resolution(docs: Path) -> None:
    edit(docs / LANDSCAPE, PROSE, "Orders flow through [[solution-design/missing-queue|one queue]].")


def break_anchor_resolution(docs: Path) -> None:
    edit(docs / LANDSCAPE, PROSE,
         "Orders flow through [[solution-design/decisions/queue-decision#^missing|one queue]].")


def break_link_policy(docs: Path) -> None:
    edit(docs / LANDSCAPE, PROSE, "Orders flow through [one queue](decisions/queue-decision.md).")


def break_table_pipe(docs: Path) -> None:
    edit(docs / LANDSCAPE, PROSE,
         "| Record |\n|---|\n| [[solution-design/decisions/queue-decision|Queue choice]] |")


def break_table_shape(docs: Path) -> None:
    edit(docs / LANDSCAPE, PROSE, "| Record |\n| Queue choice |")


def break_banned_basename(docs: Path) -> None:
    write(docs / "maps/overview.md", map_note("Solution overview"))
    append(docs / MAP, "- [[maps/overview|Solution overview]]\n")


def break_title_shape(docs: Path) -> None:
    edit(docs / LANDSCAPE, "# Solution landscape", "# Solution Landscape")


def break_orphans(docs: Path) -> None:
    write(docs / "maps/orphan-map.md", map_note("Orphan map"))


def break_moc_coverage(docs: Path) -> None:
    # It links itself, so it has an inbound link that home never reaches.
    write(docs / "maps/island-map.md", map_note("Island map", "\n[[maps/island-map|Island map]]\n"))


def break_map_coverage(docs: Path) -> None:
    # The decision's nav footer still links the hub, so only the map misses it.
    edit(docs / MAP, "- [[solution-design/landscape|Solution landscape]]\n", "")


def break_nav_footer(docs: Path) -> None:
    edit(docs / LANDSCAPE, "## Navigation <!-- sec: nav -->", "## Navigation")


def break_frontmatter_props(docs: Path) -> None:
    edit(docs / LANDSCAPE, "status: draft\n", "status: draft\ncolour: blue\n")


def break_tags_mirror(docs: Path) -> None:
    edit(docs / LANDSCAPE, "  - status/draft", "  - status/approved")


def break_alias_ownership(docs: Path) -> None:
    edit(docs / LANDSCAPE, PROSE, "Orders flow through [[maps/solution-design|SD-001]].")


def break_decision_records(docs: Path) -> None:
    append(docs / "solution-design/decision-log.md", "\nEdited by hand.\n")


def break_generated_views(docs: Path) -> None:
    append(docs / "maps/_generated/relation-status.md", "\nEdited by hand.\n")


def break_home_shape(docs: Path) -> None:
    append(docs / "home.md", "- [[solution-design/landscape|Solution landscape]]\n")


def break_obsidian_payload(docs: Path) -> None:
    (docs / ".obsidian" / "appearance.json").unlink()


VAULT_BUILDERS = {
    "vault_layout": break_vault_layout,
    "wikilink_resolution": break_wikilink_resolution,
    "anchor_resolution": break_anchor_resolution,
    "link_policy": break_link_policy,
    "table_pipe": break_table_pipe,
    "table_shape": break_table_shape,
    "banned_basename": break_banned_basename,
    "title_shape": break_title_shape,
    "orphans": break_orphans,
    "moc_coverage": break_moc_coverage,
    "map_coverage": break_map_coverage,
    "nav_footer": break_nav_footer,
    "frontmatter_props": break_frontmatter_props,
    "tags_mirror": break_tags_mirror,
    "alias_ownership": break_alias_ownership,
    "decision_records": break_decision_records,
    "generated_views": break_generated_views,
    "home_shape": break_home_shape,
    "obsidian_payload": break_obsidian_payload,
}

# A note without an inbound link cannot be reached from home either, so every
# orphans finding comes with a moc_coverage finding for the same note.
COMPANIONS = {"orphans": {"moc_coverage"}}


class VaultBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.docs = make_valid_vault(Path(cls.temporary.name) / "project")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_valid_vault_is_silent(self):
        self.assertEqual(findings(self.docs), [])

    def test_registry_lockstep_with_check_ids(self):
        self.assertEqual(sorted(VAULT_BUILDERS), sorted(vault_check.CHECK_IDS))

    def test_each_builder_fires_exactly_its_check(self):
        for check, builder in sorted(VAULT_BUILDERS.items()):
            with self.subTest(check=check), tempfile.TemporaryDirectory() as temporary:
                docs = Path(temporary) / "docs"
                shutil.copytree(self.docs, docs)
                builder(docs)
                found = findings(docs)
                self.assertEqual({finding["check"] for finding in found},
                                 {check, *COMPANIONS.get(check, ())}, found)


if __name__ == "__main__":
    unittest.main()
