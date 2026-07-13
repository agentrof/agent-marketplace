"""Tests for the analysis-space compiler (plugins/software-team/scripts/
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
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COMPILER = REPO / "plugins" / "software-team" / "scripts" / "ba_compile.py"

spec = importlib.util.spec_from_file_location("ba_compile", COMPILER)
ba = importlib.util.module_from_spec(spec)
sys.modules["ba_compile"] = ba  # dataclass resolution needs the registry entry
spec.loader.exec_module(ba)

SCHEMA = ba.load_schema(ba.DEFAULT_SCHEMA)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = ba.main(argv)
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


def make_valid_space(space: Path) -> None:
    """A gate-passing ERP space with one inventory domain: approved docs,
    cited rules, a converged locked challenge round."""
    write(space / "space.md", """---
type: space
title: ERP Analysis
status: approved
approved_at: 2026-07-12
owner_role: business_analyst
code: ERP
---

# ERP Analysis

Analysis space for the ERP program, starting with inventory.

## Purpose Scope <!-- sec: purpose_scope -->

Track sellable stock per warehouse with auditable movements.

## Domain Map <!-- sec: domain_map -->

- [Inventory](domains/inventory/domain.md)

## Out Of Scope <!-- sec: out_of_scope -->

Payroll and manufacturing.
""")
    write(space / "glossary.md", """---
type: glossary
title: Glossary
status: approved
approved_at: 2026-07-12
owner_role: business_analyst
---

# Glossary

One vocabulary for the whole space.

## Terms <!-- sec: terms -->

| term | definition |
|---|---|
| movement | one typed quantity change against a stock item |
""")
    write(space / "actors.md", """---
type: actor_roster
title: Actors
status: approved
approved_at: 2026-07-12
owner_role: business_analyst
---

# Actors

Roles that appear across the space.

## Actors <!-- sec: actors -->

| actor | role | permissions |
|---|---|---|
| warehouse operator | records receipts | create movements |
""")
    write(space / "budgets.md", """---
type: budget_set
title: Non-Functional Budgets
status: approved
approved_at: 2026-07-12
owner_role: business_analyst
scope: space
---

# Non-Functional Budgets

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
---

# Inventory

Stock items, warehouses and their movements.

## Mission <!-- sec: mission -->

Every quantity change is an auditable movement.

## Boundaries <!-- sec: boundaries -->

Owns stock levels; pricing belongs to sales.

## Process Map <!-- sec: process_map -->

- [Goods Receipt](processes/goods-receipt.md)

## Data Notes <!-- sec: data_notes -->

| entity | note |
|---|---|
| [Stock Item](entities/stock-item.md) | promoted: has lifecycle |
""")
    write(space / "domains" / "inventory" / "processes" / "goods-receipt.md", """---
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
[Stock Item](../entities/stock-item.md) per
[BR-INV-001](../rules/stock-item-lifecycle.md).

## Exception Flows <!-- sec: exception_flows -->

Damaged goods are refused at the line level.
""")
    write(space / "domains" / "inventory" / "entities" / "stock-item.md", """---
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
| sku | unique identifier | product manager | after first movement | [BR-INV-001](../rules/stock-item-lifecycle.md) |
| on_hand_quantity | derived sum of movements | system | always | [BR-INV-002](../rules/stock-item-lifecycle.md) |

## Lifecycle <!-- sec: lifecycle -->

```mermaid
stateDiagram-v2
    [*] --> draft
    draft --> active: activate
```

## Propagation <!-- sec: propagation -->

| change | propagates_to | must_not_reach | frozen_copy |
|---|---|---|---|
| name edit | future documents | issued receipts | line keeps issue-time name |
""")
    write(space / "domains" / "inventory" / "rules" / "stock-item-lifecycle.md", """---
type: rule_set
title: Stock Item Lifecycle Rules
status: approved
approved_at: 2026-07-12
owner_role: business_analyst
governs:
  - ../entities/stock-item.md
---

# Stock Item Lifecycle Rules

Lifecycle constraints for [Stock Item](../entities/stock-item.md).

## Rules <!-- sec: rules -->

| id | statement | kind | status | cites |
|---|---|---|---|---|
| BR-INV-001 | The sku cannot change after the first stock movement; the refusal names the movement date. | constraint | active | |
| BR-INV-002 | On-hand quantity is never edited directly; every change is a typed movement. | constraint | active | |

## Assumptions <!-- sec: assumptions -->

| id | statement | source | affects | status | opened_on |
|---|---|---|---|---|---|
| AS-INV-001 | Negative on-hand is never permitted. | owner confirmed in round two | BR-INV-002 | confirmed | 2026-07-09 |
""")
    write(space / "domains" / "inventory" / "acceptance" / "goods-receipt.md", """---
type: acceptance_set
title: Goods Receipt Criteria
status: approved
approved_at: 2026-07-12
owner_role: business_analyst
verifies:
  - ../processes/goods-receipt.md
---

# Goods Receipt Criteria

Criteria for [Goods Receipt](../processes/goods-receipt.md).

## Criteria <!-- sec: criteria -->

