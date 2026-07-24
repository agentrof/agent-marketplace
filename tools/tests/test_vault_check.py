"""Tests for the vault checker and the per-write vault hook
(plugins/software-engineering-team/scripts/vault_check.py, vault_hook.py),
run against the SHIPPED vault policy so the law and the machinery are
welded together.

Doctrine mirror of test_ba_compile.py: a VAULT_BUILDERS registry holds one
builder per checker check id; each builder mutates a valid, gate-passing
vault to make exactly that check fire; a meta-test keeps the registry in
lockstep with CHECK_IDS; and the untouched valid vault stays silent."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "plugins" / "software-engineering-team" / "scripts"
POLICY_PATH = (REPO / "plugins" / "software-engineering-team" / "skills"
               / "obsidian-vault" / "data" / "vault-policy.json")


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ba = load("ba_compile")
vc = load("vault_check")
vh = load("vault_hook")

POLICY = json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = vc.main(["--policy", str(POLICY_PATH)] + argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"fixture edit target not found in {path}: {old[:60]}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def typed_note(note_type: str, title: str, extra_fm: str = "") -> str:
    """A minimal authored note of a given type and title. Firing tests that
    only assert one finding is PRESENT tolerate the note's side findings."""
    tag = note_type.replace("_", "-")
    return (f"---\ntype: {note_type}\ntitle: {title}\n{extra_fm}"
            f"tags:\n  - doc/{tag}\n---\n\n# {title}\n\nBody text.\n")


SD = "solution-design"
DEC = f"{SD}/decisions"

NAV = """## Navigation <!-- sec: nav -->
[[maps/solution-design|Solution Design]] -
{peers}
"""


BA = "business-analysis"
BA_SPACE = f"{BA}/erp"

# The consumer's rendered designation map (generic English values, one per
# taxonomy type in the shipped universe). Written to <workspace>/config.json
# so titles that already close in their designation pass the check.
DESIGNATIONS = {
    "space": "space overview",
    "domain": "domain overview",
    "glossary": "glossary",
    "actor-roster": "actors",
    "budget-set": "budgets",
    "process": "process",
    "entity": "entity",
    "rule-set": "rules",
    "acceptance-set": "acceptance criteria",
    "decision": "decision",
    "challenge-record": "review round",
    "integration": "integration",
    "landscape": "landscape",
    "engagement": "engagement",
    "design-master": "design master",
    "page-override": "page override",
}


def write_config(root: Path, designations=DESIGNATIONS, history=None,
                 project_key=None) -> None:
    """Mint the consumer designation map at <workspace>/config.json (the
    workspace is the vault's parent, mirroring the runtime derivation).
    `history` writes the retired-designation ledger; `project_key` the
    PMO project binding."""
    payload = {"terminology_language": "English"}
    if project_key is not None:
        payload["project_key"] = project_key
    if designations is not None:
        payload["doc_type_designations"] = designations
    if history is not None:
        payload["doc_type_designation_history"] = history
    write(root.parent / "config.json", json.dumps(payload, indent=2))


def make_valid_vault(root: Path) -> None:
    write_config(root)
    write(root / "home.md", """---
type: home
title: Shop Knowledge Base
tags:
  - doc/home
---

# Shop Knowledge Base

- [[maps/business-analysis|Business Analysis]]
- [[maps/solution-design|Solution Design]]
""")
    write(root / "maps" / "solution-design.md", """---
type: moc
title: Solution Design
tags:
  - doc/moc
---

# Solution Design

- [[solution-design/landscape|Landscape]]
- [[solution-design/decision-log|Decision Index]]
""")
    write(root / "maps" / "business-analysis.md", """---
type: moc
title: Business Analysis
tags:
  - doc/moc
---

# Business Analysis

- [[business-analysis/erp/space|ERP Analysis]]
- [[business-analysis/erp/decisions/pilot-scope-decision|DEC-ERP-001]]
""")
    write(root / BA_SPACE / "space.md", """---
type: space
title: ERP analysis space overview
status: approved
code: ERP
tags:
  - doc/space
  - status/approved
aliases:
  - ERP
---

# ERP analysis space overview

The pilot analysis space. Its rules registry backs
[[business-analysis/erp/space|BR-ERP-001]] style citations.

## Navigation <!-- sec: nav -->
[[maps/business-analysis|Business Analysis]] -
[[business-analysis/erp/decisions/pilot-scope-decision|DEC-ERP-001]]
""")
    write(root / BA_SPACE / "decisions" / "pilot-scope-decision.md", """---
type: decision
title: Pilot scope decision
status: approved
tags:
  - doc/decision
  - status/approved
aliases:
  - DEC-ERP-001
---

# Pilot scope decision

The pilot covers inventory only, per
[[business-analysis/erp/space|ERP Analysis]].

## Ruling <!-- sec: ruling -->

| id | ruling | status | decided_on |
|---|---|---|---|
| DEC-ERP-001 | The pilot covers inventory only. | active | 2026-07-10 |

## Navigation <!-- sec: nav -->
[[business-analysis/erp/space|ERP Analysis]] -
[[maps/business-analysis|Business Analysis]]
""")
    write(root / BA_SPACE / "_generated" / "registry.json", json.dumps({
        "schema_version": 2,
        "codes": {"ERP": "(root)"},
        "ids": {
            "BR-ERP-001": {"kind": "BR", "doc": "space.md",
                           "row_status": "active"},
            "DEC-ERP-001": {"kind": "DEC",
                            "doc": "decisions/pilot-scope-decision.md",
                            "row_status": "active"},
        },
    }, indent=2))
    write(root / SD / "landscape.md", """---
type: landscape
title: Landscape
status: approved
tags:
  - doc/landscape
  - status/approved
---

# Landscape

The components and their owning decisions.

| component | decision |
|---|---|
| order-events | [[solution-design/decisions/order-events-v2-decision\\|SD-002]] |

""" + NAV.format(peers=(
        "[[solution-design/decisions/order-events-decision|SD-001]] -\n"
        "[[solution-design/decisions/order-events-v2-decision|SD-002]]")))
    write(root / DEC / "order-events-decision.md", """---
type: decision
title: Order events v1 decision
status: superseded
owner_role: solution_architect
decided_at: 2026-01-10
territory: asynchronous work
revisit_trigger: volume beyond budget
superseded_by: "[[solution-design/decisions/order-events-v2-decision]]"
tags:
  - doc/decision
  - status/superseded
aliases:
  - SD-001
---

# Order events v1 decision

**Decision:** direct queue fan-out.

""" + NAV.format(peers=(
        "[[solution-design/landscape|Landscape]] -\n"
        "[[solution-design/decisions/order-events-v2-decision|SD-002]]")))
    write(root / DEC / "order-events-v2-decision.md", """---
type: decision
title: Order events v2 decision
status: accepted
owner_role: solution_architect
decided_at: 2026-02-01
territory: asynchronous work
revisit_trigger: volume beyond budget
supersedes: "[[solution-design/decisions/order-events-decision]]"
tags:
  - doc/decision
  - status/accepted
aliases:
  - SD-002
---

# Order events v2 decision

**Decision:** managed streaming service.

""" + NAV.format(peers=(
        "[[solution-design/landscape|Landscape]] -\n"
        "[[solution-design/decisions/order-events-decision|SD-001]]")))
    obsidian = root / ".obsidian"
    write(obsidian / "app.json", json.dumps({
        "useMarkdownLinks": False,
        "newLinkFormat": "absolute",
        "alwaysUpdateLinks": True,
        "attachmentFolderPath": "_attachments",
    }))
    write(obsidian / "appearance.json",
          json.dumps({"enabledCssSnippets": ["brand"]}))
    write(obsidian / "core-plugins.json",
          json.dumps(["graph", "backlinks", "page-preview", "properties"]))
    write(obsidian / "graph.json", json.dumps({
        "search": POLICY["graph_search"],
        "colorGroups": [
            {"query": query, "color": {"a": 1, "rgb": i + 1}}
            for i, query in enumerate(POLICY["graph_color_groups"])
        ],
        "showOrphans": True, "hideUnresolved": False, "showTags": False,
    }))
    write(obsidian / "types.json",
          json.dumps({"types": POLICY["property_types"]}))
    write(obsidian / "community-plugins.json",
          json.dumps(POLICY["community_plugins"]))
    plugin_dir = obsidian / "plugins" / "obsidian-front-matter-title-plugin"
    write(plugin_dir / "manifest.json", json.dumps({
        "id": "obsidian-front-matter-title-plugin",
        "name": "Front Matter Title",
        "version": "4.1.1",
        "minAppVersion": "1.0.0",
    }))
    # Tiny stand-in build; the settings shape follows the REAL upstream
    # contract (bare "title" key, "suggest" not "switcher", noteLink and
    # alias pinned off).
    write(plugin_dir / "main.js", "module.exports = { onload() {} };\n")
    write(plugin_dir / "data.json", json.dumps({
        "version": "4.1.1",
        "templates": {"common": {"main": "title", "fallback": None}},
        "features": {
            "explorer": {"enabled": True},
            "graph": {"enabled": True},
            "search": {"enabled": True},
            "suggest": {"enabled": True},
            "tab": {"enabled": True},
            "canvas": {"enabled": True},
            "noteLink": {"enabled": False},
            "alias": {"enabled": False},
        },
    }))
    write(obsidian / "snippets" / "brand.css", ".theme-dark {}\n")
    code, out, err = run(["render-decisions", "--vault", str(root)])
    assert code == 0, f"fixture render failed: {out} {err}"


