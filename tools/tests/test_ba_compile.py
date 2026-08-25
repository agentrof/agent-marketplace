"""Tests for the analysis-space compiler (plugins/software-engineering-team/scripts/
ba_compile.py), run against the SHIPPED schema so the taxonomy and the
machinery are welded together.

Doctrine mirror of test_validate.py: a BA_BUILDERS registry holds one
builder per compiler check id; each builder mutates a valid, gate-passing
space to make exactly that check fire; a meta-test keeps the registry in
lockstep with the compiler's CHECK_IDS; and the untouched valid space stays
silent."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
COMPILER = REPO / "plugins" / "software-engineering-team" / "scripts" / "ba_compile.py"
VAULT_CHECK = REPO / "plugins" / "software-engineering-team" / "scripts" / "vault_check.py"

spec = importlib.util.spec_from_file_location("ba_compile", COMPILER)
ba = importlib.util.module_from_spec(spec)
sys.modules["ba_compile"] = ba  # dataclass resolution needs the registry entry
spec.loader.exec_module(ba)
sys.path.insert(0, str(COMPILER.parent))
import stage_package  # noqa: E402

SCHEMA = ba.load_schema(ba.DEFAULT_SCHEMA)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = ba.main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def run_compiler(compiler, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = compiler.main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def load_compiler(path: Path, name: str):
    module_spec = importlib.util.spec_from_file_location(name, path)
    compiler = importlib.util.module_from_spec(module_spec)
    sys.modules[name] = compiler
    module_spec.loader.exec_module(compiler)
    return compiler


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"fixture edit target not found in {path}: {old[:60]}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def seed_vault_scaffolding(space: Path) -> None:
    """The map seed setup materializes in a real project; the stubs' nav
    links resolve against it."""
    docs_root = space.parent.parent
    write(docs_root / "maps" / "business-analysis.md",
          "---\ntype: moc\ntitle: Business Analysis\ntags:\n"
          "  - doc/moc\n---\n\n# Business Analysis\n")


def make_valid_space(space: Path) -> None:
    """A gate-passing ERP space with one inventory domain: approved docs,
    wikilink-cited rules and no review-history state. Named files carry their
    plain contract names; typed content includes its document kind."""
    seed_vault_scaffolding(space)
    write(space / "space.md", """---
type: space
title: ERP Analysis
status: approved
approved_at: 2026-07-12
owner_role: business_analyst
code: ERP
aliases:
  - ERP
---

# ERP Analysis

Analysis space for the ERP program, starting with inventory.

## Purpose Scope <!-- sec: purpose_scope -->

Track sellable stock per warehouse with auditable movements.

## Domain Map <!-- sec: domain_map -->

- [[business-analysis/erp/domains/inventory/domain|Inventory]]

## Out Of Scope <!-- sec: out_of_scope -->

Payroll and manufacturing.
""")
    write(space / "glossary.md", """---
type: glossary
title: ERP Glossary
status: approved
approved_at: 2026-07-12
owner_role: business_analyst
---

# ERP Glossary

One vocabulary for the whole space.

## Terms <!-- sec: terms -->

| term | technical_name | definition |
|---|---|---|
| movement | stock_movement | one typed quantity change against a stock item |
| warehouse zone | | a named storage area inside a warehouse |
""")
    write(space / "actors.md", """---
type: actor_roster
title: ERP Actors
status: approved
approved_at: 2026-07-12
owner_role: business_analyst
---

# ERP Actors

Roles that appear across the space.

## Actors <!-- sec: actors -->

| actor | role | permissions |
|---|---|---|
| warehouse operator | records receipts | create movements |
""")
    write(space / "budgets.md", """---
type: budget_set
title: ERP Non-Functional Budgets
status: approved
approved_at: 2026-07-12
owner_role: business_analyst
scope: space
---

# ERP Non-Functional Budgets

Space-level quantified budgets, all six categories covered.

## Performance <!-- sec: performance -->

Receipt posting completes under 2 seconds at p95 with 50 concurrent operators.

## Volume <!-- sec: volume -->

2 million movements per year, 100 thousand stock items.

## Availability <!-- sec: availability -->

None stated, confirmed.

## Security <!-- sec: security -->

Only the inventory planner role may block items.

## Compliance <!-- sec: compliance -->

None stated, confirmed.

## Operability <!-- sec: operability -->

None stated, confirmed.
""")
    write(space / "domains" / "inventory" / "domain.md", """---
type: domain
title: Inventory
status: approved
approved_at: 2026-07-12
owner_role: business_analyst
code: INV
aliases:
  - INV
---

# Inventory

Stock items, warehouses and their movements.

## Mission <!-- sec: mission -->

Every quantity change is an auditable movement.

## Boundaries <!-- sec: boundaries -->

Owns stock levels; pricing belongs to sales.

## Process Map <!-- sec: process_map -->

- [[business-analysis/erp/domains/inventory/processes/goods-receipt-process|Goods Receipt]]

## Data Notes <!-- sec: data_notes -->

| entity | note |
|---|---|
| [[business-analysis/erp/domains/inventory/entities/stock-item-entity\\|Stock Item]] | promoted: has lifecycle |
""")
    write(space / "domains" / "inventory" / "processes" / "goods-receipt-process.md", """---
type: process
title: Goods Receipt
status: approved
approved_at: 2026-07-12
owner_role: business_analyst
---

# Goods Receipt

Receiving purchased goods into a warehouse.

## Actors <!-- sec: actors -->

Warehouse operator records; inventory planner reviews.

## Trigger <!-- sec: trigger -->

A delivery arrives against a purchase order.

## Main Flow <!-- sec: main_flow -->