| id | criterion | cites | verify | status |
|---|---|---|---|---|
| AC-INV-001 | Given an accepted line, when the receipt posts, then a movement row exists and on-hand rises by the line quantity. | BR-INV-002 | assert movement row and quantity delta via the stock query | active |
| AC-INV-002 | Given an item with a movement, when its sku is edited, then the edit is refused naming the movement date. | BR-INV-001 | attempt the edit; assert refusal message content | active |
""")
    write(space / "domains" / "inventory" / "reviews" / "round-1.md", """---
type: challenge_record
title: Inventory Challenge Round 1
status: approved
approved_at: 2026-07-12
owner_role: business_analyst
round: 1
review_scope: domain
verdict: converged
locked: true
---

# Inventory Challenge Round 1

Round 1 panel found no blocking gaps; one minor finding triaged.

## Panel <!-- sec: panel -->

| member | kind | why |
|---|---|---|
| negative-scenarios | lens | receipt edge cases |
| warehouse operations expert | expert | floor operations depth |

## Findings <!-- sec: findings -->

| id | lens | severity | finding | disposition | targets |
|---|---|---|---|---|---|
| CH-INV-001 | warehouse operations expert | minor | Damaged-goods refusal already covered. | covered | BR-INV-002 |

## Triage Audit <!-- sec: triage_audit -->

Independent audit reviewed the covered disposition; no disagreement.

## Verdict <!-- sec: verdict -->

Converged: zero blocking findings this round.
""")
    write(space / "reviews" / "space-round-1.md", """---
type: challenge_record
title: Space Challenge Round 1
status: approved
approved_at: 2026-07-12
owner_role: business_analyst
round: 1
review_scope: space
verdict: converged
locked: true
---

# Space Challenge Round 1

Cross-domain round over the registry and the inventory overview.

## Panel <!-- sec: panel -->

| member | kind | why |
|---|---|---|
| cross-domain consistency | lens | single domain so far; ran against the registry |

## Findings <!-- sec: findings -->

| id | lens | severity | finding | disposition | targets |
|---|---|---|---|---|---|
| CH-ERP-001 | cross-domain consistency | minor | Movement wording consistent with glossary. | covered | BR-INV-002 |

## Triage Audit <!-- sec: triage_audit -->

Independent audit reviewed the covered disposition; no disagreement.

## Verdict <!-- sec: verdict -->