def check_findings(root: Path):
    code, out, _ = run(["check", "--vault", str(root), "--json"])
    findings = [json.loads(line) for line in out.splitlines()
                if line.startswith("{")]
    return code, findings


# --- one builder per checker check id ------------------------------------


def break_vault_layout(root: Path) -> None:
    write(root / SD / "sketch.base", "views: []\n")


def break_wikilink_resolution(root: Path) -> None:
    edit(root / SD / "landscape.md",
         "The components and their owning decisions.",
         "The components and their owning decisions.\n\n"
         "See [[solution-design/missing-note|the missing note]].")


def break_anchor_resolution(root: Path) -> None:
    edit(root / DEC / "order-events-v2-decision.md",
         "**Decision:** managed streaming service.",
         "**Decision:** managed streaming service, per"
         " [[solution-design/landscape#Landscape|the landscape heading]].")


def break_link_policy(root: Path) -> None:
    edit(root / DEC / "order-events-v2-decision.md",
         "**Decision:** managed streaming service.",
         "**Decision:** managed streaming service, see"
         " [the landscape](../landscape.md).")


def break_table_pipe(root: Path) -> None:
    edit(root / SD / "landscape.md",
         "[[solution-design/decisions/order-events-v2-decision\\|SD-002]]",
         "[[solution-design/decisions/order-events-v2-decision|SD-002]]")


def break_table_shape(root: Path) -> None:
    edit(root / SD / "landscape.md",
         "The components and their owning decisions.",
         "The components and their owning decisions.\n\n"
         "| component | note |\n"
         "```\nfenced text right after the header\n```")


def break_banned_basename(root: Path) -> None:
    write(root / SD / "notes.md", """---
type: note
title: Working Notes
tags:
  - doc/note
---

# Working Notes

Scratch notes under a policy-banned basename.

""" + NAV.format(peers=(
        "[[solution-design/landscape|Landscape]] -\n"
        "[[solution-design/decisions/order-events-decision|SD-001]]")))
    edit(root / "maps" / "solution-design.md",
         "- [[solution-design/landscape|Landscape]]",
         "- [[solution-design/landscape|Landscape]]\n"
         "- [[solution-design/notes|Working Notes]]")


def break_title_shape(root: Path) -> None:
    edit(root / SD / "landscape.md", "title: Landscape\n", "")


def break_map_coverage(root: Path) -> None:
    edit(root / "maps" / "business-analysis.md",
         "- [[business-analysis/erp/space|ERP Analysis]]\n", "")


def break_alias_ownership(root: Path) -> None:
    edit(root / SD / "landscape.md",
         "The components and their owning decisions.",
         "The components and their owning decisions, per"
         " [[solution-design/landscape|SD-001]].")


def break_orphans(root: Path) -> None:
    write(root / SD / "floating.md", """---
type: note
title: Floating
tags:
  - doc/note
---

# Floating

Nothing links here.

""" + NAV.format(peers=(
        "[[solution-design/landscape|Landscape]] -\n"
        "[[solution-design/decisions/order-events-decision|SD-001]]")))


def break_moc_coverage(root: Path) -> None:
    for name, other in (("loop-a", "loop-b"), ("loop-b", "loop-a")):
        write(root / SD / f"{name}.md", f"""---
type: note
title: Loop {name[-1].upper()}
tags:
  - doc/note
---

# Loop {name[-1].upper()}

Mutual: [[solution-design/{other}|{other}]].

""" + NAV.format(peers=(
            "[[solution-design/landscape|Landscape]] -\n"
            "[[solution-design/decisions/order-events-decision|SD-001]]")))


def break_nav_footer(root: Path) -> None:
    edit(root / SD / "landscape.md",
         "## Navigation <!-- sec: nav -->", "## Navigation")


def break_frontmatter_props(root: Path) -> None:
    edit(root / SD / "landscape.md", "type: landscape\n", "")


def break_tags_mirror(root: Path) -> None:
    edit(root / SD / "landscape.md",
         "  - status/approved", "  - status/approved\n  - doc/extra")


def break_decision_records(root: Path) -> None:
    # The record id's single home is the id-shaped alias; dropping it
    # leaves the note without exactly one SD-shaped alias.
    edit(root / DEC / "order-events-v2-decision.md",
         "aliases:\n  - SD-002\n", "aliases:\n")


def break_generated_views(root: Path) -> None:
    (root / SD / "decision-log.md").unlink()


def break_home_shape(root: Path) -> None:
    edit(root / "home.md", "- [[maps/solution-design|Solution Design]]",
         "- [[maps/solution-design|Solution Design]]\n"
         "- [[solution-design/landscape|Landscape]]")


def break_obsidian_payload(root: Path) -> None:
    edit(root / ".obsidian" / "app.json", "\"absolute\"", "\"shortest\"")


def break_designation_drift(root: Path) -> None:
    # The double-suffix shape: a retired ledger value survives UNDER the
    # current designation, so the containment check stays green and only
    # the ledger-backed drift check can see it.
    config_path = root.parent / "config.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["doc_type_designation_history"] = {
        "space": [{"value": "blueprint", "replaced": "2026-01-01",
                   "superseded_by": "space overview"}]}
    config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    edit(root / BA_SPACE / "space.md",
         "title: ERP analysis space overview",
         "title: ERP analysis blueprint space overview")
    edit(root / BA_SPACE / "space.md",
         "# ERP analysis space overview",
         "# ERP analysis blueprint space overview")