Operator scans items; each accepted line creates a movement for
[[business-analysis/erp/domains/inventory/entities/stock-item-entity|Stock Item]] per
[[business-analysis/erp/domains/inventory/rules/stock-item-lifecycle-rules|BR-INV-001]].

## Exception Flows <!-- sec: exception_flows -->

Damaged goods are refused at the line level.
""")
    write(space / "domains" / "inventory" / "entities" / "stock-item-entity.md", """---
type: entity
title: Stock Item
status: approved
approved_at: 2026-07-12
owner_role: business_analyst
---

# Stock Item

One sellable, storable product variant tracked per warehouse.

## Fields <!-- sec: fields -->

| field | meaning | source | frozen_when | rules |
|---|---|---|---|---|
| sku | unique identifier | product manager | after first movement | BR-INV-001 |
| on_hand_quantity | derived sum of movements | system | always | BR-INV-002 |

## Lifecycle <!-- sec: lifecycle -->

```mermaid
stateDiagram-v2
    [*] --> draft
    draft --> active: activate
    active --> retired: kullanım dışı bırak
```

```mermaid
flowchart TD
    A[Müşteri fatura oluşturur] --> B[Onaya gönderilir]
```

## Propagation <!-- sec: propagation -->

| change | propagates_to | must_not_reach | frozen_copy |
|---|---|---|---|
| name edit | future documents | issued receipts | line keeps issue-time name |
""")
    write(space / "domains" / "inventory" / "rules" / "stock-item-lifecycle-rules.md", """---
type: rule_set
title: Stock Item Lifecycle Rules
status: approved
approved_at: 2026-07-12
owner_role: business_analyst
governs:
  - "[[business-analysis/erp/domains/inventory/entities/stock-item-entity]]"
---

# Stock Item Lifecycle Rules

Lifecycle constraints for [[business-analysis/erp/domains/inventory/entities/stock-item-entity|Stock Item]].

## Rules <!-- sec: rules -->

| id | statement | kind | status | cites |
|---|---|---|---|---|
| BR-INV-001 | The sku cannot change after the first stock movement; the refusal names the movement date. | constraint | active | |
| BR-INV-002 | On-hand quantity is never edited directly; every change is a typed movement. | constraint | active | |

## Assumptions <!-- sec: assumptions -->

| id | statement | source | affects | status | opened_on |
|---|---|---|---|---|---|
| AS-INV-001 | Negative on-hand is never permitted. | owner confirmed during analysis | [[business-analysis/erp/domains/inventory/rules/stock-item-lifecycle-rules\\|BR-INV-002]] | confirmed | 2026-07-09 |
""")
    write(space / "domains" / "inventory" / "acceptance" / "goods-receipt-acceptance.md", """---
type: acceptance_set
title: Goods Receipt Criteria
status: approved
approved_at: 2026-07-12
owner_role: business_analyst
verifies:
  - "[[business-analysis/erp/domains/inventory/processes/goods-receipt-process]]"
---

# Goods Receipt Criteria

Criteria for [[business-analysis/erp/domains/inventory/processes/goods-receipt-process|Goods Receipt]].

## Criteria <!-- sec: criteria -->

