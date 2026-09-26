"""Project-level Obsidian title, taxonomy, and graph palette contracts."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.tests.git_fixture import init_repository


ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "software-engineering-team"
SETUP = PLUGIN / "scripts" / "setup_project.py"
VAULT_CHECK = PLUGIN / "scripts" / "vault_check.py"
POLICY = (
    PLUGIN
    / "skill-content"
    / "obsidian-vault"
    / "data"
    / "vault-policy.json"
)

BACKLOG_COLORS = {
    "backlog": 11032055,
    "backlog-review": 16007006,
    "epic": 3900150,
    "epic-review": 16096779,
    "story": 1357990,
    "test-plan": 2278750,
}


class ProjectVaultContractTests(unittest.TestCase):
    def setup_project(self, root: Path) -> Path:
        init_repository(root)
        result = subprocess.run(
            [sys.executable, str(SETUP), "--project-root", str(root)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return root / "workspace"

    def check_vault(self, workspace: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(VAULT_CHECK),
                "check",
                "--vault",
                str(workspace / "docs"),
                "--json",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def run_vault(
        self, *args: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VAULT_CHECK), *args],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )

    @staticmethod
    def decision_note(title: str, ident: str, relation: str = "") -> str:
        relation_row = f"related_to:\n  - \"{relation}\"\n" if relation else ""
        return (
            "---\ntype: decision\n"
            f"title: {title}\nstatus: proposed\n{relation_row}"
            "tags:\n  - doc/decision\n  - status/proposed\n"
            f"aliases:\n  - {ident}\n---\n\n# {title}\n"
        )

    def test_setup_writes_small_config_and_fixed_colors(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            config = json.loads(
                (workspace / "config.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                config,
                {
                    "schema_version": 2,
                    "team_id": "software-engineering-team",
                    "output_language": "English",
                    "terminology_language": "English",
                },
            )

            policy = json.loads(POLICY.read_text(encoding="utf-8"))
            policy_groups = {
                group["id"]: (group["query"], group["rgb"])
                for group in policy["graph_color_groups"]
                if group["id"] in BACKLOG_COLORS
            }
            self.assertEqual(
                policy_groups,
                {
                    doc_type: (f"tag:#doc/{doc_type}", color)
                    for doc_type, color in BACKLOG_COLORS.items()
                },
            )

            graph = json.loads(
                (workspace / "docs/.obsidian/graph.json").read_text(
                    encoding="utf-8"
                )
            )
            rendered = {
                group["query"]: group["color"]["rgb"]
                for group in graph["colorGroups"]
            }
            for doc_type, color in BACKLOG_COLORS.items():
                self.assertEqual(rendered[f"tag:#doc/{doc_type}"], color)

    def test_vault_check_rejects_a_backlog_type_outside_its_nested_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            orphan = workspace / "docs/backlog/orphan-story.md"
            orphan.write_text(
                "---\n"
                "type: story\n"
                "title: Orphan story\n"
                "status: planned\n"
                "owner_role: backend-developer\n"
                "tags:\n"
                "  - doc/story\n"
                "  - status/planned\n"
                "aliases:\n"
                "  - ST-999\n"
                "---\n\n"
                "# Orphan story\n",
                encoding="utf-8",
            )
            result = self.check_vault(workspace)
            self.assertEqual(result.returncode, 1)
            self.assertIn("type 'story' is not legal at this path", result.stdout)

    def test_vault_check_rejects_project_property_type_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            path = workspace / "docs/.obsidian/types.json"
            types = json.loads(path.read_text(encoding="utf-8"))
            types["types"]["owner_role"] = "number"
            path.write_text(json.dumps(types, indent=2) + "\n", encoding="utf-8")
            result = self.check_vault(workspace)
            self.assertEqual(result.returncode, 1)
            self.assertIn("property 'owner_role' must be typed 'text'", result.stdout)

    def test_business_analysis_fragment_reconciles_all_declared_property_types(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            result = self.run_vault(
                "reconcile-payload-fragment",
                "--vault", str(workspace / "docs"),
                "--fragment", "business-analysis",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            types = json.loads(
                (workspace / "docs/.obsidian/types.json").read_text(
                    encoding="utf-8"
                )
            )["types"]
            self.assertEqual(types["package_status"], "text")
            self.assertEqual(types["package_approved_at_utc"], "datetime")

    def test_vault_check_rejects_project_backlog_color_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            path = workspace / "docs/.obsidian/graph.json"
            graph = json.loads(path.read_text(encoding="utf-8"))
            story = next(
                group
                for group in graph["colorGroups"]
                if group["query"] == "tag:#doc/story"
            )
            story["color"]["rgb"] += 1
            path.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
            result = self.check_vault(workspace)
            self.assertEqual(result.returncode, 1)
            self.assertIn(
                "graph.json colorGroups do not match policy",
                result.stdout,
            )

    def test_policy_closes_the_nested_backlog_markdown_paths(self):
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        self.assertEqual(
            {
                key: policy["type_path_patterns"][key]
                for key in (
                    "backlog",
                    "backlog_review",
                    "epic",
                    "epic_review",
                    "story",
                    "test_plan",
                )
            },
            {
                "backlog": [r"^backlog/backlog\.md$"],
                "backlog_review": [
                    r"^backlog/reviews/round-[0-9]+-backlog-review\.md$"
                ],
                "epic": [
                    r"^backlog/epics/[a-z0-9]+(?:-[a-z0-9]+)*/epic\.md$"
                ],
                "epic_review": [
                    r"^backlog/epics/[a-z0-9]+(?:-[a-z0-9]+)*/reviews/round-[0-9]+-epic-review\.md$"
                ],
                "story": [
                    r"^backlog/epics/[a-z0-9]+(?:-[a-z0-9]+)*/stories/[a-z0-9]+(?:-[a-z0-9]+)*/story\.md$"
                ],
                "test_plan": [
                    r"^backlog/epics/[a-z0-9]+(?:-[a-z0-9]+)*/stories/[a-z0-9]+(?:-[a-z0-9]+)*/test-plan\.md$"
                ],
            },
        )

    def test_normalize_dry_run_and_second_apply_are_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            note = workspace / "docs/solution-design/decisions/problem-decision.md"
            note.parent.mkdir(parents=True)
            note.write_text(
                self.decision_note("Problem", "DEC-001"), encoding="utf-8"
            )
            before = note.read_bytes()
            dry = self.run_vault(
                "normalize", "--vault", str(workspace / "docs"),
                "--dry-run", "--json",
            )
            self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
            self.assertEqual(note.read_bytes(), before)
            first = self.run_vault(
                "normalize", "--vault", str(workspace / "docs"), "--json"
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            normalized = note.read_bytes()
            self.assertIn(b"title: Problem", normalized)
            second = self.run_vault(
                "normalize", "--vault", str(workspace / "docs"), "--json"
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(note.read_bytes(), normalized)

    def test_decision_revision_lineage_is_not_read_as_a_supersede_chain(self):
        for lineage, ok in (("ARC:ROOT:ADR-003@r1", True),
                            ("ARC:orders-api:HUB-orders-api@r12", True),
                            ("solution-design/decisions/other-decision", False),
                            ("ARC:ROOT:ADR-003@r0", False)):
            with self.subTest(supersedes=lineage), tempfile.TemporaryDirectory() as temporary:
                workspace = self.setup_project(Path(temporary))
                decisions = workspace / "docs/solution-design/decisions"
                decisions.mkdir(parents=True)
                note = self.decision_note("Dispatch decision", "SD-001")
                (decisions / "dispatch-decision.md").write_text(
                    note.replace("---\n\n#", f"supersedes: {lineage}\n---\n\n#"),
                    encoding="utf-8",
                )
                result = self.check_vault(workspace)
                if ok:
                    self.assertNotIn("quoted wikilink", result.stdout)
                else:
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("quoted wikilink", result.stdout)

    def test_title_shape_rejects_generic_and_duplicate_graph_labels(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            decisions = workspace / "docs/solution-design/decisions"
            decisions.mkdir(parents=True)
            (decisions / "overview-decision.md").write_text(
                self.decision_note("Overview", "DEC-001"), encoding="utf-8"
            )
            (decisions / "overview-copy-decision.md").write_text(
                self.decision_note("overview", "DEC-002"), encoding="utf-8"
            )
            result = self.check_vault(workspace)
            self.assertEqual(result.returncode, 1)
            self.assertIn("generic title 'Overview'", result.stdout)
            self.assertIn("also used by", result.stdout)

    def test_relation_render_is_deterministic_and_materializes_inverse(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            decisions = workspace / "docs/solution-design/decisions"
            decisions.mkdir(parents=True)
            target = decisions / "target-decision.md"
            source = decisions / "source-decision.md"
            target.write_text(
                self.decision_note("Target decision", "DEC-001"),
                encoding="utf-8",
            )
            source.write_text(
                self.decision_note(
                    "Source decision", "DEC-002",
                    "[[solution-design/decisions/target-decision|Target decision]]",
                ),
                encoding="utf-8",
            )
            first = self.run_vault(
                "render-relations", "--vault", str(workspace / "docs")
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            rendered = target.read_bytes()
            self.assertIn(b"Related from", rendered)
            self.assertIn(
                b"[[solution-design/decisions/source-decision|Source decision]]",
                rendered,
            )
            second = self.run_vault(
                "render-relations", "--vault", str(workspace / "docs")
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(target.read_bytes(), rendered)

    def test_relation_catalogs_reserve_the_target_link_budget(self):
        for incoming_relations in (100, 101, 198, 199, 200):
            with self.subTest(incoming_relations=incoming_relations), \
                    tempfile.TemporaryDirectory() as temporary:
                workspace = self.setup_project(Path(temporary))
                decisions = workspace / "docs/solution-design/decisions"
                decisions.mkdir(parents=True)
                target = decisions / "target-decision.md"
                target.write_text(
                    self.decision_note("Target decision", "DEC-000"),
                    encoding="utf-8",
                )
                for index in range(1, incoming_relations + 1):
                    (decisions / f"source-{index:03d}.md").write_text(
                        self.decision_note(
                            f"Source decision {index:03d}",
                            f"DEC-{index:03d}",
                            "[[solution-design/decisions/target-decision|"
                            "Target decision]]",
                        ),
                        encoding="utf-8",
                    )

                rendered = self.run_vault(
                    "render-relations", "--vault", str(workspace / "docs")
                )
                self.assertEqual(
                    rendered.returncode, 0, rendered.stdout + rendered.stderr
                )
                catalogs = sorted(
                    (workspace / "docs/maps/_relations").rglob(
                        "relations-*.md"
                    )
                )
                self.assertEqual(
                    len(catalogs), (
                        0 if incoming_relations <= 100
                        else (incoming_relations + 98) // 99
                    )
                )
                catalog_text = [
                    path.read_text(encoding="utf-8") for path in catalogs
                ]
                self.assertTrue(
                    all(text.count("[[") <= 100 for text in catalog_text)
                )
                projection_text = catalog_text or [
                    target.read_text(encoding="utf-8")
                ]
                for index in range(1, incoming_relations + 1):
                    needle = (
                        "[[solution-design/decisions/"
                        f"source-{index:03d}|Source decision {index:03d}]]"
                    )
                    self.assertEqual(
                        sum(text.count(needle) for text in projection_text), 1
                    )
                checked = self.check_vault(workspace)
                self.assertNotIn(
                    "generated navigation catalog exceeds 100 links",
                    checked.stdout,
                )

    def test_design_system_fragment_registers_contract_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            result = self.run_vault(
                "reconcile-payload-fragment", "--vault", workspace / "docs",
                "--fragment", "design-system",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            types = json.loads(
                (workspace / "docs/.obsidian/types.json").read_text(encoding="utf-8")
            )
            self.assertEqual(types["types"]["contract_version"], "number")

    def test_relation_contract_rejects_wrong_target_type(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            decisions = workspace / "docs/solution-design/decisions"
            decisions.mkdir(parents=True)
            note = decisions / "source-decision.md"
            note.write_text(
                self.decision_note(
                    "Source decision", "DEC-002",
                    "[[home|Home]]",
                ).replace("related_to:", "verifies:"),
                encoding="utf-8",
            )
            result = self.check_vault(workspace)
            self.assertEqual(result.returncode, 1)
            self.assertIn("relation 'verifies' cannot target type 'home'", result.stdout)

    def test_materialize_payload_is_idempotent_and_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary) / "docs"
            first = self.run_vault(
                "materialize-payload", "--vault", str(docs)
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            graph_path = docs / ".obsidian/graph.json"
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            graph["scale"] = 2
            graph_path.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
            before = graph_path.read_bytes()
            second = self.run_vault(
                "materialize-payload", "--vault", str(docs)
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(graph_path.read_bytes(), before)

    def test_brand_payload_and_enablement_converge_without_losing_appearance(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            workspace = self.setup_project(project)
            docs = workspace / "docs"
            brand = docs / ".obsidian/snippets/brand.css"
            brand.write_text("stale brand\n", encoding="utf-8")
            appearance_path = docs / ".obsidian/appearance.json"
            appearance = json.loads(appearance_path.read_text(encoding="utf-8"))
            appearance["accentColor"] = "#ABCDEF"
            appearance["enabledCssSnippets"] = ["project-custom"]
            appearance_path.write_text(
                json.dumps(appearance, indent=2) + "\n", encoding="utf-8"
            )

            failed = self.check_vault(workspace)
            self.assertEqual(failed.returncode, 1)
            self.assertIn("brand.css does not match", failed.stdout)
            self.assertIn("must include brand", failed.stdout)

            applied = subprocess.run(
                [sys.executable, str(SETUP), "apply", "--project-root",
                 str(project), "--json"], cwd=ROOT, capture_output=True,
                text=True, check=False,
            )
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            self.assertEqual(
                brand.read_bytes(),
                (PLUGIN / "templates/vault/.obsidian/snippets/brand.css").read_bytes(),
            )
            repaired = json.loads(appearance_path.read_text(encoding="utf-8"))
            self.assertEqual(repaired["accentColor"], "#ABCDEF")
            self.assertEqual(
                repaired["enabledCssSnippets"], ["project-custom", "brand"]
            )

    def test_standardize_graph_colors_preserves_unowned_knobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            graph_path = workspace / "docs/.obsidian/graph.json"
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            graph["colorGroups"][0]["color"]["rgb"] = 7
            graph["scale"] = 2
            graph_path.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
            first = self.run_vault(
                "standardize-graph-colors", "--vault", str(workspace / "docs")
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            repaired = json.loads(graph_path.read_text(encoding="utf-8"))
            policy = json.loads(POLICY.read_text(encoding="utf-8"))
            expected = {
                group["query"]: group["rgb"]
                for group in policy["graph_color_groups"]
            }
            actual = {
                group["query"]: group["color"]["rgb"]
                for group in repaired["colorGroups"]
            }
            self.assertEqual(actual, expected)
            self.assertEqual(repaired["scale"], 2)
            second = self.run_vault(
                "standardize-graph-colors", "--vault", str(workspace / "docs")
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertIn("already standard", second.stdout)

    def test_opaque_artifacts_accept_arbitrary_files_and_relative_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            docs = workspace / "docs"
            artifacts = docs / "design-system" / "artifacts"
            artifacts.mkdir(parents=True)
            (artifacts / "standalone.html").write_text("<!doctype html>", encoding="utf-8")
            (artifacts / "source.md").write_text("not a vault note", encoding="utf-8")
            (artifacts / "view.base").write_text("opaque", encoding="utf-8")
            (artifacts / "no-extension").write_bytes(b"opaque\x00bytes")
            home = docs / "home.md"
            home.write_text(
                home.read_text(encoding="utf-8")
                + "\n[Catalog](design-system/artifacts/standalone.html)\n",
                encoding="utf-8",
            )
            result = self.check_vault(workspace)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_artifacts_cannot_create_an_unknown_top_level_subtree(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            path = workspace / "docs" / "unknown" / "artifacts" / "anything.bin"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"x")
            result = self.check_vault(workspace)
            self.assertEqual(result.returncode, 1)
            self.assertIn("unknown top-level directory 'unknown'", result.stdout)


class AliasOwnershipTests(unittest.TestCase):
    """An id-shaped link alias targets the one note that owns the id."""

    REVIEW = "backlog/reviews/round-9-backlog-review.md"
    EPIC = "backlog/epics/workspace"

    @classmethod
    def setUpClass(cls):
        if str(PLUGIN / "scripts") not in sys.path:
            sys.path.insert(0, str(PLUGIN / "scripts"))
        import vault_check

        cls.vault_check = vault_check

    @staticmethod
    def note(title: str, *aliases: str, body: str = "") -> str:
        rows = "".join(f"  - {alias}\n" for alias in aliases)
        return (
            f"---\ntitle: {title}\n"
            + (f"aliases:\n{rows}" if rows else "")
            + f"---\n\n# {title}\n\n{body}"
        )

    def alias_findings(self, files: dict[str, str]) -> list[tuple]:
        """Check a vault whose review note links from body line 7 onward."""
        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary)
            for rel, text in files.items():
                path = docs / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            vault = self.vault_check.build_vault(
                docs, self.vault_check.load_policy(POLICY)
            )
            findings: list = []
            self.vault_check.check_alias_ownership(vault, findings)
        return [(f.path, f.line, f.check, f.message) for f in findings]

    def misowned(
        self, line: int, alias: str, target: str, owner: str
    ) -> tuple:
        return (
            self.REVIEW, line, "alias_ownership",
            f"alias '{alias}' decorates a link to '{target}' but the id's"
            f" owning note is {owner}",
        )

    def backlog_notes(self, review_body: str) -> dict[str, str]:
        return {
            f"{self.EPIC}/epic.md": self.note("Workspace epic", "EP-004"),
            f"{self.EPIC}/reviews/round-4-epic-review.md": self.note(
                "Workspace epic review", "EP-004-REVIEW-004"
            ),
            f"{self.EPIC}/stories/handoff/story.md": self.note(
                "Handoff story", "ST-009"
            ),
            "requirements/req-001-platform-naming.md": self.note(
                "Platform naming requirement", "REQ-001"
            ),
            "delivery/deliveries/dlv-001-first-slice/delivery.md": self.note(
                "First slice delivery", "DLV-001"
            ),
            self.REVIEW: self.note("Backlog review", body=review_body),
        }

    def test_note_owned_id_on_another_target_is_an_error(self):
        epic = self.EPIC
        review = f"{epic}/reviews/round-4-epic-review"
        requirement = "requirements/req-001-platform-naming"
        delivery = "delivery/deliveries/dlv-001-first-slice/delivery"
        files = self.backlog_notes(
            f"- [[{review}|EP-004]]\n"
            f"- [[{epic}/epic|ST-009]]\n"
            f"- [[{delivery}|REQ-001]]\n"
            f"- [[{requirement}|DLV-001]]\n"
        )
        self.assertEqual(self.alias_findings(files), [
            self.misowned(7, "EP-004", review, f"{epic}/epic.md"),
            self.misowned(8, "ST-009", f"{epic}/epic",
                          f"{epic}/stories/handoff/story.md"),
            self.misowned(9, "REQ-001", delivery, f"{requirement}.md"),
            self.misowned(10, "DLV-001", requirement, f"{delivery}.md"),
        ])

    def test_note_owned_id_on_its_owner_passes(self):
        epic = self.EPIC
        files = self.backlog_notes(
            f"- [[{epic}/epic|EP-004]]\n"
            f"- [[{epic}/epic#^scope|EP-004]]\n"
            f"| epic | [[{epic}/epic\\|EP-004]] |\n"
            f"- [[{epic}/stories/handoff/story|EP-004]]\n"
        )
        # Only the fourth link misses its owner; the first three pass.
        self.assertEqual(self.alias_findings(files), [
            self.misowned(10, "EP-004", f"{epic}/stories/handoff/story",
                          f"{epic}/epic.md"),
        ])

    def test_id_declared_by_two_notes_is_skipped(self):
        one = "backlog/epics/alpha/stories/one/story"
        two = "backlog/epics/alpha/stories/two/story"
        files = {
            f"{one}.md": self.note("Alpha story one", "ST-001"),
            "backlog/epics/beta/stories/one/story.md": self.note(
                "Beta story one", "ST-001"
            ),
            # One note listing its id twice still owns it, and a
            # machine-directory note never competes for ownership.
            f"{two}.md": self.note("Alpha story two", "ST-002", "ST-002"),
            "backlog/_generated/mirror.md": self.note(
                "Machine mirror", "ST-002"
            ),
            self.REVIEW: self.note(
                "Backlog review",
                body=f"- [[{two}|ST-001]]\n- [[{one}|ST-002]]\n",
            ),
        }
        self.assertEqual(self.alias_findings(files), [
            self.misowned(8, "ST-002", one, f"{two}.md"),
        ])

    def test_decision_and_registry_ownership_is_unchanged(self):
        decision = "solution-design/decisions/dispatch-decision"
        story = f"{self.EPIC}/stories/dispatch/story"
        rules = "business-analysis/shop/domains/inventory/rules/stock-rules"
        item = "business-analysis/shop/domains/inventory/entities/item-entity"
        # The story and the entity also declare the ids, which never
        # outranks the decision tree or the space registry.
        files = {
            f"{decision}.md": self.note("Dispatch decision", "SD-001"),
            f"{story}.md": self.note("Dispatch story", "ST-001", "SD-001"),
            f"{rules}.md": self.note("Stock rules"),
            f"{item}.md": self.note("Item entity", "BR-INV-001"),
            "business-analysis/shop/_generated/registry.json": json.dumps({
                "ids": {"BR-INV-001": {
                    "doc": "domains/inventory/rules/stock-rules.md"}},
            }),
            self.REVIEW: self.note(
                "Backlog review",
                body=(
                    f"- [[{decision}|SD-001]]\n"
                    f"- [[{story}|SD-001]]\n"
                    f"- [[{rules}|BR-INV-001]]\n"
                    f"- [[{item}|BR-INV-001]]\n"
                ),
            ),
        }
        self.assertEqual(self.alias_findings(files), [
            self.misowned(8, "SD-001", story, f"{decision}.md"),
            (
                self.REVIEW, 10, "alias_ownership",
                f"alias 'BR-INV-001' decorates a link to '{item}' but the"
                f" registry declares its owner as {rules}.md",
            ),
        ])


if __name__ == "__main__":
    unittest.main()