VAULT_BUILDERS = {
    "vault_layout": break_vault_layout,
    "wikilink_resolution": break_wikilink_resolution,
    "anchor_resolution": break_anchor_resolution,
    "link_policy": break_link_policy,
    "table_pipe": break_table_pipe,
    "table_shape": break_table_shape,
    "banned_basename": break_banned_basename,
    "title_shape": break_title_shape,
    "designation_drift": break_designation_drift,
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

# A builder whose defect necessarily trips a second check tolerates
# exactly that side effect and nothing else.
TOLERATED = {
    "orphans": {"moc_coverage"},        # zero inbound implies unreachable
    # a deleted index is also a stale index and a dead map link
    "generated_views": {"decision_records", "wikilink_resolution"},
}


class ValidVaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "docs"
        make_valid_vault(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_vault_is_silent(self):
        code, findings = check_findings(self.root)
        self.assertEqual(code, 0)
        self.assertEqual(findings, [],
                         [f"{f['check']}: {f['path']}: {f['message']}"
                          for f in findings])

    def test_render_is_deterministic(self):
        index = self.root / SD / "decision-log.md"
        first = index.read_bytes()
        code, _, _ = run(["render-decisions", "--vault", str(self.root)])
        self.assertEqual(code, 0)
        self.assertEqual(first, index.read_bytes())

    def test_index_carries_marker_and_rows(self):
        text = (self.root / SD / "decision-log.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("<!-- generated by vault_check"))
        self.assertIn("\\|SD-001]]", text)
        self.assertIn("superseded", text)

    def test_scope_confines_but_keeps_global_checks(self):
        break_obsidian_payload(self.root)
        write(self.root / "business-analysis" / "stray.md", "no frontmatter\n")
        code, findings = check_findings_scoped(self.root, SD)
        self.assertEqual(code, 1)
        checks = {f["check"] for f in findings}
        self.assertIn("obsidian_payload", checks)
        self.assertNotIn("frontmatter_props", checks)

    def test_exclude_downgrades_to_warning(self):
        break_wikilink_resolution(self.root)
        code, out, _ = run(["check", "--vault", str(self.root),
                            "--exclude", f"{SD}/landscape.md"])
        self.assertEqual(code, 0)
        self.assertIn("excluded path", out)


def check_findings_scoped(root: Path, scope: str):
    code, out, _ = run(["check", "--vault", str(root), "--scope", scope,
                        "--json"])
    findings = [json.loads(line) for line in out.splitlines()
                if line.startswith("{")]
    return code, findings


class BuilderFixtureTests(unittest.TestCase):
    def test_registry_lockstep_with_check_ids(self):
        self.assertEqual(sorted(VAULT_BUILDERS), sorted(vc.CHECK_IDS))

    def test_each_builder_fires_its_check(self):
        for check, builder in sorted(VAULT_BUILDERS.items()):
            with self.subTest(check=check):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp) / "docs"
                    make_valid_vault(root)
                    builder(root)
                    code, findings = check_findings(root)
                    self.assertEqual(code, 1, f"{check}: expected errors")
                    fired = {f["check"] for f in findings}
                    self.assertIn(check, fired)
                    allowed = {check} | TOLERATED.get(check, set())
                    self.assertLessEqual(
                        fired, allowed,
                        f"{check}: unexpected side findings {fired - allowed}")

    def test_title_h1_divergence_fires(self):
        """Title law v2: the first H1 is byte-identical to the title."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "docs"
            make_valid_vault(root)
            edit(root / SD / "landscape.md", "# Landscape",
                 "# The Landscape")
            code, findings = check_findings(root)
            self.assertEqual(code, 1)
            matching = [f for f in findings if f["check"] == "title_shape"
                        and "byte-identical to the title" in f["message"]]
            self.assertTrue(matching, findings)

    def test_id_led_title_fires(self):
        """Title law v2 flip: a title led by the note's own id alias is
        an error (ids live in the alias, never in the label)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "docs"
            make_valid_vault(root)
            edit(root / DEC / "order-events-v2-decision.md",
                 "title: Order events v2 decision",
                 'title: "SD-002: Order events v2 decision"')
            code, findings = check_findings(root)
            self.assertEqual(code, 1)
            matching = [f for f in findings if f["check"] == "title_shape"
                        and "id-led" in f["message"]]
            self.assertTrue(matching, findings)


class VerbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "docs"
        make_valid_vault(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stamp_decision_one_write(self):
        note = self.root / DEC / "order-retries-decision.md"
        write(note, """---
type: decision
title: Order retries decision
status: proposed
owner_role: solution_architect
territory: asynchronous work
revisit_trigger: retry storm observed
tags:
  - doc/decision
  - status/proposed
aliases:
  - SD-003
---

# Order retries decision

**Decision:** bounded retries with dead-lettering.

""" + NAV.format(peers=(
            "[[solution-design/landscape|Landscape]] -\n"
            "[[solution-design/decisions/order-events-v2-decision|SD-002]]")))
        edit(self.root / SD / "landscape.md",
             "[[solution-design/decisions/order-events-v2-decision|SD-002]]",
             "[[solution-design/decisions/order-events-v2-decision|SD-002]] -\n"
             "[[solution-design/decisions/order-retries-decision|SD-003]]")
        code, out, err = run([
            "stamp-decision", "--vault", str(self.root),
            "--note", f"{DEC}/order-retries-decision.md",
            "--status", "accepted"])
        self.assertEqual(code, 0, out + err)
        text = note.read_text(encoding="utf-8")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.assertIn("status: accepted", text)
        self.assertIn(f"decided_at: {today}", text)
        self.assertIn("- status/accepted", text)
        index = (self.root / SD / "decision-log.md").read_text(encoding="utf-8")
        self.assertIn("SD-003", index)
        code, findings = check_findings(self.root)
        self.assertEqual(code, 0,
                         [f"{f['check']}: {f['message']}" for f in findings])

    def test_stamp_supersede_writes_both_ends(self):
        note = self.root / DEC / "order-events-v3-decision.md"
        write(note, """---
type: decision
title: Order events v3 decision
status: proposed
owner_role: solution_architect
territory: asynchronous work
revisit_trigger: volume beyond budget
tags:
  - doc/decision
  - status/proposed
aliases:
  - SD-003
---

# Order events v3 decision

**Decision:** broker-managed fan-out.

""" + NAV.format(peers=(
            "[[solution-design/landscape|Landscape]] -\n"
            "[[solution-design/decisions/order-events-v2-decision|SD-002]]")))
        edit(self.root / SD / "landscape.md",
             "| order-events | [[solution-design/decisions/order-events-v2-decision\\|SD-002]] |",
             "| order-events | [[solution-design/decisions/order-events-v3-decision\\|SD-003]] |")
        edit(self.root / SD / "landscape.md",
             "[[solution-design/decisions/order-events-v2-decision|SD-002]]",
             "[[solution-design/decisions/order-events-v2-decision|SD-002]] -\n"
             "[[solution-design/decisions/order-events-v3-decision|SD-003]]")
        code, out, err = run([
            "stamp-decision", "--vault", str(self.root),
            "--note", f"{DEC}/order-events-v3-decision.md",
            "--status", "accepted",
            "--supersedes", f"{DEC}/order-events-v2-decision.md"])
        self.assertEqual(code, 0, out + err)
        new_text = note.read_text(encoding="utf-8")
        old_text = (self.root / DEC / "order-events-v2-decision.md").read_text(
            encoding="utf-8")
        self.assertIn('supersedes: "[[solution-design/decisions/'
                      'order-events-v2-decision]]"', new_text)
        self.assertIn('superseded_by: "[[solution-design/decisions/'
                      'order-events-v3-decision]]"', old_text)
        self.assertIn("status: superseded", old_text)
        self.assertIn("- status/superseded", old_text)
        code, findings = check_findings(self.root)
        self.assertEqual(code, 0,
                         [f"{f['check']}: {f['message']}" for f in findings])

    def test_render_refuses_duplicate_ids(self):
        write(self.root / DEC / "order-events-copy-decision.md",
              (self.root / DEC / "order-events-v2-decision.md")
              .read_text(encoding="utf-8"))
        code, _, err = run(["render-decisions", "--vault", str(self.root)])
        self.assertEqual(code, 1)
        self.assertIn("duplicate id number 002", err)

    def test_migrate_rewrites_deterministic_classes(self):
        legacy = self.root / SD / "legacy.md"
        write(legacy, """---
type: note
title: Legacy
tags:
  - doc/wrong
---

# Legacy

See [the landscape](landscape.md) and
[SD-001](decisions/order-events-decision.md).

""" + NAV.format(peers=(
            "[[solution-design/landscape|Landscape]] -\n"
            "[[solution-design/decisions/order-events-decision|SD-001]]")))
        edit(self.root / "maps" / "solution-design.md",
             "- [[solution-design/landscape|Landscape]]",
             "- [[solution-design/landscape|Landscape]]\n"
             "- [[solution-design/legacy|Legacy]]")
        code, out, _ = run(["migrate", "--vault", str(self.root)])
        self.assertEqual(code, 0)
        text = legacy.read_text(encoding="utf-8")
        self.assertIn("[[solution-design/landscape|the landscape]]", text)
        self.assertIn(
            "[[solution-design/decisions/order-events-decision|SD-001]]", text)
        self.assertIn("- doc/note", text)
        self.assertNotIn("doc/wrong", text)
        code, findings = check_findings(self.root)
        self.assertEqual(code, 0,
                         [f"{f['check']}: {f['message']}" for f in findings])


def v5_shaped_names(root: Path) -> None:
    """Rewind the mini analysis space to its v5 names: chain-prefixed
    space file, id-prefixed decision file, referrers pointing at both."""
    (root / BA_SPACE / "space.md").rename(root / BA_SPACE / "erp-space.md")
    (root / BA_SPACE / "decisions" / "pilot-scope-decision.md").rename(
        root / BA_SPACE / "decisions" / "dec-erp-001-pilot-scope.md")
    for rel in ("maps/business-analysis.md", f"{BA_SPACE}/erp-space.md",
                f"{BA_SPACE}/decisions/dec-erp-001-pilot-scope.md"):
        path = root / rel
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            "business-analysis/erp/decisions/pilot-scope-decision",
            "business-analysis/erp/decisions/dec-erp-001-pilot-scope")
        text = text.replace("business-analysis/erp/space",
                            "business-analysis/erp/erp-space")
        path.write_text(text, encoding="utf-8")


class RenameVerbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "docs"
        make_valid_vault(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rename_round_trip_restores_green(self):
        v5_shaped_names(self.root)
        code, out, err = run(["migrate", "--vault", str(self.root),
                              "--rename"])
        self.assertEqual(code, 0, out + err)
        self.assertTrue((self.root / BA_SPACE / "space.md").is_file())
        self.assertTrue((self.root / BA_SPACE / "decisions"
                         / "pilot-scope-decision.md").is_file())
        self.assertFalse((self.root / BA_SPACE / "erp-space.md").exists())
        self.assertFalse((self.root / BA_SPACE / "decisions"
                          / "dec-erp-001-pilot-scope.md").exists())
        map_text = (self.root / "maps" / "business-analysis.md").read_text(
            encoding="utf-8")
        self.assertIn("business-analysis/erp/space", map_text)
        self.assertIn("pilot-scope-decision", map_text)
        self.assertNotIn("erp-space", map_text)
        code, findings = check_findings(self.root)
        self.assertEqual(code, 0,
                         [f"{f['check']}: {f['message']}" for f in findings])

    def test_rename_double_run_is_idempotent(self):
        """Red-team 2: the decision skip gate is the filename-suffix
        test, never the record id (both generations carry the alias), so
        a second --rename run plans nothing."""
        v5_shaped_names(self.root)
        code, _, err = run(["migrate", "--vault", str(self.root),
                            "--rename"])
        self.assertEqual(code, 0, err)
        code, out, err = run(["migrate", "--vault", str(self.root),
                              "--rename", "--json"])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["renames"], [])
        self.assertEqual(payload["manual"], [])

    def test_rename_same_slug_collision_routes_manual(self):
        """Red-team 6: two v5 decisions minting one plain target route to
        the manual list and the run proceeds; never an abort, never an
        id appended to disambiguate."""
        v5_shaped_names(self.root)
        source = (self.root / BA_SPACE / "decisions"
                  / "dec-erp-001-pilot-scope.md")
        twin = (self.root / BA_SPACE / "decisions"
                / "dec-erp-002-pilot-scope.md")
        write(twin, source.read_text(encoding="utf-8")
              .replace("DEC-ERP-001", "DEC-ERP-002"))
        code, out, err = run(["migrate", "--vault", str(self.root),
                              "--rename", "--dry-run", "--json"])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        manual = set(payload["manual"])
        self.assertIn(f"{BA_SPACE}/decisions/dec-erp-001-pilot-scope.md",
                      manual)
        self.assertIn(f"{BA_SPACE}/decisions/dec-erp-002-pilot-scope.md",
                      manual)
        self.assertTrue(payload["collisions"])
        # the real run proceeds (no abort) and executes the unambiguous
        # renames
        code, _, err = run(["migrate", "--vault", str(self.root),
                            "--rename"])
        self.assertEqual(code, 0, err)
        self.assertTrue((self.root / BA_SPACE / "space.md").is_file())

    def test_rename_round_inverses_are_node_scoped(self):
        """The review-suffix inversion maps BOTH the v6 plain forms and the
        v4/v5 chain forms to the -review target, node-scoped: the root only
        applies the space-round inverse and a domain only the round inverse,
        so a domain never claims a space-round name. Already-suffixed names
        never re-match (idempotent)."""
        reviews = self.root / BA_SPACE / "reviews"
        write(reviews / "erp-space-round-1.md", "# r\n")   # v5 chain, root
        write(reviews / "space-round-2.md", "# r\n")        # v6 plain, root
        dom = self.root / BA_SPACE / "domains" / "inventory" / "reviews"
        write(dom / "erp-inventory-round-1.md", "# r\n")    # v5 chain, domain
        write(dom / "round-2.md", "# r\n")                   # v6 plain, domain
        write(dom / "round-3-review.md", "# r\n")            # v7, already done
        write(dom / "space-round-9.md", "# r\n")             # misplaced space
        code, out, err = run(["migrate", "--vault", str(self.root),
                              "--rename", "--dry-run", "--json"])
        self.assertEqual(code, 0, out + err)
        payload = json.loads(out)
        plan = {e["old"]: e["new"] for e in payload["renames"]}
        self.assertEqual(plan.get(f"{BA_SPACE}/reviews/erp-space-round-1.md"),
                         f"{BA_SPACE}/reviews/space-round-1-review.md")
        self.assertEqual(plan.get(f"{BA_SPACE}/reviews/space-round-2.md"),
                         f"{BA_SPACE}/reviews/space-round-2-review.md")
        self.assertEqual(
            plan.get(f"{BA_SPACE}/domains/inventory/reviews/"
                     "erp-inventory-round-1.md"),
            f"{BA_SPACE}/domains/inventory/reviews/round-1-review.md")
        self.assertEqual(
            plan.get(f"{BA_SPACE}/domains/inventory/reviews/round-2.md"),
            f"{BA_SPACE}/domains/inventory/reviews/round-2-review.md")
        # already -review is a no-op; a domain never claims a space-round
        self.assertNotIn(
            f"{BA_SPACE}/domains/inventory/reviews/round-3-review.md", plan)
        self.assertNotIn(
            f"{BA_SPACE}/domains/inventory/reviews/space-round-9.md", plan)

    def test_rename_dry_run_counts_and_writes_nothing(self):
        v5_shaped_names(self.root)
        code, out, err = run(["migrate", "--vault", str(self.root),
                              "--rename", "--dry-run", "--json"])
        self.assertEqual(code, 0, out + err)
        payload = json.loads(out)
        self.assertTrue(payload["dry_run"])
        by_old = {entry["old"]: entry for entry in payload["renames"]}
        space_entry = by_old[f"{BA_SPACE}/erp-space.md"]
        self.assertEqual(space_entry["new"], f"{BA_SPACE}/space.md")
        self.assertGreaterEqual(space_entry["referrers"], 2)
        dec_entry = by_old[f"{BA_SPACE}/decisions/dec-erp-001-pilot-scope.md"]
        self.assertEqual(dec_entry["new"],
                         f"{BA_SPACE}/decisions/pilot-scope-decision.md")
        self.assertTrue((self.root / BA_SPACE / "erp-space.md").is_file())

    def test_rename_vetoes_frozen_referrer(self):
        v5_shaped_names(self.root)
        write(self.root.parent / "work-orders" / "wo-7" / "freeze.json",
              json.dumps({"frozen_paths":
                          ["workspace/docs/maps/business-analysis.md"]}))
        code, out, err = run(["migrate", "--vault", str(self.root),
                              "--rename", "--dry-run", "--json"])
        self.assertEqual(code, 0, out + err)
        payload = json.loads(out)
        blocked = {entry["old"]: entry for entry in payload["blocked"]}
        self.assertIn(f"{BA_SPACE}/erp-space.md", blocked)
        self.assertIn("maps/business-analysis.md",
                      blocked[f"{BA_SPACE}/erp-space.md"]["blocked_by"])
        self.assertEqual(sorted(payload["blocked_paths"]),
                         sorted(blocked))

    def test_migrate_keeps_frozen_note_byte_identical(self):
        legacy = self.root / SD / "legacy.md"
        write(legacy, """---
type: note
title: Frozen Legacy
tags:
  - doc/note
---

# Frozen Legacy

See [the landscape](landscape.md).

""" + NAV.format(peers=(
            "[[solution-design/landscape|Landscape]] -\n"
            "[[solution-design/decisions/order-events-decision|SD-001]]")))
        write(self.root.parent / "work-orders" / "wo-9" / "freeze.json",
              json.dumps({"frozen_paths":
                          ["workspace/docs/solution-design/legacy.md"]}))
        before = legacy.read_bytes()
        code, out, _ = run(["migrate", "--vault", str(self.root)])
        self.assertEqual(code, 0, out)
        self.assertEqual(legacy.read_bytes(), before)

    def test_migrate_exclude_skips_note(self):
        legacy = self.root / SD / "legacy.md"
        write(legacy, """---
type: note
title: Excluded Legacy
tags:
  - doc/note
---

# Excluded Legacy

See [the landscape](landscape.md).

""" + NAV.format(peers=(
            "[[solution-design/landscape|Landscape]] -\n"
            "[[solution-design/decisions/order-events-decision|SD-001]]")))
        before = legacy.read_bytes()
        code, _, _ = run(["migrate", "--vault", str(self.root),
                          "--exclude", f"{SD}/legacy.md"])
        self.assertEqual(code, 0)
        self.assertEqual(legacy.read_bytes(), before)

    def test_migrate_rewrites_bare_citation_cells(self):
        scratch = self.root / BA_SPACE / "cites-scratch.md"
        write(scratch, "| ref | cites |\n|---|---|\n| a | DEC-ERP-001 |\n")
        code, _, _ = run(["migrate", "--vault", str(self.root)])
        self.assertEqual(code, 0)
        text = scratch.read_text(encoding="utf-8")
        self.assertIn(
            "[[business-analysis/erp/decisions/pilot-scope-decision"
            "\\|DEC-ERP-001]]", text)

    def test_migrate_retargets_nav_first_link_to_hub(self):
        dec = self.root / BA_SPACE / "decisions" / "pilot-scope-decision.md"
        edit(dec,
             "[[business-analysis/erp/space|ERP Analysis]] -\n"
             "[[maps/business-analysis|Business Analysis]]",
             "[[maps/business-analysis|Business Analysis]] -\n"
             "[[business-analysis/erp/space|ERP Analysis]]")
        code, _, _ = run(["migrate", "--vault", str(self.root)])
        self.assertEqual(code, 0)
        text = dec.read_text(encoding="utf-8")
        nav = text.split("<!-- sec: nav -->", 1)[1]
        links = vc.WIKILINK_RE.findall(nav)
        # promoted to first; the map link dropped instead of duplicated
        self.assertIn("business-analysis/erp/space", links[0][1])
        self.assertEqual(
            sum("erp/space" in inner for _e, inner in links), 1)

    def test_migrate_converts_scalar_governs_to_block_list(self):
        target = self.root / SD / "landscape.md"
        edit(target, "status: approved\n",
             "status: approved\ngoverns: \"[[solution-design/landscape]]\"\n")
        code, _, _ = run(["migrate", "--vault", str(self.root)])
        self.assertEqual(code, 0)
        text = target.read_text(encoding="utf-8")
        self.assertIn(
            "governs:\n  - \"[[solution-design/landscape]]\"", text)
        code, findings = check_findings(self.root)
        self.assertEqual(code, 0,
                         [f"{f['check']}: {f['message']}" for f in findings])

    def test_migrate_deid_leads_decision_title_and_h1(self):
        """The v5 '<ID>: ' lead leaves the title AND the first H1 in one
        write; the alias keeps the id."""
        target = self.root / DEC / "order-events-decision.md"
        edit(target, "title: Order events v1 decision",
             'title: "SD-001: Order events v1 decision"')
        edit(target, "# Order events v1 decision",
             "# SD-001: Order events v1 decision")
        code, _, _ = run(["migrate", "--vault", str(self.root)])
        self.assertEqual(code, 0)
        text = target.read_text(encoding="utf-8")
        self.assertIn("title: Order events v1 decision", text)
        self.assertIn("# Order events v1 decision", text)
        self.assertNotIn("SD-001: Order events", text)
        code, findings = check_findings(self.root)
        self.assertEqual(code, 0,
                         [f"{f['check']}: {f['message']}" for f in findings])

    def test_migrate_appends_missing_designation_title_and_h1(self):
        """A typed title lacking its designation gains it in migrate, and
        the first H1 is rewritten in the SAME write; the append is
        idempotent (the same fold/word-boundary test the check uses)."""
        space = self.root / BA_SPACE / "space.md"
        edit(space, "title: ERP analysis space overview",
             "title: ERP analysis")
        edit(space, "# ERP analysis space overview", "# ERP analysis")
        code, findings = check_findings(self.root)
        self.assertEqual(code, 1)  # the missing designation is an error
        code, out, _ = run(["migrate", "--vault", str(self.root)])
        self.assertEqual(code, 0, out)
        text = space.read_text(encoding="utf-8")
        self.assertIn("title: ERP analysis space overview", text)
        self.assertIn("# ERP analysis space overview", text)
        before = space.read_bytes()
        code, _, _ = run(["migrate", "--vault", str(self.root)])
        self.assertEqual(code, 0)
        self.assertEqual(space.read_bytes(), before)
        code, findings = check_findings(self.root)
        self.assertEqual(code, 0,
                         [f"{f['check']}: {f['message']}" for f in findings])

    def test_migrate_deletes_retired_scaffold_unscoped(self):
        """Red-team 4: the retired scaffold files leave the vault inside
        ANY migrate run (scope or not), and a scoped gate on a v5-shaped
        root passes right after (the deadlock regression); the class is
        idempotent."""
        write(self.root / "backlog.md",
              "<!-- generated by pmo render; do not edit by hand -->\n\n"
              "# Backlog\n")
        write(self.root / "quality-ledger.md",
              "<!-- generated by pmo render; do not edit by hand -->\n\n"
              "# Quality Ledger\n")
        write(self.root / "start-here.md", """---
type: guide
title: Start Here
tags:
  - doc/guide
---

# Start Here

How to read this vault.
""")
        write(self.root / "maps" / "delivery.md", """---
type: moc
title: Delivery
tags:
  - doc/moc
---

# Delivery

- [[backlog|Backlog]]
- [[quality-ledger|Quality Ledger]]
""")
        edit(self.root / "home.md",
             "- [[maps/solution-design|Solution Design]]",
             "- [[maps/solution-design|Solution Design]]\n"
             "- [[maps/delivery|Delivery]]\n"
             "- [[start-here|Start Here]]")
        code, findings = check_findings_scoped(self.root, SD)
        self.assertEqual(code, 1)  # the v5 scaffold trips the global layout
        code, out, err = run(["migrate", "--vault", str(self.root),
                              "--scope", SD])
        self.assertEqual(code, 0, out + err)
        for rel in ("backlog.md", "quality-ledger.md", "start-here.md",
                    "maps/delivery.md"):
            self.assertFalse((self.root / rel).exists(), rel)
        home_text = (self.root / "home.md").read_text(encoding="utf-8")
        self.assertNotIn("start-here", home_text)
        self.assertNotIn("maps/delivery", home_text)
        code, findings = check_findings_scoped(self.root, SD)
        self.assertEqual(code, 0,
                         [f"{f['check']}: {f['message']}" for f in findings])
        # idempotent: a second plain run changes nothing and stays green
        before = (self.root / "home.md").read_bytes()
        code, _, _ = run(["migrate", "--vault", str(self.root)])
        self.assertEqual(code, 0)
        self.assertEqual((self.root / "home.md").read_bytes(), before)
        code, findings = check_findings(self.root)
        self.assertEqual(code, 0,
                         [f"{f['check']}: {f['message']}" for f in findings])

    def test_payload_reconcile_heals_asserted_keys_only(self):
        graph_path = self.root / ".obsidian" / "graph.json"
        data = json.loads(graph_path.read_text(encoding="utf-8"))
        data["search"] = ""
        data["colorGroups"] = []
        data["scale"] = 2  # a consumer-tuned knob the law does not assert
        graph_path.write_text(json.dumps(data), encoding="utf-8")
        (self.root / ".obsidian" / "community-plugins.json").unlink()
        code, out, _ = run(["migrate", "--vault", str(self.root)])
        self.assertEqual(code, 0, out)
        healed = json.loads(graph_path.read_text(encoding="utf-8"))
        self.assertEqual([g["query"] for g in healed["colorGroups"]],
                         POLICY["graph_color_groups"])
        self.assertEqual(healed["search"], POLICY["graph_search"])
        self.assertEqual(healed["scale"], 2)
        enabled = json.loads(
            (self.root / ".obsidian" / "community-plugins.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(enabled, POLICY["community_plugins"])
        code, findings = check_findings(self.root)
        self.assertEqual(code, 0,
                         [f"{f['check']}: {f['message']}" for f in findings])


class DesignSystemWriterTests(unittest.TestCase):
    """The design-system persist script is a vault writer: its outputs are
    born compliant (frontmatter floor, tag mirror, nav section)."""

    @classmethod
    def setUpClass(cls):
        scripts = (REPO / "plugins" / "software-engineering-team" / "skills"
                   / "ui-ux-design" / "scripts")
        sys.path.insert(0, str(scripts))
        try:
            spec = importlib.util.spec_from_file_location(
                "design_system", scripts / "design_system.py")
            cls.ds = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.ds)
        finally:
            sys.path.remove(str(scripts))

    def assert_born_compliant(self, text: str, doc_type: str,
                              designation: str):
        fm, _, fm_error = ba.parse_frontmatter(text)
        self.assertIsNone(fm_error, text[:120])
        self.assertEqual(fm.get("type"), doc_type)
        self.assertEqual(fm.get("tags"), [f"doc/{doc_type.replace('_', '-')}"])
        self.assertIn("<!-- sec: nav -->", text)
        self.assertIn("[[maps/design-system|Design System]]", text)
        # Born-compliant title: carries the canonical English designation
        # (word-boundary check) and the first H1 is byte-identical to it.
        title = fm.get("title")
        self.assertTrue(vc.designation_present(title, designation),
                        f"title {title!r} lacks designation {designation!r}")
        self.assertIn(f"# {title}\n", text)

    def test_master_is_born_compliant(self):
        text = self.ds.format_master_md({"project_name": "Shop"})
        self.assert_born_compliant(text, "design_master", "design master")

    def test_page_override_is_born_compliant(self):
        text = self.ds.format_page_override_md(
            {"project_name": "Shop"}, "dashboard")
        self.assert_born_compliant(text, "page_override", "page override")
        self.assertIn("[[design-system/MASTER|Design Master]]", text)


class TemplateParityTests(unittest.TestCase):
    def test_template_types_json_derives_from_policy(self):
        template = json.loads(
            (REPO / "plugins" / "software-engineering-team" / "templates"
             / "vault" / ".obsidian" / "types.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(template.get("types"), POLICY["property_types"])


class HookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "proj"
        self.vault = self.project / "workspace" / "docs"
        make_valid_vault(self.vault)

    def tearDown(self):
        self.tmp.cleanup()

    def hook(self, fn, payload):
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            code = fn(payload)
        return code, err.getvalue()

    def payload(self, rel: str, content: str, tool="Write") -> dict:
        return {"tool_name": tool,
                "tool_input": {"file_path": str(self.vault / rel),
                               "content": content}}

    def test_pre_denies_vault_internal_markdown_link(self):
        code, err = self.hook(vh.pre, self.payload(
            "solution-design/new.md", "See [the landscape](../landscape.md)."))
        self.assertEqual(code, 2)
        self.assertIn("wikilink", err)

    def test_pre_denies_inline_flow_list(self):
        code, err = self.hook(vh.pre, self.payload(
            "solution-design/new.md", "---\ntags: [doc/note]\n---\n"))
        self.assertEqual(code, 2)
        self.assertIn("block list", err)

    def test_pre_denies_unescaped_table_pipe(self):
        code, err = self.hook(vh.pre, self.payload(
            "solution-design/new.md",
            "| x | [[solution-design/landscape|Landscape]] |"))
        self.assertEqual(code, 2)
        self.assertIn("pipe", err)

    def test_pre_passes_compliant_content_and_foreign_paths(self):
        code, _ = self.hook(vh.pre, self.payload(
            "solution-design/new.md",
            "See [[solution-design/landscape|the landscape]] and"
            " [docs](https://example.com) and"
            " [the sketch](../../sketches/a/preview.html)."))
        self.assertEqual(code, 0)
        code, _ = self.hook(vh.pre, {
            "tool_name": "Write",
            "tool_input": {"file_path": str(self.project / "src" / "x.md"),
                           "content": "[a](b.md)"}})
        self.assertEqual(code, 0)

    def test_post_surfaces_findings_for_the_changed_file(self):
        target = self.vault / SD / "landscape.md"
        edit(target, "The components and their owning decisions.",
             "See [[solution-design/missing-note|missing]].")
        code, err = self.hook(vh.post, self.payload(
            f"{SD}/landscape.md", "ignored"))
        self.assertEqual(code, 2)
        self.assertIn("wikilink_resolution", err)
        self.assertIn("repair them in this session", err)

    def test_post_is_silent_on_a_green_write(self):
        code, err = self.hook(vh.post, self.payload(
            f"{SD}/landscape.md", "ignored"))
        self.assertEqual(code, 0, err)


class DesignationCheckTests(unittest.TestCase):
    """The deterministic title-designation slice: word-boundary containment
    against the config map, standalone round number, and the fail-closed
    map-absence and map-coverage findings."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "docs"
        make_valid_vault(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def has(self, findings, severity, needle):
        return any(f["check"] == "title_shape" and f["severity"] == severity
                   and needle in f["message"] for f in findings)

    def test_mid_word_false_pass_fires(self):
        """'Identity management' embeds 'entity' inside 'identity'; the left
        word-boundary match rejects it where a bare substring would pass."""
        write(self.root / SD / "identity-entity.md",
              typed_note("entity", "Identity management"))
        code, findings = check_findings(self.root)
        self.assertEqual(code, 1)
        self.assertTrue(self.has(findings, "error",
                                 "does not carry the 'entity' designation"),
                        findings)

    def test_designation_missing_fires(self):
        edit(self.root / BA_SPACE / "space.md",
             "title: ERP analysis space overview", "title: ERP analysis")
        edit(self.root / BA_SPACE / "space.md",
             "# ERP analysis space overview", "# ERP analysis")
        code, findings = check_findings(self.root)
        self.assertEqual(code, 1)
        self.assertTrue(self.has(findings, "error",
                                 "does not carry the 'space' designation"),
                        findings)

    def test_review_round_number_missing_fires(self):
        """A challenge-record title carrying the designation but not its
        round number as a standalone token is an error."""
        write(self.root / BA_SPACE / "reviews" / "space-round-2-review.md",
              typed_note("challenge_record", "Payments review round",
                         "status: draft\nowner_role: business_analyst\n"
                         "round: 2\nreview_scope: space\n"
                         "verdict: converged\n"))
        code, findings = check_findings(self.root)
        self.assertEqual(code, 1)
        self.assertTrue(self.has(
            findings, "error",
            "does not carry its round number 2 as a standalone token"),
            findings)

    def test_review_round_digit_boundary(self):
        """The round number is a STANDALONE token: a '2' buried inside '12'
        does not satisfy round 2."""
        write(self.root / BA_SPACE / "reviews" / "space-round-2-review.md",
              typed_note("challenge_record", "Payments review round 12",
                         "status: draft\nowner_role: business_analyst\n"
                         "round: 2\nreview_scope: space\n"
                         "verdict: converged\n"))
        code, findings = check_findings(self.root)
        self.assertEqual(code, 1)
        self.assertTrue(self.has(
            findings, "error", "round number 2 as a standalone token"),
            findings)
        # the same title with the standalone number passes the round check
        edit(self.root / BA_SPACE / "reviews" / "space-round-2-review.md",
             "Payments review round 12", "Payments review round 2")
        code, findings = check_findings(self.root)
        self.assertFalse(self.has(findings, "error", "standalone token"),
                         findings)

    def test_unconfigured_config_warns(self):
        (self.root.parent / "config.json").unlink()
        code, findings = check_findings(self.root)
        self.assertEqual(code, 0)  # warnings only, never a hard block
        self.assertTrue(self.has(findings, "warning",
                                 "type designations are not configured"),
                        findings)

    def test_unreadable_config_warns_fail_closed(self):
        (self.root.parent / "config.json").write_text("{ not json",
                                                       encoding="utf-8")
        code, findings = check_findings(self.root)
        self.assertEqual(code, 0)
        self.assertTrue(self.has(findings, "warning",
                                 "type designations are not configured"),
                        findings)

    def test_map_coverage_missing_type_warns(self):
        partial = dict(DESIGNATIONS)
        del partial["decision"]
        write_config(self.root, partial)
        code, findings = check_findings(self.root)
        self.assertEqual(code, 0)  # a missing type is the mint-duty warning
        self.assertTrue(self.has(
            findings, "warning",
            "doc type 'decision' has no designation in the configured map"),
            findings)

    def test_map_coverage_unknown_key_errors(self):
        extra = dict(DESIGNATIONS)
        extra["ghost"] = "specter"
        write_config(self.root, extra)
        code, findings = check_findings(self.root)
        self.assertEqual(code, 1)
        self.assertTrue(self.has(
            findings, "error",
            "designation map key 'ghost' names no known doc type"),
            findings)


class DesignationTransformTests(unittest.TestCase):
    """The raw-offset tail transform: fold() is the equality oracle only,
    never an index space (it changes string length on the Turkish dotted
    I), and stripping is tail-only, longest-first, never emptying."""

    def test_fold_changes_length_and_strip_survives_it(self):
        self.assertNotEqual(len(vc.fold("İ")), len("İ"))
        self.assertEqual(
            vc.strip_designation_tails("İlk sürümde tam paket kararı",
                                       ["kararı"]),
            "İlk sürümde tam paket")

    def test_double_suffix_and_duplicate_strip(self):
        self.assertEqual(
            vc.strip_designation_tails("İlk sürüm kararı hüküm",
                                       ["hüküm", "kararı"]),
            "İlk sürüm")
        self.assertEqual(
            vc.strip_designation_tails("Foo kararı kararı", ["kararı"]),
            "Foo")

    def test_longest_fold_first_beats_substring_sibling(self):
        self.assertEqual(
            vc.strip_designation_tails("X inceleme turu",
                                       ["turu", "inceleme turu"]),
            "X")

    def test_whole_title_designation_never_stripped(self):
        self.assertEqual(
            vc.strip_designation_tails("Aktörler", ["Aktörler"]),
            "Aktörler")

    def test_tail_only_never_mid_title(self):
        # another type's designation mid-title is legitimate vocabulary
        self.assertEqual(
            vc.strip_designation_tails("Tenant hiyerarşi kuralları kararı",
                                       ["hüküm", "kararı"]),
            "Tenant hiyerarşi kuralları")

    def test_agglutinated_tail_matches_punctuation_does_not(self):
        self.assertEqual(vc.designation_tail_span("Tenant kararına",
                                                  "kararı"), 7)
        self.assertIsNone(vc.designation_tail_span("X kararı (taslak)",
                                                   "kararı"))

    def test_round_token_peeling(self):
        self.assertEqual(
            vc.designation_tail_span("Foundation inceleme turu 3",
                                     "inceleme turu", 3), 11)
        self.assertIsNone(
            vc.designation_tail_span("Foundation inceleme turu 3",
                                     "inceleme turu", 2))
        self.assertEqual(
            vc.peel_trailing_token("Foundation inceleme turu 3", "3"),
            "Foundation inceleme turu")
        self.assertIsNone(vc.peel_trailing_token("Tur 3 incelemesi", "3"))

    def test_apply_retitle_requotes_and_syncs_h1(self):
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "n.md"
            write(note, "---\ntype: decision\ntitle: Esnek model kararı\n"
                        "---\n\n# Esnek model kararı\n\nBody.\n")
            self.assertTrue(vc.apply_retitle(
                Path(tmp), "n.md", "Esnek model kararı",
                "Esnek model: hüküm"))
            text = note.read_text(encoding="utf-8")
            self.assertIn('title: "Esnek model: hüküm"', text)
            self.assertIn("# Esnek model: hüküm", text)
            self.assertIn("Body.", text)


class DesignationDriftTests(unittest.TestCase):
    """The ledger-backed drift check: stale tails are errors (locked:
    warnings), mid-title strandings and non-closing designations warn,
    ledger hygiene lands on config.json, and a vault with no ledger is
    silent (green stays green by construction)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "docs"
        make_valid_vault(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def drift(self, findings, severity, needle):
        return any(f["check"] == "designation_drift"
                   and f["severity"] == severity
                   and needle in f["message"] for f in findings)

    def test_stale_tail_under_current_designation_errors(self):
        # the exact silent-green repro, inverted: double suffix now RED
        ruling = dict(DESIGNATIONS, decision="ruling")
        write_config(self.root, ruling,
                     history={"decision": [{"value": "decision"}]})
        edit(self.root / BA_SPACE / "decisions" / "pilot-scope-decision.md",
             "title: Pilot scope decision", "title: Pilot scope decision ruling")
        edit(self.root / BA_SPACE / "decisions" / "pilot-scope-decision.md",
             "# Pilot scope decision", "# Pilot scope decision ruling")
        code, findings = check_findings(self.root)
        self.assertEqual(code, 1)
        self.assertTrue(self.drift(
            findings, "error", "retired 'decision' designation 'decision'"),
            findings)

    def test_no_ledger_double_suffix_stays_green_stated_limit(self):
        # without the ledger the drift check has no memory: the shape
        # verifies green, the documented limit --from exists for
        ruling = dict(DESIGNATIONS, decision="ruling")
        write_config(self.root, ruling)
        for old, new in (("title: Pilot scope decision",
                          "title: Pilot scope decision ruling"),
                         ("# Pilot scope decision",
                          "# Pilot scope decision ruling"),
                         ("title: Order events v1 decision",
                          "title: Order events v1 ruling"),
                         ("# Order events v1 decision",
                          "# Order events v1 ruling"),
                         ("title: Order events v2 decision",
                          "title: Order events v2 ruling"),
                         ("# Order events v2 decision",
                          "# Order events v2 ruling")):
            for name in ("business-analysis/erp/decisions/"
                         "pilot-scope-decision.md",
                         "solution-design/decisions/"
                         "order-events-decision.md",
                         "solution-design/decisions/"
                         "order-events-v2-decision.md"):
                path = self.root / name
                text = path.read_text(encoding="utf-8")
                if old in text:
                    path.write_text(text.replace(old, new),
                                    encoding="utf-8")
        run(["render-decisions", "--vault", str(self.root)])
        code, findings = check_findings(self.root)
        self.assertEqual(code, 0, findings)

    def test_mid_title_stranding_warns(self):
        write_config(self.root, DESIGNATIONS,
                     history={"decision": [{"value": "scope"}]})
        edit(self.root / BA_SPACE / "decisions" / "pilot-scope-decision.md",
             "title: Pilot scope decision",
             "title: Pilot scope refinement decision")
        edit(self.root / BA_SPACE / "decisions" / "pilot-scope-decision.md",
             "# Pilot scope decision", "# Pilot scope refinement decision")
        code, findings = check_findings(self.root)
        self.assertEqual(code, 0)
        self.assertTrue(self.drift(findings, "warning", "appears mid-title"),
                        findings)

    def test_locked_stale_warns_never_errors(self):
        write_config(self.root, DESIGNATIONS,
                     history={"challenge-record": [{"value": "audit round"}]})
        write(self.root / BA_SPACE / "reviews" / "space-round-2-review.md",
              typed_note("challenge_record", "Payments audit round 2",
                         "status: approved\nowner_role: business_analyst\n"
                         "round: 2\nreview_scope: space\n"
                         "verdict: continue\nlocked: true\n"
                         "approved_at: 2026-07-01\n"))
        _code, findings = check_findings(self.root)
        # the locked record's designation findings are warnings, never
        # errors (the unwired fixture note's graph findings are not the
        # subject here)
        rel = f"{BA_SPACE}/reviews/space-round-2-review.md"
        designation = [f for f in findings if f["path"] == rel
                       and f["check"] in ("designation_drift",
                                          "title_shape")]
        self.assertTrue(designation, findings)
        self.assertTrue(all(f["severity"] == "warning"
                            for f in designation), designation)
        self.assertTrue(self.drift(
            findings, "warning", "retired 'challenge-record'"), findings)
        self.assertTrue(any(
            f["check"] == "title_shape" and f["severity"] == "warning"
            and "locked record" in f["message"]
            and "--include-locked" in f["remediation"]
            for f in designation), designation)

    def test_whole_title_retired_designation_warns_not_errors(self):
        # 'Landscape' whose designation moves on: the drift finding is
        # a judgment warning (mechanical transition would empty the
        # base); the missing-designation error stays title_shape's
        write_config(self.root, dict(DESIGNATIONS, landscape="peyzaj"),
                     history={"landscape": [{"value": "landscape"}]})
        code, findings = check_findings(self.root)
        self.assertEqual(code, 1)
        drift = [f for f in findings if f["check"] == "designation_drift"
                 and f["path"] == f"{SD}/landscape.md"]
        self.assertTrue(drift, findings)
        self.assertTrue(all(f["severity"] == "warning" for f in drift),
                        drift)
        self.assertTrue(any("IS the retired" in f["message"]
                            for f in drift), drift)

    def test_current_designation_not_closing_warns(self):
        write_config(self.root, dict(DESIGNATIONS, decision="ruling"),
                     history={"decision": [{"value": "decision"}]})
        edit(self.root / BA_SPACE / "decisions" / "pilot-scope-decision.md",
             "title: Pilot scope decision", "title: Ruling on pilot scope")
        edit(self.root / BA_SPACE / "decisions" / "pilot-scope-decision.md",
             "# Pilot scope decision", "# Ruling on pilot scope")
        code, findings = check_findings(self.root)
        self.assertTrue(self.drift(findings, "warning",
                                   "not the closing phrase"), findings)

    def test_ledger_hygiene_errors_on_config(self):
        write_config(self.root, DESIGNATIONS,
                     history={"ghost": [{"value": "specter"}],
                              "decision": [{"value": "decision"}]})
        code, findings = check_findings(self.root)
        self.assertEqual(code, 1)
        config_findings = [f for f in findings
                           if f["check"] == "designation_drift"
                           and f["path"] == "config.json"]
        messages = " | ".join(f["message"] for f in config_findings)
        self.assertIn("history key 'ghost'", messages)
        self.assertIn("repeats the current designation", messages)

    def test_changed_fast_path_emits_drift(self):
        write_config(self.root, dict(DESIGNATIONS, decision="ruling"),
                     history={"decision": [{"value": "decision"}]})
        rel = f"{BA_SPACE}/decisions/pilot-scope-decision.md"
        edit(self.root / rel, "title: Pilot scope decision",
             "title: Pilot scope decision ruling")
        edit(self.root / rel, "# Pilot scope decision",
             "# Pilot scope decision ruling")
        code, out, _ = run(["check", "--vault", str(self.root),
                            "--changed", rel, "--json"])
        findings = [json.loads(line) for line in out.splitlines()
                    if line.startswith("{")]
        self.assertEqual(code, 1)
        self.assertTrue(any(f["check"] == "designation_drift"
                            for f in findings), findings)


def tree_snapshot(base: Path) -> dict:
    return {p.relative_to(base).as_posix(): p.read_bytes()
            for p in sorted(base.rglob("*")) if p.is_file()}


class ReconcileVerbTests(unittest.TestCase):
    """The reconcile-designations verb: plan/apply split with a real
    dry-run, old -> new transitions with no double suffix, idempotent
    re-runs, locked-record consent and audit, and the migrate second
    door held shut."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "proj" / "workspace"
        self.root = self.project / "docs"
        make_valid_vault(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def reconcile(self, *argv):
        return run(["reconcile-designations", "--vault", str(self.root)]
                   + list(argv))

    def read(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    def test_dry_run_plans_and_writes_nothing(self):
        before = tree_snapshot(self.project)
        code, out, err = self.reconcile("--set", "decision=ruling",
                                        "--dry-run", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(tree_snapshot(self.project), before)
        plan = json.loads(out)
        self.assertTrue(plan["dry_run"])
        news = {r["path"]: r["new_title"] for r in plan["retitles"]}
        self.assertEqual(
            news[f"{BA_SPACE}/decisions/pilot-scope-decision.md"],
            "Pilot scope ruling")
        self.assertEqual(
            news[f"{DEC}/order-events-v2-decision.md"],
            "Order events v2 ruling")
        self.assertEqual(plan["config"]["history_appends"],
                         [{"type": "decision", "value": "decision"}])

    def test_execute_transitions_titles_h1_config_and_index(self):
        # a curated alias byte-equal to the old title is swept too
        edit(self.root / "maps" / "solution-design.md",
             "- [[solution-design/decision-log|Decision Index]]",
             "- [[solution-design/decision-log|Decision Index]]\n"
             "- [[solution-design/decisions/order-events-v2-decision"
             "|Order events v2 decision]]")
        code, out, err = self.reconcile("--set", "decision=ruling")
        self.assertEqual(code, 0, err)
        pilot = self.read(f"{BA_SPACE}/decisions/pilot-scope-decision.md")
        self.assertIn("title: Pilot scope ruling", pilot)
        self.assertIn("# Pilot scope ruling", pilot)
        self.assertNotIn("decision ruling", pilot)
        self.assertIn("|Order events v2 ruling]]",
                      self.read("maps/solution-design.md"))
        index = self.read(f"{SD}/decision-log.md")
        self.assertIn("Order events v2 ruling", index)
        config = json.loads((self.project / "config.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(config["doc_type_designations"]["decision"],
                         "ruling")
        entry = config["doc_type_designation_history"]["decision"][0]
        self.assertEqual(entry["value"], "decision")
        self.assertEqual(entry["superseded_by"], "ruling")
        code, findings = check_findings(self.root)
        self.assertEqual(code, 0, findings)

    def test_second_run_is_byte_identical(self):
        code, _, err = self.reconcile("--set", "decision=ruling")
        self.assertEqual(code, 0, err)
        before = tree_snapshot(self.project)
        code, _, err = self.reconcile("--set", "decision=ruling")
        self.assertEqual(code, 0, err)
        self.assertEqual(tree_snapshot(self.project), before)

    def test_locked_skipped_by_default_included_with_audit(self):
        write_config(self.root, DESIGNATIONS, project_key="erp")
        rel = f"{BA_SPACE}/reviews/space-round-2-review.md"
        write(self.root / rel,
              typed_note("challenge_record", "ERP review round 2",
                         "status: approved\nowner_role: business_analyst\n"
                         "round: 2\nreview_scope: space\n"
                         "verdict: continue\nlocked: true\n"
                         "approved_at: 2026-07-01\n"))
        stub = self.project.parent / "pmo_stub.py"
        log = self.project.parent / "events.jsonl"
        stub.write_text(
            "import json, sys, pathlib\n"
            f"path = pathlib.Path({str(log)!r})\n"
            "path.open('a').write(json.dumps(sys.argv[1:]) + '\\n')\n",
            encoding="utf-8")
        before = (self.root / rel).read_bytes()
        code, out, err = self.reconcile(
            "--set", "challenge-record=assessment round")
        self.assertEqual(code, 0, err)
        self.assertEqual((self.root / rel).read_bytes(), before)
        self.assertIn("LOCKED skipped", out)
        code, out, err = self.reconcile(
            "--set", "challenge-record=assessment round",
            "--include-locked", "--pmo-launcher", str(stub))
        self.assertEqual(code, 0, err)
        text = self.read(rel)
        self.assertIn("title: ERP assessment round 2", text)
        self.assertIn("# ERP assessment round 2", text)
        self.assertIn("verdict: continue", text)
        self.assertIn("locked: true", text)
        events = [json.loads(line)
                  for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(events), 1)
        self.assertIn("designation_relabel", events[0])
        self.assertIn("erp", events[0])

    def test_from_override_heals_a_ledgerless_swap(self):
        write_config(self.root, dict(DESIGNATIONS, decision="ruling"))
        code, _, err = self.reconcile("--set", "decision=ruling",
                                      "--from", "decision=decision")
        self.assertEqual(code, 0, err)
        self.assertIn("title: Pilot scope ruling",
                      self.read(f"{BA_SPACE}/decisions/"
                                "pilot-scope-decision.md"))
        config = json.loads((self.project / "config.json")
                            .read_text(encoding="utf-8"))
        # --from is never recorded: it was not a configured value
        self.assertNotIn("doc_type_designation_history", config)

    def test_manual_cases_named_not_touched(self):
        write(self.root / BA_SPACE / "actors.md",
              typed_note("actor_roster", "Actors"))
        write(self.root / BA_SPACE / "reviews" / "round-1-review.md",
              typed_note("challenge_record", "Tur 1 incelemesi",
                         "status: draft\nowner_role: business_analyst\n"
                         "round: 1\nreview_scope: space\n"))
        code, out, _ = self.reconcile(
            "--set", "actor-roster=cast",
            "--set", "challenge-record=assessment round",
            "--dry-run", "--json")
        self.assertEqual(code, 0)
        plan = json.loads(out)
        reasons = {m["path"]: m["reason"] for m in plan["manual"]}
        self.assertIn("only its designation",
                      reasons[f"{BA_SPACE}/actors.md"])
        self.assertIn("round number",
                      reasons[f"{BA_SPACE}/reviews/round-1-review.md"])

    def test_migrate_second_door_stays_shut(self):
        # config already changed with the ledger recorded, titles stale:
        # migrate must NOT append the new value over the retired tail
        write_config(self.root, dict(DESIGNATIONS, decision="ruling"),
                     history={"decision": [{"value": "decision"}]})
        code, _, err = run(["migrate", "--vault", str(self.root)])
        self.assertEqual(code, 0, err)
        pilot = self.read(f"{BA_SPACE}/decisions/pilot-scope-decision.md")
        self.assertIn("title: Pilot scope decision", pilot)
        self.assertNotIn("decision ruling", pilot)

    def test_migrate_content_dry_run_writes_nothing(self):
        edit(self.root / SD / "landscape.md",
             "The components and their owning decisions.",
             "See [the decision](decisions/order-events-decision.md).")
        before = tree_snapshot(self.project)
        code, out, err = run(["migrate", "--vault", str(self.root),
                              "--dry-run"])
        self.assertEqual(code, 0, err)
        self.assertEqual(tree_snapshot(self.project), before)
        self.assertIn("dry run", out)

    def test_unknown_type_and_missing_config_refused(self):
        code, _, err = self.reconcile("--set", "ghost=x")
        self.assertEqual(code, 2)
        self.assertIn("no known doc type", err)
        (self.project / "config.json").unlink()
        code, _, err = self.reconcile("--set", "decision=ruling")
        self.assertEqual(code, 2)
        self.assertIn("setup entry", err)


class ConfigGuardHookTests(unittest.TestCase):
    """The write-time single-writer guard on workspace/config.json: no
    Write/Edit ever changes the designation keys (mint included); the
    sanctioned verb writes via subprocess and never meets this hook."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "proj"
        self.vault = self.project / "workspace" / "docs"
        make_valid_vault(self.vault)
        self.config = self.project / "workspace" / "config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def hook(self, tool_input: dict, tool="Write"):
        err = io.StringIO()
        payload = {"tool_name": tool, "tool_input": tool_input}
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            code = vh.pre(payload)
        return code, err.getvalue()

    def config_payload(self, mutate=None) -> str:
        data = json.loads(self.config.read_text(encoding="utf-8"))
        if mutate:
            mutate(data)
        return json.dumps(data, indent=2)

    def test_edit_naming_the_key_denied(self):
        code, err = self.hook(
            {"file_path": str(self.config),
             "old_string": '"doc_type_designations"', "new_string": "x"},
            tool="Edit")
        self.assertEqual(code, 2)
        self.assertIn("reconcile-designations", err)

    def test_edit_fragment_inside_guarded_block_denied(self):
        code, err = self.hook(
            {"file_path": str(self.config),
             "old_string": '"decision": "decision"',
             "new_string": '"decision": "ruling"'},
            tool="Edit")
        self.assertEqual(code, 2)
        self.assertIn("machine-managed", err)

    def test_edit_of_unrelated_key_passes(self):
        code, err = self.hook(
            {"file_path": str(self.config),
             "old_string": '"terminology_language": "English"',
             "new_string": '"terminology_language": "German"'},
            tool="Edit")
        self.assertEqual(code, 0, err)

    def test_write_changing_the_subtree_denied(self):
        content = self.config_payload(
            lambda d: d["doc_type_designations"].update(decision="ruling"))
        code, err = self.hook({"file_path": str(self.config),
                               "content": content})
        self.assertEqual(code, 2)
        self.assertIn("single writer", err)

    def test_write_preserving_the_subtree_passes(self):
        content = self.config_payload(
            lambda d: d.update(terminology_language="German"))
        code, err = self.hook({"file_path": str(self.config),
                               "content": content})
        self.assertEqual(code, 0, err)

    def test_write_introducing_over_absent_denied_mint_is_the_verbs(self):
        fresh = self.project / "other" / "workspace" / "config.json"
        code, err = self.hook({
            "file_path": str(fresh),
            "content": json.dumps({"doc_type_designations": {}})})
        self.assertEqual(code, 2)
        self.assertIn("mint through the same verb", err)
        code, err = self.hook({
            "file_path": str(fresh),
            "content": json.dumps({"project_key": "erp"})})
        self.assertEqual(code, 0, err)

    def test_unparseable_write_denied(self):
        code, err = self.hook({"file_path": str(self.config),
                               "content": "{ broken"})
        self.assertEqual(code, 2)
        self.assertIn("unparseable", err)

    def test_foreign_config_json_untouched(self):
        code, err = self.hook({"file_path": str(self.project / "frontend"
                                                / "config.json"),
                               "content": "{ broken"})
        self.assertEqual(code, 0, err)


class FoldUnitTests(unittest.TestCase):
    def test_fold_is_nfkc_casefold(self):
        import unicodedata
        self.assertEqual(vc.fold("SÜREÇ"),
                         unicodedata.normalize("NFKC", "SÜREÇ").casefold())
        # the Turkish dotted-I hazard: casefold on NFKC-normalized text is
        # deterministic (no locale lower()); it does not raise.
        self.assertEqual(vc.fold("İŞ"),
                         unicodedata.normalize("NFKC", "İŞ").casefold())

    def test_designation_present_non_ascii_boundary(self):
        # boundary match on a non-ASCII designation
        self.assertTrue(vc.designation_present("Sipariş süreç", "süreç"))
        # mid-word rejection on a non-ASCII designation (left boundary)
        self.assertFalse(vc.designation_present("Fessüreç", "süreç"))




class ProjectLimitsVaultTests(unittest.TestCase):
    """workspace/config.json limits override the shipped nav-peer policy
    values; an inconsistent pair drops both overrides."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "docs"
        make_valid_vault(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def add_limits(self, limits) -> None:
        cfg = self.root.parent / "config.json"
        data = json.loads(cfg.read_text(encoding="utf-8"))
        data["limits"] = limits
        cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def test_nav_peer_override_fires_with_provenance(self):
        self.add_limits({"nav_peer_min": 1, "nav_peer_max": 1})
        code, findings = check_findings(self.root)
        navs = [f for f in findings if f["check"] == "nav_footer"]
        self.assertEqual(code, 1)
        self.assertTrue(navs, findings)
        self.assertIn("(policy range 1-1: project override)",
                      navs[0]["message"])

    def test_nav_peer_invalid_pair_dropped(self):
        self.add_limits({"nav_peer_min": 9, "nav_peer_max": 2})
        code, findings = check_findings(self.root)
        self.assertEqual(code, 0)
        self.assertEqual(findings, [],
                         [f"{f['check']}: {f['message']}" for f in findings])


if __name__ == "__main__":
    unittest.main()