Converged: zero blocking findings this round.
""")
    (space / "_generated").mkdir(exist_ok=True)
    code, _, err = run(["render", "--space", str(space)])
    assert code == 0, f"valid space must render: {err}"


INV = Path("domains") / "inventory"


def break_space_layout(space: Path) -> None:
    write(space / "stray-notes.md", "---\ntype: process\ntitle: X\nstatus: draft\n"
          "owner_role: business_analyst\n---\n\n# X\n")


def break_frontmatter_schema(space: Path) -> None:
    edit(space / INV / "processes" / "goods-receipt.md",
         "owner_role: business_analyst\n", "")


def break_status_legality(space: Path) -> None:
    edit(space / INV / "entities" / "stock-item.md",
         "status: approved", "status: shipped")


def break_required_sections(space: Path) -> None:
    edit(space / INV / "processes" / "goods-receipt.md",
         "## Trigger <!-- sec: trigger -->", "## Trigger")


def break_summary_caps(space: Path) -> None:
    filler = "\n".join(f"Summary filler line {i}." for i in range(12))
    edit(space / INV / "entities" / "stock-item.md",
         "One sellable, storable product variant tracked per warehouse.",
         filler)


def break_content_bans(space: Path) -> None:
    edit(space / INV / "processes" / "goods-receipt.md",
         "Damaged goods are refused", "Damaged goods — refused")


def break_dead_links(space: Path) -> None:
    edit(space / INV / "processes" / "goods-receipt.md",
         "(../entities/stock-item.md)", "(../entities/missing.md)")


def break_id_format(space: Path) -> None:
    edit(space / INV / "rules" / "stock-item-lifecycle.md",
         "| BR-INV-002 |", "| BR-2 |")


def break_id_unique(space: Path) -> None:
    edit(space / INV / "rules" / "stock-item-lifecycle.md",
         "| BR-INV-002 | On-hand quantity",
         "| BR-INV-001 | Duplicate. | constraint | active | |\n"
         "| BR-INV-002 | On-hand quantity")


def break_id_minting(space: Path) -> None:
    edit(space / INV / "rules" / "stock-item-lifecycle.md",
         "| BR-INV-002 | On-hand quantity",
         "| BR-FIN-009 | Wrong code. | constraint | active | |\n"
         "| BR-INV-002 | On-hand quantity")


def break_id_links(space: Path) -> None:
    edit(space / INV / "processes" / "goods-receipt.md",
         "Damaged goods are refused", "Per BR-INV-001, damaged goods are refused")


def break_row_schema(space: Path) -> None:
    edit(space / INV / "acceptance" / "goods-receipt.md",
         "| attempt the edit; assert refusal message content |", "| |")


def break_semantic_links(space: Path) -> None:
    edit(space / "space.md", "- [Inventory](domains/inventory/domain.md)",
         "The domain list is maintained elsewhere.")


def break_approval_preconditions(space: Path) -> None:
    edit(space / INV / "rules" / "stock-item-lifecycle.md",
         "| confirmed | 2026-07-09 |", "| open | 2026-07-09 |")


def break_challenge_record(space: Path) -> None:
    edit(space / INV / "reviews" / "round-1.md",
         "| minor | Damaged-goods refusal already covered. |",
         "| blocking | Damaged-goods refusal already covered. |")


def break_br_uncited(space: Path) -> None:
    edit(space / INV / "rules" / "stock-item-lifecycle.md",
         "| BR-INV-002 | On-hand quantity",
         "| BR-INV-003 | An uncited rule. | constraint | active | |\n"
         "| BR-INV-002 | On-hand quantity")


def break_thresholds(space: Path) -> None:
    filler = "\n".join(f"Exception narration line {i}." for i in range(160))
    edit(space / INV / "processes" / "goods-receipt.md",
         "Damaged goods are refused at the line level.", filler)


def break_aging(space: Path) -> None:
    edit(space / INV / "rules" / "stock-item-lifecycle.md",
         "| confirmed | 2026-07-09 |", "| open | 2020-01-01 |")


def break_gate_approval(space: Path) -> None:
    edit(space / INV / "entities" / "stock-item.md",
         "status: approved\napproved_at: 2026-07-12", "status: draft")


def break_generated_freshness(space: Path) -> None:
    edit(space / INV / "rules" / "stock-item-lifecycle.md",
         "never edited directly", "never edited directly by hand")


BA_BUILDERS = {
    "space_layout": break_space_layout,
    "frontmatter_schema": break_frontmatter_schema,
    "status_legality": break_status_legality,
    "required_sections": break_required_sections,
    "summary_caps": break_summary_caps,
    "content_bans": break_content_bans,
    "dead_links": break_dead_links,
    "id_format": break_id_format,
    "id_unique": break_id_unique,
    "id_minting": break_id_minting,
    "id_links": break_id_links,
    "row_schema": break_row_schema,
    "semantic_links": break_semantic_links,
    "approval_preconditions": break_approval_preconditions,
    "challenge_record": break_challenge_record,
    "br_uncited": break_br_uncited,
    "thresholds": break_thresholds,
    "aging": break_aging,
    "gate_approval": break_gate_approval,
    "generated_freshness": break_generated_freshness,
}

# Builders that mutate authored docs make the pre-built generated views
# stale as a side effect; freshness noise is expected for them. Builders
# in this set are judged on their own check id only.
GATE_CHECKS = {"gate_approval"}


def collect(space: Path, gate: bool = False) -> list:
    schema = SCHEMA
    scanned, base = ba.scan_space(space, schema)
    findings = ba.run_checks(scanned, base, gate=gate, gate_node="")
    if not scanned.broken:
        warnings = [f for f in findings if f.severity == "warning"]
        findings += ba.freshness_findings(scanned, warnings)
    return findings


class ValidSpaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.space = Path(self.tmp.name) / "erp"
        make_valid_space(self.space)

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_space_is_silent(self):
        findings = collect(self.space)
        self.assertEqual(findings, [], [f"{f.check}: {f.message}" for f in findings])

    def test_valid_space_passes_the_gate(self):
        findings = collect(self.space, gate=True)
        self.assertEqual([f for f in findings if f.severity == "error"], [])

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
             / "stock-item-lifecycle.md",
             "every change is a typed movement",
             "every change is a typed, audited movement")
        stale = [f for f in collect(self.space) if f.check == "generated_freshness"]
        self.assertTrue(stale)
        code, _, _ = run(["render", "--space", str(self.space)])
        self.assertEqual(code, 0)
        self.assertEqual(collect(self.space), [])


class BuilderFixtureTests(unittest.TestCase):
    def test_registry_lockstep_with_compiler_checks(self):
        self.assertEqual(sorted(BA_BUILDERS), sorted(ba.CHECK_IDS))

    def test_each_builder_fires_its_check(self):
        for check, builder in sorted(BA_BUILDERS.items()):
            with self.subTest(check=check):
                with tempfile.TemporaryDirectory() as tmp:
                    space = Path(tmp) / "erp"
                    make_valid_space(space)
                    builder(space)
                    findings = collect(space, gate=check in GATE_CHECKS)
                    matching = [f for f in findings if f.check == check]
                    self.assertTrue(
                        matching,
                        f"{check}: no finding fired; got "
                        f"{[(f.check, f.message) for f in findings]}")


class SubcommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_is_born_compliant(self):
        space = self.root / "topic"
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

    def test_resolve_known_and_unknown(self):
        space = self.root / "erp"
        make_valid_space(space)
        code, out, _ = run(["resolve", "--space", str(space),
                            "--ids", "BR-INV-001"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["BR-INV-001"]["doc"],
                         "domains/inventory/rules/stock-item-lifecycle.md")
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
        edit(space / "domains" / "inventory" / "acceptance" / "goods-receipt.md",
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


if __name__ == "__main__":
    unittest.main()