| id | criterion | cites | verify | status |
|---|---|---|---|---|
| AC-INV-001 | Given an accepted line, when the receipt posts, then a movement row exists and on-hand rises by the line quantity. | [[business-analysis/erp/domains/inventory/rules/stock-item-lifecycle-rules\\|BR-INV-002]] | assert movement row and quantity delta via the stock query | active |
| AC-INV-002 | Given an item with a movement, when its sku is edited, then the edit is refused naming the movement date. | [[business-analysis/erp/domains/inventory/rules/stock-item-lifecycle-rules\\|BR-INV-001]] | attempt the edit; assert refusal message content | active |
""")
    (space / "_generated").mkdir(exist_ok=True)
    code, _, err = run(["render", "--space", str(space)])
    assert code == 0, f"valid space must render: {err}"


INV = Path("domains") / "inventory"


def break_space_layout(space: Path) -> None:
    write(space / "stray-notes.md", "---\ntype: process\ntitle: X\nstatus: draft\n"
          "owner_role: business_analyst\n---\n\n# X\n")


def break_frontmatter_schema(space: Path) -> None:
    edit(space / INV / "processes" / "goods-receipt-process.md",
         "owner_role: business_analyst\n", "")


def break_status_legality(space: Path) -> None:
    edit(space / INV / "entities" / "stock-item-entity.md",
         "status: approved", "status: shipped")


def break_required_sections(space: Path) -> None:
    edit(space / INV / "processes" / "goods-receipt-process.md",
         "## Trigger <!-- sec: trigger -->", "## Trigger")


def break_summary_caps(space: Path) -> None:
    filler = "\n".join(f"Summary filler line {i}." for i in range(12))
    edit(space / INV / "entities" / "stock-item-entity.md",
         "One sellable, storable product variant tracked per warehouse.",
         filler)


def break_content_bans(space: Path) -> None:
    edit(space / INV / "processes" / "goods-receipt-process.md",
         "Damaged goods are refused", "Damaged goods — refused")


def break_dead_links(space: Path) -> None:
    edit(space / INV / "processes" / "goods-receipt-process.md",
         "[[business-analysis/erp/domains/inventory/entities/stock-item-entity|Stock Item]]",
         "[[business-analysis/erp/domains/inventory/entities/missing|Stock Item]]")


def break_id_format(space: Path) -> None:
    edit(space / INV / "rules" / "stock-item-lifecycle-rules.md",
         "| BR-INV-002 |", "| BR-2 |")


def break_id_unique(space: Path) -> None:
    edit(space / INV / "rules" / "stock-item-lifecycle-rules.md",
         "| BR-INV-002 | On-hand quantity",
         "| BR-INV-001 | Duplicate. | constraint | active | |\n"
         "| BR-INV-002 | On-hand quantity")


def break_id_minting(space: Path) -> None:
    # A mint cell written as a wikilink: the mint declares the bare id.
    edit(space / INV / "rules" / "stock-item-lifecycle-rules.md",
         "| BR-INV-002 | On-hand quantity",
         "| [[business-analysis/erp/domains/inventory/rules/"
         "stock-item-lifecycle-rules\\|BR-INV-002]] | On-hand quantity")


def break_id_links(space: Path) -> None:
    # A bare id in an id-citation column: cells cite ids as wikilinks.
    edit(space / INV / "acceptance" / "goods-receipt-acceptance.md",
         "| [[business-analysis/erp/domains/inventory/rules/"
         "stock-item-lifecycle-rules\\|BR-INV-002]] | assert movement row",
         "| BR-INV-002 | assert movement row")


def break_row_schema(space: Path) -> None:
    edit(space / INV / "acceptance" / "goods-receipt-acceptance.md",
         "| attempt the edit; assert refusal message content |", "| |")


def break_semantic_links(space: Path) -> None:
    edit(space / "space.md",
         "- [[business-analysis/erp/domains/inventory/domain|Inventory]]",
         "The domain list is maintained elsewhere.")


def break_approval_preconditions(space: Path) -> None:
    edit(space / INV / "rules" / "stock-item-lifecycle-rules.md",
         "| confirmed | 2026-07-09 |", "| open | 2026-07-09 |")


def break_br_uncited(space: Path) -> None:
    edit(space / INV / "rules" / "stock-item-lifecycle-rules.md",
         "| BR-INV-002 | On-hand quantity",
         "| BR-INV-003 | An uncited rule. | constraint | active | |\n"
         "| BR-INV-002 | On-hand quantity")


def break_thresholds(space: Path) -> None:
    filler = "\n".join(f"Exception narration line {i}." for i in range(1400))
    edit(space / INV / "processes" / "goods-receipt-process.md",
         "Damaged goods are refused at the line level.", filler)


def break_aging(space: Path) -> None:
    edit(space / INV / "rules" / "stock-item-lifecycle-rules.md",
         "| confirmed | 2026-07-09 |", "| open | 2020-01-01 |")


def break_gate_approval(space: Path) -> None:
    edit(space / INV / "entities" / "stock-item-entity.md",
         "status: approved\napproved_at: 2026-07-12", "status: draft")


def break_generated_freshness(space: Path) -> None:
    edit(space / INV / "rules" / "stock-item-lifecycle-rules.md",
         "never edited directly", "never edited directly by hand")


def break_future_dates(space: Path) -> None:
    edit(space / "space.md", "approved_at: 2026-07-12",
         "approved_at: 9999-01-01")


def break_identifier_shape(space: Path) -> None:
    edit(space / "domains" / "inventory" / "entities" / "stock-item-entity.md",
         "| sku |", "| ürünNo |")


def break_diagram_identifiers(space: Path) -> None:
    edit(space / "domains" / "inventory" / "entities" / "stock-item-entity.md",
         "draft --> active: activate",
         "draft --> onaylandı: onayla")


BA_BUILDERS = {
    "space_layout": break_space_layout,
    "frontmatter_schema": break_frontmatter_schema,
    "status_legality": break_status_legality,
    "required_sections": break_required_sections,
    "content_bans": break_content_bans,
    "dead_links": break_dead_links,
    "id_format": break_id_format,
    "id_unique": break_id_unique,
    "id_minting": break_id_minting,
    "id_links": break_id_links,
    "row_schema": break_row_schema,
    "semantic_links": break_semantic_links,
    "approval_preconditions": break_approval_preconditions,
    "br_uncited": break_br_uncited,
    "gate_approval": break_gate_approval,
    "generated_freshness": break_generated_freshness,
    "future_dates": break_future_dates,
    "identifier_shape": break_identifier_shape,
    "diagram_identifiers": break_diagram_identifiers,
}

# Builders that mutate authored docs make the pre-built generated views
# stale as a side effect; freshness noise is expected for them. Builders
# in this set are judged on their own check id only.
GATE_CHECKS = {"gate_approval"}


def collect(space: Path, gate: bool = False) -> list:
    vault_root = ba.resolve_vault_root(space, "")
    scanned, base = ba.scan_space(space, SCHEMA)
    scanned.vault_root = vault_root
    findings = ba.run_checks(scanned, base, gate=gate, gate_node="")
    if not scanned.broken:
        warnings = [f for f in findings if f.severity == "warning"]
        findings += ba.freshness_findings(scanned, warnings)
    return findings


def write_config(space: Path, payload) -> None:
    """workspace/config.json for the test workspace (the vault root's
    parent, i.e. two levels above the space's business-analysis parent)."""
    target = space.parent.parent.parent / "config.json"
    text = payload if isinstance(payload, str) else json.dumps(payload)
    target.write_text(text, encoding="utf-8")


class ValidSpaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.space = Path(self.tmp.name) / "docs" / "business-analysis" / "erp"
        make_valid_space(self.space)

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_space_is_silent(self):
        findings = collect(self.space)
        self.assertEqual(findings, [], [f"{f.check}: {f.message}" for f in findings])

    def test_valid_space_without_review_history_passes_the_gate(self):
        findings = collect(self.space, gate=True)
        self.assertEqual([f for f in findings if f.severity == "error"], [])

    def test_schema_has_no_persistent_challenge_state(self):
        self.assertNotIn("challenge_record", SCHEMA["doc_types"])
        self.assertNotIn("challenge", SCHEMA)
        self.assertNotIn("locked", SCHEMA["frontmatter"]["optional"])
        self.assertNotRegex(SCHEMA["id_format"], r"(?:^|\|)CH(?:\||\))")

    def test_generated_views_have_no_challenge_history(self):
        registry = (self.space / "_generated" / "registry.md").read_text(
            encoding="utf-8"
        )
        status = (self.space / "_generated" / "status.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("Challenge Findings", registry)
        self.assertNotIn("Challenge coverage", status)

    def test_render_is_deterministic(self):
        first = {p.name: p.read_bytes()
                 for p in (self.space / "_generated").iterdir()}
        code, _, _ = run(["render", "--space", str(self.space)])
        self.assertEqual(code, 0)
        second = {p.name: p.read_bytes()
                  for p in (self.space / "_generated").iterdir()}
        self.assertEqual(first, second)

    def test_staleness_round_trip(self):
        edit(self.space / "domains" / "inventory" / "rules"
             / "stock-item-lifecycle-rules.md",
             "every change is a typed movement",
             "every change is a typed, audited movement")
        stale = [f for f in collect(self.space) if f.check == "generated_freshness"]
        self.assertTrue(stale)
        code, _, _ = run(["render", "--space", str(self.space)])
        self.assertEqual(code, 0)
        self.assertEqual(collect(self.space), [])

    def test_generated_inverse_relations_do_not_change_ba_semantics(self):
        target = (
            self.space / "domains" / "inventory" / "acceptance"
            / "goods-receipt-acceptance.md"
        )
        target.write_text(
            target.read_text(encoding="utf-8").rstrip()
            + "\n\n<!-- sec: nav -->\n"
            "- [[maps/business-analysis|Business Analysis]]\n",
            encoding="utf-8",
        )
        code, _, err = run(["render", "--space", str(self.space)])
        self.assertEqual(code, 0, err)
        registry = self.space / "_generated" / "registry.json"
        before = registry.read_bytes()
        relation = (
            "## Related knowledge <!-- sec: relations:generated:start -->\n\n"
            "- Verified by: "
            "[[backlog/epics/receiving/stories/receive-goods/story|ST-001]]\n\n"
            "<!-- sec: relations:generated:end -->"
        )
        text = target.read_text(encoding="utf-8")
        target.write_text(
            text.replace(
                "<!-- sec: nav -->", relation + "\n\n<!-- sec: nav -->"
            ),
            encoding="utf-8",
        )
        self.assertEqual(collect(self.space), [])
        code, _, err = run(["render", "--space", str(self.space)])
        self.assertEqual(code, 0, err)
        self.assertEqual(registry.read_bytes(), before)


class BuilderFixtureTests(unittest.TestCase):
    def test_registry_lockstep_with_compiler_checks(self):
        self.assertEqual(sorted(BA_BUILDERS), sorted(ba.CHECK_IDS))

    def test_each_builder_fires_its_check(self):
        for check, builder in sorted(BA_BUILDERS.items()):
            with self.subTest(check=check):
                with tempfile.TemporaryDirectory() as tmp:
                    space = Path(tmp) / "docs" / "business-analysis" / "erp"
                    make_valid_space(space)
                    builder(space)
                    findings = collect(space, gate=check in GATE_CHECKS)
                    matching = [f for f in findings if f.check == check]
                    self.assertTrue(
                        matching,
                        f"{check}: no finding fired; got "
                        f"{[(f.check, f.message) for f in findings]}")

    def test_glossary_technical_name_shape_fires(self):
        """The glossary mapping column is identifier-shaped when filled;
        the valid space's empty cell proves allow_empty stays silent."""
        with tempfile.TemporaryDirectory() as tmp:
            space = Path(tmp) / "docs" / "business-analysis" / "erp"
            make_valid_space(space)
            edit(space / "glossary.md", "| stock_movement |",
                 "| stokHareketi |")
            findings = collect(space)
            self.assertTrue(
                [f for f in findings if f.check == "identifier_shape"])


class ApproveTests(unittest.TestCase):
    """The approve verb stamps status and the UTC date in one script-owned
    write; a doc the checks reject is restored byte-identical."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.space = Path(self.tmp.name) / "docs" / "business-analysis" / "erp"
        make_valid_space(self.space)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def utc_today() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).date().isoformat()

    def test_approve_stamps_status_and_utc_date(self):
        target = self.space / INV / "entities" / "stock-item-entity.md"
        edit(target, "status: approved\napproved_at: 2026-07-12",
             "status: in_review")
        code, out, err = run(["approve", "--space", str(self.space),
                              "--doc", "domains/inventory/entities/stock-item-entity.md"])
        self.assertEqual(code, 0, out + err)
        text = target.read_text(encoding="utf-8")
        self.assertIn("status: approved", text)
        self.assertIn(f"approved_at: {self.utc_today()}", text)

    def test_approve_rejected_doc_restores_bytes(self):
        target = self.space / INV / "rules" / "stock-item-lifecycle-rules.md"
        edit(target, "status: approved\napproved_at: 2026-07-12",
             "status: in_review")
        edit(target, "| confirmed | 2026-07-09 |",
             f"| open | {self.utc_today()} |")
        before = target.read_bytes()
        code, out, err = run(["approve", "--space", str(self.space),
                              "--doc",
                              "domains/inventory/rules/stock-item-lifecycle-rules.md"])
        self.assertEqual(code, 1, out + err)
        self.assertEqual(target.read_bytes(), before)

    def test_approve_already_approved_fails(self):
        code, _, err = run(["approve", "--space", str(self.space),
                            "--doc", "space.md"])
        self.assertEqual(code, 1)
        self.assertIn("already approved", err)

    def test_approve_refuses_draft(self):
        """Approval follows review: a draft doc must pass through
        in_review before the verb accepts it."""
        target = self.space / INV / "entities" / "stock-item-entity.md"
        edit(target, "status: approved\napproved_at: 2026-07-12",
             "status: draft")
        code, _, err = run(["approve", "--space", str(self.space),
                            "--doc", "domains/inventory/entities/stock-item-entity.md"])
        self.assertEqual(code, 1)
        self.assertIn("in_review", err)

    def test_approve_unknown_doc_is_usage_error(self):
        code, _, err = run(["approve", "--space", str(self.space),
                            "--doc", "nope.md"])
        self.assertEqual(code, 2, err)

class SubcommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "docs" / "business-analysis"

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_is_born_compliant(self):
        space = self.root / "topic"
        seed_vault_scaffolding(space)
        code, _, err = run(["init", "--space", str(space),
                            "--title", "Topic", "--code", "TOP"])
        self.assertEqual(code, 0, err)
        code, _, _ = run(["render", "--space", str(space)])
        self.assertEqual(code, 0)
        self.assertEqual(collect(space), [])

    def test_stub_carries_schema_shape(self):
        space = self.root / "topic"
        run(["init", "--space", str(space), "--title", "Topic", "--code", "TOP"])
        code, out, err = run(["stub", "--space", str(space), "--type", "process",
                              "--slug", "intake", "--title", "Intake"])
        self.assertEqual(code, 0, err)
        self.assertIn("next free ids", out)
        findings = collect(space)
        structural = [f for f in findings if f.check in
                      ("frontmatter_schema", "required_sections", "status_legality")]
        self.assertEqual(structural, [])

    def test_challenge_record_stub_is_retired(self):
        space = self.root / "topic"
        run(["init", "--space", str(space), "--title", "Topic", "--code", "TOP"])
        code, _, err = run([
            "stub", "--space", str(space), "--type", "challenge_record",
            "--title", "Malformed review",
        ])
        self.assertEqual(code, 2)
        self.assertIn("unknown type 'challenge_record'", err)

    def test_stub_nav_targets_owning_hub(self):
        """Content stubs nav to their owning overview hub; overview stubs
        (hubs themselves) nav to the subtree map."""
        space = self.root / "topic"
        run(["init", "--space", str(space), "--title", "Topic", "--code", "TOP"])
        run(["stub", "--space", str(space), "--type", "process",
             "--slug", "intake", "--title", "Intake"])
        text = (space / "processes" / "intake-process.md").read_text(encoding="utf-8")
        self.assertIn("[[business-analysis/topic/space|", text)
        overview = (space / "space.md").read_text(encoding="utf-8")
        self.assertIn("[[maps/business-analysis|Business Analysis]]", overview)

    def test_decision_stub_mints_alias_id_note(self):
        """Decision stubs mint <slug>-decision.md with a natural (never
        id-led) title and H1, the record id in the frontmatter alias and
        the seeded ruling row, and a hub-first nav."""
        space = self.root / "erp"
        make_valid_space(space)
        code, _, err = run(["stub", "--space", str(space), "--type", "decision",
                            "--node", "domains/inventory",
                            "--slug", "batch-sizing", "--title", "Batch sizing"])
        self.assertEqual(code, 0, err)
        target = (space / "domains" / "inventory" / "decisions"
                  / "batch-sizing-decision.md")
        self.assertTrue(target.is_file())
        text = target.read_text(encoding="utf-8")
        self.assertIn("title: Batch sizing\n", text)
        self.assertIn("# Batch sizing\n", text)
        self.assertNotIn("DEC-INV-001:", text)
        self.assertIn("aliases:\n  - DEC-INV-001", text)
        self.assertIn("| DEC-INV-001 | To be decided. | active |", text)
        self.assertIn(
            "[[business-analysis/erp/domains/inventory/domain|",
            text)
        code, _, _ = run(["render", "--space", str(space)])
        self.assertEqual(code, 0)
        findings = [f for f in collect(space)
                    if f.path.startswith("domains/inventory/decisions/")]
        self.assertEqual(findings, [],
                         [f"{f.check}: {f.message}" for f in findings])

    def test_resolve_known_and_unknown(self):
        space = self.root / "erp"
        make_valid_space(space)
        code, out, _ = run(["resolve", "--space", str(space),
                            "--ids", "BR-INV-001"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["BR-INV-001"]["doc"],
                         "domains/inventory/rules/stock-item-lifecycle-rules.md")
        self.assertEqual(payload["BR-INV-001"]["doc_status"], "approved")
        self.assertEqual(len(payload["BR-INV-001"]["statement_sha256"]), 64)
        code, _, err = run(["resolve", "--space", str(space),
                            "--ids", "BR-INV-001,BR-INV-999"])
        self.assertEqual(code, 1)
        self.assertIn("BR-INV-999", err)

    def test_verify_import(self):
        space = self.root / "erp"
        make_valid_space(space)
        good = self.root / "good.json"
        good.write_text(json.dumps({"criteria": [
            {"criterion_id": "AC-INV-001", "story": "WP-01", "disposition": "covered"},
        ]}), encoding="utf-8")
        code, _, _ = run(["verify-import", "--space", str(space),
                          "--json-file", str(good)])
        self.assertEqual(code, 0)
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"criteria": [
            {"criterion_id": "AC-INV-777", "story": "WP-01", "disposition": "covered"},
        ]}), encoding="utf-8")
        code, _, err = run(["verify-import", "--space", str(space),
                            "--json-file", str(bad)])
        self.assertEqual(code, 1)
        self.assertIn("AC-INV-777", err)

    def test_verify_import_rejects_unapproved_owner(self):
        space = self.root / "erp"
        make_valid_space(space)
        edit(space / "domains" / "inventory" / "acceptance" / "goods-receipt-acceptance.md",
             "status: approved\napproved_at: 2026-07-12", "status: draft")
        payload = self.root / "import.json"
        payload.write_text(json.dumps({"criteria": [
            {"criterion_id": "AC-INV-001", "story": "WP-01", "disposition": "covered"},
        ]}), encoding="utf-8")
        code, _, err = run(["verify-import", "--space", str(space),
                            "--json-file", str(payload)])
        self.assertEqual(code, 1)
        self.assertIn("not approved", err)

    def test_render_refuses_duplicate_ids(self):
        space = self.root / "erp"
        make_valid_space(space)
        break_id_unique(space)
        code, _, err = run(["render", "--space", str(space)])
        self.assertEqual(code, 1)
        self.assertIn("duplicate ids", err)

    def test_render_removes_stray_generated_files(self):
        space = self.root / "erp"
        make_valid_space(space)
        stray = space / "_generated" / "leftover.md"
        stray.write_text("x", encoding="utf-8")
        code, _, _ = run(["render", "--space", str(space)])
        self.assertEqual(code, 0)
        self.assertFalse(stray.exists())

    def test_generated_views_carry_the_marker(self):
        space = self.root / "erp"
        make_valid_space(space)
        for name in ("index.md", "registry.md", "backlinks.md",
                     "status.md", "open-questions.md"):
            first = (space / "_generated" / name).read_text(
                encoding="utf-8").splitlines()[0]
            self.assertIn("generated by ba_compile render", first)

    def test_registry_json_shape(self):
        space = self.root / "erp"
        make_valid_space(space)
        payload = json.loads((space / "_generated" / "registry.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(payload["codes"]["INV"], "domains/inventory")
        entry = payload["ids"]["BR-INV-002"]
        self.assertEqual(entry["cited_by"], ["AC-INV-001"])
        self.assertEqual(entry["doc_status"], "approved")


class PackageLifecycleTests(unittest.TestCase):
    """Receipt lifecycle, compatibility classification and write safety."""

    entity = "domains/inventory/entities/stock-item-entity.md"
    process = "domains/inventory/processes/goods-receipt-process.md"
    acceptance = "domains/inventory/acceptance/goods-receipt-acceptance.md"
    decision = "domains/inventory/decisions/batch-sizing-decision.md"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.docs = Path(self.tmp.name) / "docs"
        self.space = self.docs / "business-analysis" / "erp"
        make_valid_space(self.space)

    def tearDown(self):
        self.tmp.cleanup()

    @property
    def overview(self) -> Path:
        return self.space / "space.md"

    def command(self, *args: str):
        return run([*args, "--space", str(self.space)])

    def approve_package(self):
        code, out, err = run(["approve-package", "--space", str(self.space),
                              "--vault-root", str(self.docs)])
        self.assertEqual(code, 0, out + err)
        return json.loads(out.strip().splitlines()[-1])

    def add_approved_decision(self) -> Path:
        code, out, err = run([
            "stub", "--space", str(self.space), "--type", "decision",
            "--node", "domains/inventory", "--slug", "batch-sizing",
            "--title", "Batch sizing",
        ])
        self.assertEqual(code, 0, out + err)
        target = self.space / self.decision
        text = target.read_text(encoding="utf-8")
        text = text.replace("status: draft", "status: in_review")
        text = text.replace("  - status/draft", "  - status/in-review")
        target.write_text(text, encoding="utf-8")
        code, out, err = run(["approve", "--space", str(self.space),
                              "--doc", self.decision])
        self.assertEqual(code, 0, out + err)
        approved = target.read_text(encoding="utf-8")
        self.assertIn("status: approved", approved)
        self.assertIn(f"approved_at: {ba.utc_today().isoformat()}", approved)
        self.assertIn("  - status/approved", approved)
        return target

    def prepare_v2_decision_legacy(self, status: str = "draft") -> Path:
        target = self.add_approved_decision()
        self.approve_package()
        text = ba.draft_document_text(target.read_text(encoding="utf-8"))
        self.assertIsNotNone(text)
        if status == "in_review":
            text = ba.restamp_frontmatter(text, {"status": "in_review"})
            text = text.replace("  - status/draft", "  - status/in-review")
        target.write_text(text, encoding="utf-8")
        code, out, err = run(["render", "--space", str(self.space)])
        self.assertEqual(code, 0, out + err)
        root = self.overview.read_text(encoding="utf-8")
        root = ba.restamp_frontmatter(root, {
            "package_contract_version": 2,
            "package_hash": ba.package_hash(self.space),
        })
        self.assertIsNotNone(root)
        self.overview.write_text(root, encoding="utf-8")
        return target

    def test_multiple_documents_join_one_open_revision(self):
        self.approve_package()
        for rel in (self.entity, self.process, self.acceptance):
            code, out, err = run(["begin-revision", "--space", str(self.space),
                                  "--doc", rel])
            self.assertEqual(code, 0, out + err)
        root = self.overview.read_text(encoding="utf-8")
        self.assertIn("package_status: draft", root)
        self.assertNotIn("package_hash:", root)
        self.assertNotIn("package_approved_at_utc:", root)
        for rel in (self.entity, self.process, self.acceptance):
            self.assertIn("status: draft", (self.space / rel).read_text(encoding="utf-8"))

    def test_markerless_draft_package_accepts_another_approved_document(self):
        self.approve_package()
        root = ba.restamp_frontmatter(
            self.overview.read_text(encoding="utf-8"), {"package_status": "draft"})
        self.overview.write_text(root, encoding="utf-8")
        code, out, err = run(["begin-revision", "--space", str(self.space),
                              "--doc", self.process])
        self.assertEqual(code, 0, out + err)
        root = self.overview.read_text(encoding="utf-8")
        self.assertNotIn("package_hash:", root)
        self.assertNotIn("package_approved_at_utc:", root)
        self.assertNotIn("revision_baseline", root)
        self.assertNotIn("revision_marker", root)

    def test_open_revision_rejects_an_already_open_target_without_writes(self):
        self.approve_package()
        code, out, err = run(["begin-revision", "--space", str(self.space),
                              "--doc", self.entity])
        self.assertEqual(code, 0, out + err)
        before = {path: path.read_bytes() for path in (self.overview, self.space / self.entity)}
        code, _, err = run(["begin-revision", "--space", str(self.space),
                            "--doc", self.entity])
        self.assertEqual(code, 1)
        self.assertIn("approved or superseded", err)
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_unrecognized_package_status_rejects_without_writes(self):
        self.approve_package()
        root = self.overview.read_text(encoding="utf-8").replace(
            "package_status: approved", "package_status: abandoned")
        self.overview.write_text(root, encoding="utf-8")
        before = {path: path.read_bytes() for path in (self.overview, self.space / self.entity)}
        code, _, err = run(["begin-revision", "--space", str(self.space),
                            "--doc", self.entity])
        self.assertEqual(code, 1)
        self.assertIn("approved, draft, or legacy-approved", err)
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_target_write_failure_restores_the_exact_root_receipt(self):
        self.approve_package()
        target = self.space / self.entity
        before_root, before_target = self.overview.read_bytes(), target.read_bytes()
        replace = ba.atomic_replace

        def fail_target(path, text):
            if path == target:
                raise OSError("simulated target failure")
            return replace(path, text)

        with mock.patch.object(ba, "atomic_replace", side_effect=fail_target):
            code, _, err = run(["begin-revision", "--space", str(self.space),
                                "--doc", self.entity])
        self.assertEqual(code, 1)
        self.assertIn("original package receipt restored", err)
        self.assertEqual(self.overview.read_bytes(), before_root)
        self.assertEqual(target.read_bytes(), before_target)

    def test_failed_root_restore_leaves_the_package_draft(self):
        self.approve_package()
        target = self.space / self.entity
        replace = ba.atomic_replace
        root_writes = 0

        def fail_restore(path, text):
            nonlocal root_writes
            if path == target:
                raise OSError("simulated target failure")
            if path == self.overview:
                root_writes += 1
                if root_writes == 2:
                    raise OSError("simulated restore failure")
            return replace(path, text)

        with mock.patch.object(ba, "atomic_replace", side_effect=fail_restore):
            code, _, err = run(["begin-revision", "--space", str(self.space),
                                "--doc", self.entity])
        self.assertEqual(code, 1)
        self.assertIn("root remains draft", err)
        root = self.overview.read_text(encoding="utf-8")
        self.assertIn("package_status: draft", root)
        self.assertNotIn("package_hash:", root)

    def test_space_document_revision_is_one_atomic_replacement(self):
        self.approve_package()
        replace = ba.atomic_replace
        writes = []

        def record(path, text):
            writes.append(path)
            return replace(path, text)

        with mock.patch.object(ba, "atomic_replace", side_effect=record):
            code, out, err = run(["begin-revision", "--space", str(self.space),
                                  "--doc", "space.md"])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(writes, [self.overview])
        root = self.overview.read_text(encoding="utf-8")
        self.assertIn("status: draft", root)
        self.assertIn("package_status: draft", root)
        self.assertNotIn("approved_at:", root)
        self.assertNotIn("package_hash:", root)

    def test_v2_decision_only_gate_failure_is_legacy_readonly_everywhere(self):
        target = self.prepare_v2_decision_legacy("in_review")
        classification = ba.classify_package(self.space, SCHEMA, self.docs)
        self.assertEqual(classification["profile"], "legacy-readonly")
        self.assertEqual(classification["legacy_reason"], "v2-decision-gate")
        code, out, err = run(["status", "--space", str(self.space)])
        self.assertEqual(code, 0, out + err)
        status = json.loads(out)
        self.assertTrue(status["current"])
        self.assertTrue(status["legacy"])
        self.assertEqual(status["verification_profile"], "legacy-readonly")
        candidate = stage_package.ba_candidates(self.docs)[0]
        self.assertEqual(candidate["verification_profile"], "legacy-readonly")
        self.assertTrue(candidate["current"])
        before = target.read_bytes()
        code, out, err = run(["begin-revision", "--space", str(self.space),
                              "--doc", self.decision])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(target.read_bytes(), before)
        self.assertIn("package_status: draft", self.overview.read_text(encoding="utf-8"))

    def test_v2_and_v3_compatibility_profiles_are_distinct(self):
        self.approve_package()
        root = ba.restamp_frontmatter(
            self.overview.read_text(encoding="utf-8"), {"package_contract_version": 2})
        self.overview.write_text(root, encoding="utf-8")
        self.assertEqual(ba.classify_package(self.space, SCHEMA, self.docs)["profile"],
                         "strict-current")

    def test_stale_or_nondecision_v2_gate_failure_is_invalid(self):
        target = self.prepare_v2_decision_legacy()
        target.write_text(target.read_text(encoding="utf-8") + "\nStale edit.\n",
                          encoding="utf-8")
        self.assertEqual(ba.classify_package(self.space, SCHEMA, self.docs)["profile"],
                         "invalid")

        self.tmp.cleanup()
        self.tmp = tempfile.TemporaryDirectory()
        self.docs = Path(self.tmp.name) / "docs"
        self.space = self.docs / "business-analysis" / "erp"
        make_valid_space(self.space)
        self.add_approved_decision()
        self.approve_package()
        process = self.space / self.process
        process.write_text(ba.draft_document_text(process.read_text(encoding="utf-8")),
                           encoding="utf-8")
        code, out, err = run(["render", "--space", str(self.space)])
        self.assertEqual(code, 0, out + err)
        root = ba.restamp_frontmatter(self.overview.read_text(encoding="utf-8"), {
            "package_contract_version": 2,
            "package_hash": ba.package_hash(self.space),
        })
        self.overview.write_text(root, encoding="utf-8")
        self.assertEqual(ba.classify_package(self.space, SCHEMA, self.docs)["profile"],
                         "invalid")

    def test_v3_package_with_open_decision_is_invalid_then_migrates_to_strict(self):
        target = self.prepare_v2_decision_legacy()
        root = ba.restamp_frontmatter(
            self.overview.read_text(encoding="utf-8"), {
                "package_contract_version": 3,
                "package_hash": ba.package_hash(self.space),
            })
        self.overview.write_text(root, encoding="utf-8")
        self.assertEqual(ba.classify_package(self.space, SCHEMA, self.docs)["profile"],
                         "invalid")

        root = ba.restamp_frontmatter(
            self.overview.read_text(encoding="utf-8"), {"package_contract_version": 2})
        self.overview.write_text(root, encoding="utf-8")
        code, out, err = run(["begin-revision", "--space", str(self.space),
                              "--doc", self.decision])
        self.assertEqual(code, 0, out + err)
        text = target.read_text(encoding="utf-8").replace("status: draft", "status: in_review")
        text = text.replace("  - status/draft", "  - status/in-review")
        target.write_text(text, encoding="utf-8")
        code, out, err = run(["approve", "--space", str(self.space),
                              "--doc", self.decision])
        self.assertEqual(code, 0, out + err)
        receipt = self.approve_package()
        self.assertEqual(receipt["verification_profile"], "strict-current")
        root = self.overview.read_text(encoding="utf-8")
        self.assertIn("package_contract_version: 3", root)
        self.assertEqual(ba.classify_package(self.space, SCHEMA, self.docs)["profile"],
                         "strict-current")

    def test_decision_in_review_is_vault_legal_but_blocks_ba_package_closure(self):
        target = self.add_approved_decision()
        text = ba.draft_document_text(target.read_text(encoding="utf-8"))
        text = ba.restamp_frontmatter(text, {"status": "in_review"})
        text = text.replace("  - status/draft", "  - status/in-review")
        target.write_text(text, encoding="utf-8")
        relative = f"business-analysis/erp/{self.decision}"
        checked = subprocess.run(
            [sys.executable, str(VAULT_CHECK), "check", "--vault", str(self.docs),
             "--impact", relative], capture_output=True, text=True, check=False,
        )
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        code, out, err = run(["approve-package", "--space", str(self.space),
                              "--vault-root", str(self.docs)])
        self.assertEqual(code, 1)
        self.assertIn("gate-blocking doc is in_review", out + err)

    def test_superseded_decision_passes_the_package_gate(self):
        target = self.add_approved_decision()
        text = ba.draft_document_text(target.read_text(encoding="utf-8"))
        text = ba.restamp_frontmatter(text, {
            "status": "superseded",
            "superseded_by": "next-decision",
        })
        text = text.replace("  - status/draft", "  - status/superseded")
        target.write_text(text, encoding="utf-8")
        receipt = self.approve_package()
        self.assertEqual(receipt["verification_profile"], "strict-current")

    def test_issue_56_lifecycle_runs_three_times_in_every_distribution(self):
        compilers = {
            "canonical": COMPILER,
            "claude": REPO / "dist/claude/software-engineering-team/scripts/ba_compile.py",
            "codex": REPO / "dist/codex/software-engineering-team/scripts/ba_compile.py",
            "opencode": REPO / "dist/opencode/software-engineering-team/scripts/ba_compile.py",
        }
        for name, path in compilers.items():
            self.assertTrue(path.is_file(), name)
            compiler = load_compiler(path, f"issue_56_{name}")
            for attempt in range(3):
                with tempfile.TemporaryDirectory() as raw:
                    docs = Path(raw) / "docs"
                    space = docs / "business-analysis" / "erp"
                    make_valid_space(space)
                    for argv in (
                        ["approve-package", "--space", str(space),
                         "--vault-root", str(docs)],
                        ["begin-revision", "--space", str(space),
                         "--doc", self.entity],
                        ["begin-revision", "--space", str(space),
                         "--doc", self.process],
                    ):
                        code, out, err = run_compiler(compiler, argv)
                        self.assertEqual(code, 0,
                                         f"{name} attempt {attempt + 1}: {out}{err}")
                    root = (space / "space.md").read_text(encoding="utf-8")
                    self.assertIn("package_status: draft", root)
                    self.assertNotIn("package_hash:", root)




class NumericLimitRemovalTests(unittest.TestCase):
    """BA remains structural and semantic when authored content grows."""

    def test_long_summary_and_deep_nesting_have_no_numeric_finding(self):
        with tempfile.TemporaryDirectory() as temporary:
            space = Path(temporary) / "docs" / "business-analysis" / "erp"
            make_valid_space(space)
            summary = space / "space.md"
            summary.write_text(summary.read_text(encoding="utf-8") + "\n" + "\n".join("Additional scope context." for _ in range(400)), encoding="utf-8")
            findings = collect(space)
            numeric = {"summary_caps", "thresholds", "aging"}
            self.assertFalse([item for item in findings if item.check in numeric])


if __name__ == "__main__":
    unittest.main()
