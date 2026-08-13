"""Deterministic preparation, experience and backlog compiler contracts."""

from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import tempfile
import unittest
import sys
import subprocess
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "plugins" / "software-engineering-team" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


EXPERIENCE = module("experience_compile")
ARTIFACT = module("experience_artifact_check")
DESIGN_SYSTEM = module("design_system_compile")
BACKLOG = module("backlog_compile")
PREPARATION = module("preparation_check")
PROJECT_CONFIG = module("project_config")
SETUP_CHECK = module("setup_check")
VAULT_GATE = module("vault_gate")
CURRENT_PROJECT_CONTRACT_VERSION = (
    SETUP_CHECK.marketplace_paths.CURRENT_PROJECT_CONTRACT_VERSION
)


def call(target, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = target.main(argv)
    return code, out.getvalue(), err.getvalue()


def complete_designations() -> dict[str, str]:
    content = REPO / "plugins" / "software-engineering-team" / "skill-content"
    schema = json.loads((content / "business-analysis" / "data"
                         / "space-schema.json").read_text(encoding="utf-8"))
    policy = json.loads((content / "obsidian-vault" / "data"
                         / "vault-policy.json").read_text(encoding="utf-8"))
    types = {key.replace("_", "-") for key in schema["doc_types"]}
    types.update(policy["extra_doc_types"])
    types.difference_update({"home", "moc"})
    return {key: f"{key} designation" for key in sorted(types)}


class ExperienceCompilerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        self.root = self.project / "workspace" / "docs" / "experience-design"
        docs = self.root.parent
        docs.mkdir(parents=True)
        (docs / "home.md").write_text("# Home\n", encoding="utf-8")
        ba = docs / "business-analysis" / "marketplace"
        (ba / "_generated").mkdir(parents=True)
        (ba / "space.md").write_text(
            "---\nstatus: approved\n---\n# Marketplace\n", encoding="utf-8"
        )
        registry_bytes = (json.dumps({
            "schema_version": 3,
            "codes": {"CAT": "domains/catalog"},
            "ids": {
                "AC-CAT-001": {
                    "doc": "domains/catalog/acceptance/catalog-acceptance.md",
                    "doc_status": "approved",
                },
                "AC-CAT-002": {
                    "doc": "domains/catalog/acceptance/catalog-acceptance.md",
                    "doc_status": "approved",
                },
            },
        }, indent=2, sort_keys=True) + "\n").encode()
        (ba / "_generated" / "registry.json").write_bytes(registry_bytes)
        self.analysis_hash = "sha256:" + hashlib.sha256(registry_bytes).hexdigest()
        call(EXPERIENCE, ["init-program", "--root", str(self.root),
                          "--program", "PRG-001", "--title", "Marketplace Program"])
        call(EXPERIENCE, ["init-release", "--root", str(self.root),
                          "--program", "PRG-001", "--release", "REL-001",
                          "--title", "Marketplace Release"])
        self.release = self.root / "programs" / "prg-001" / "releases" / "rel-001"

    def tearDown(self):
        self.tmp.cleanup()

    def stub(self, kind, ident, slug, *extra):
        design = (["--uses-design", "[[design-system/MASTER|Design System]]"]
                  if kind == "screen" else [])
        return call(EXPERIENCE, [
            "stub", "--release-root", str(self.release), "--kind", kind,
            "--id", ident, "--slug", slug,
            "--scope", "marketplace#domains/catalog",
            "--analysis-hash", self.analysis_hash,
            "--criterion-set", "marketplace:AC-CAT-001", *design, *extra,
        ])

    def test_init_stub_render_and_gate(self):
        code, _, err = self.stub("journey", "JRN-001", "browse")
        self.assertEqual(code, 0, err)
        code, _, err = self.stub("screen", "SCR-001", "catalog")
        self.assertEqual(code, 0, err)
        projection = self.release / "spaces" / "marketplace" / "domains" / "catalog" / "domain.md"
        self.assertTrue(projection.is_file())
        self.assertIn("analysis_hash: sha256:", projection.read_text(encoding="utf-8"))
        code, out, err = call(EXPERIENCE, ["render", "--release-root", str(self.release)])
        self.assertEqual(code, 0, err)
        registry = json.loads((self.release / "_generated" / "effective-registry.json").read_text())
        self.assertEqual([row["id"] for row in registry["records"]], ["JRN-001", "SCR-001"])
        owner = "spaces/marketplace/domains/catalog"
        code, _, err = call(EXPERIENCE, [
            "init-artifact", "--release-root", str(self.release),
            "--owner", owner,
            "--package", "catalog",
        ])
        self.assertEqual(code, 0, err)
        artifact = self.release / owner / "artifacts" / "catalog-preview.html"
        artifact.write_text(
            artifact.read_text(encoding="utf-8").replace(
                "</body>",
                '<div id="SCR-001" data-experience-id="SCR-001"></div></body>',
            ),
            encoding="utf-8",
        )
        code, _, err = call(EXPERIENCE, [
            "approve-artifact", "--release-root", str(self.release),
            "--owner", owner, "--package", "catalog",
        ])
        self.assertEqual(code, 0, err)
        code, out, _ = call(ARTIFACT, [
            "--artifact", str(artifact), "--release-root", str(self.release),
            "--owner", owner, "--declared-id", "SCR-001", "--json",
        ])
        self.assertEqual(code, 0, out)
        code, _, _ = call(EXPERIENCE, ["check", "--release-root", str(self.release), "--gate"])
        self.assertEqual(code, 1)
        code, _, err = call(EXPERIENCE, ["stamp", "--release-root", str(self.release),
                                        "--challenge-hash", "sha256:review"])
        self.assertEqual(code, 0, err)
        code, out, err = call(EXPERIENCE, ["check", "--release-root", str(self.release), "--gate"])
        self.assertEqual(code, 0, out + err)
        code, _, err = call(EXPERIENCE, [
            "render", "--root", str(self.root), "--program", "PRG-001",
        ])
        self.assertEqual(code, 0, err)
        program_generated = self.root / "programs" / "prg-001" / "_generated"
        self.assertEqual(
            {path.name for path in program_generated.iterdir()},
            {"program-registry.json", "release-matrix.md", "coverage.md", "status.md"},
        )
        code, _, _ = call(EXPERIENCE, [
            "check", "--root", str(self.root), "--program", "PRG-001", "--gate",
        ])
        self.assertEqual(code, 1)
        code, _, err = call(EXPERIENCE, [
            "stamp", "--root", str(self.root), "--program", "PRG-001",
            "--challenge-hash", "sha256:program-review",
        ])
        self.assertEqual(code, 0, err)
        code, _, err = call(EXPERIENCE, [
            "check", "--root", str(self.root), "--program", "PRG-001", "--gate",
        ])
        self.assertEqual(code, 0, err)
        registry_path = self.root.parent / "business-analysis" / "marketplace" / "_generated" / "registry.json"
        registry_path.write_text(registry_path.read_text() + "\n", encoding="utf-8")
        code, out, _ = call(EXPERIENCE, ["check", "--release-root", str(self.release)])
        self.assertEqual(code, 1)
        self.assertIn("BA registry hash is stale", out)

    def test_lowest_common_ancestor_and_duplicate_identity_fail(self):
        self.stub("journey", "JRN-001", "browse")
        note = self.release / "spaces" / "marketplace" / "domains" / "catalog" / "journeys" / "browse-journey.md"
        wrong = self.release / "journeys" / "browse-journey.md"
        wrong.parent.mkdir(exist_ok=True)
        wrong.write_text(note.read_text(encoding="utf-8"), encoding="utf-8")
        code, out, _ = call(EXPERIENCE, ["check", "--release-root", str(self.release)])
        self.assertEqual(code, 1)
        self.assertIn("duplicate identity", out)
        self.assertIn("scope owner", out)

    def test_release_inheritance_requires_revision_and_exact_supersedes(self):
        self.stub("screen", "SCR-001", "catalog")
        self.assertEqual(call(EXPERIENCE, ["render", "--release-root", str(self.release)])[0], 0)
        call(EXPERIENCE, ["init-release", "--root", str(self.root),
                          "--program", "PRG-001", "--release", "REL-002",
                          "--inherits", "REL-001"])
        second = self.release.parent / "rel-002"
        code, _, err = call(EXPERIENCE, [
            "stub", "--release-root", str(second), "--kind", "screen",
            "--id", "SCR-001", "--revision", "2", "--slug", "catalog",
            "--scope", "marketplace#domains/catalog",
            "--analysis-hash", self.analysis_hash,
            "--supersedes", "PRG-001:SCR-001@r1",
            "--uses-design", "[[design-system/MASTER|Design System]]",
        ])
        self.assertEqual(code, 0, err)
        self.assertEqual(call(EXPERIENCE, ["render", "--release-root", str(second)])[0], 0)

    def test_enterprise_scale_accepts_ten_thousand_flows(self):
        release_note = self.release / "release.md"
        release_note.write_text(release_note.read_text().replace("scale: small", "scale: enterprise"), encoding="utf-8")
        self.stub("flow-set", "FLW-001", "catalog")
        note = self.release / "spaces" / "marketplace" / "domains" / "catalog" / "flows" / "catalog-flows.md"
        text = note.read_text(encoding="utf-8")
        flows = json.dumps([f"variation-{value}" for value in range(10000)])
        note.write_text(text.replace("criterion_refs:\n", f"flows: {flows}\ncriterion_refs:\n"), encoding="utf-8")
        code, _, err = call(EXPERIENCE, ["check", "--release-root", str(self.release)])
        self.assertEqual(code, 0, err)

    def test_artifact_rejects_remote_assets_and_metadata_drift(self):
        artifact = self.release / "artifacts" / "catalog-preview.html"
        artifact.write_text('<meta name="experience-program" content="WRONG"><script src="https://example.test/x.js"></script><div data-experience-id="SCR-001"></div>', encoding="utf-8")
        registry = self.release / "_generated" / "effective-registry.json"
        registry.parent.mkdir(exist_ok=True)
        registry.write_text(json.dumps({"program_id": "PRG-001", "release_id": "REL-001", "registry_hash": "sha256:x"}), encoding="utf-8")
        code, out, _ = call(ARTIFACT, ["--artifact", str(artifact), "--release-root", str(self.release), "--owner", ".", "--declared-id", "SCR-001", "--json"])
        self.assertEqual(code, 1)
        result = json.loads(out)
        self.assertTrue(any("remote" in value for value in result["findings"]))


class DesignSystemCompilerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "workspace" / "docs" / "design-system"
        (self.root / "pages").mkdir(parents=True)
        (self.root / "MASTER.md").write_text(
            "---\ntype: design_master\ntitle: Shop design master\n"
            "status: draft\nrevision: 1\ntags:\n  - doc/design-master\n"
            "---\n\n# Shop design master\n\nRules.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_approve_and_begin_revision_preserve_hash_chain(self):
        code, _, err = call(DESIGN_SYSTEM, [
            "approve", "--root", str(self.root),
        ])
        self.assertEqual(code, 0, err)
        self.assertEqual(call(DESIGN_SYSTEM, [
            "check", "--root", str(self.root),
        ])[0], 0)
        approved = DESIGN_SYSTEM.parse_frontmatter(
            self.root / "MASTER.md"
        )[0]
        prior_hash = approved["baseline_hash"]
        code, _, err = call(DESIGN_SYSTEM, [
            "begin-revision", "--root", str(self.root),
        ])
        self.assertEqual(code, 0, err)
        draft = DESIGN_SYSTEM.parse_frontmatter(self.root / "MASTER.md")[0]
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["revision"], 2)
        self.assertEqual(draft["supersedes_hash"], prior_hash)
        self.assertNotIn("baseline_hash", draft)

    def test_approved_content_drift_fails_check(self):
        self.assertEqual(call(DESIGN_SYSTEM, [
            "approve", "--root", str(self.root),
        ])[0], 0)
        master = self.root / "MASTER.md"
        master.write_text(
            master.read_text(encoding="utf-8") + "Changed.\n",
            encoding="utf-8",
        )
        code, out, _ = call(DESIGN_SYSTEM, [
            "check", "--root", str(self.root),
        ])
        self.assertEqual(code, 1)
        self.assertIn("baseline_hash is stale", out)


class BacklogCompilerTests(unittest.TestCase):
    def plan(self):
        return {
            "mode": "baseline", "program_id": "PRG-001",
            "releases": [{"release_id": "REL-001",
                          "experience_registry": "registry.json",
                          "experience_registry_hash": "sha256:registry"}],
            "epics": [{"external_id": "EP-01", "title": "Catalog",
                       "goal": "Customers browse the catalog."}],
            "stories": [{
                "external_id": "WP-01", "title": "Catalog", "scope": "Browse",
                "excludes": "Checkout", "priority": "high: first",
                "epic": "EP-01",
                "release_id": "REL-001", "delivery_owners": {"owner": "frontend"},
                "dor": ["Inputs approved"], "dod": ["Behavior verified"],
                "criteria": ["marketplace:AC-CAT-001"], "ui": True,
                "ux_refs": ["PRG-001:SCR-001@r1"],
                "solution_refs": ["SD-001"], "budget_refs": ["BUD-001"],
                "depends_on": [],
            }],
            "shares": [], "findings": [],
            "gates": {"reviewer": "approved", "domains": ["marketplace"], "program": "approved"},
        }

    def test_valid_plan_and_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            (Path(tmp) / "registry.json").write_text(json.dumps({
                "program_id": "PRG-001", "release_id": "REL-001",
                "registry_hash": "sha256:registry",
                "records": [{"id": "SCR-001", "revision": 1}],
            }), encoding="utf-8")
            plan = self.plan(); path.write_text(json.dumps(plan), encoding="utf-8")
            code, out, _ = call(BACKLOG, ["check", "--plan", str(path), "--mode", "baseline", "--json"])
            self.assertEqual(code, 0, out)
            code, out, _ = call(BACKLOG, [
                "verify-apply", "--plan", str(path),
                "--draft-hash", BACKLOG.plan_hash(plan),
            ])
            self.assertEqual(code, 0, out)
            draft = self.plan(); draft["gates"] = {"domains": ["marketplace"]}
            path.write_text(json.dumps(draft), encoding="utf-8")
            code, out, _ = call(BACKLOG, ["check", "--plan", str(path), "--mode", "baseline", "--json"])
            self.assertEqual(code, 0, out)
            plan["stories"][0]["criteria"] = ["legacy:AC-CAT-001"]
            path.write_text(json.dumps(plan), encoding="utf-8")
            code, out, _ = call(BACKLOG, ["check", "--plan", str(path), "--mode", "baseline", "--json"])
            self.assertEqual(code, 0, out)
            plan["stories"].append({**plan["stories"][0], "external_id": "WP-02", "criteria": ["marketplace:AC-CAT-002"], "depends_on": [{"item": "WP-01", "reason": "Consumes catalog output."}]})
            plan["stories"][0]["depends_on"] = [{"item": "WP-02", "reason": "Consumes profile output."}]
            path.write_text(json.dumps(plan), encoding="utf-8")
            code, out, _ = call(BACKLOG, ["check", "--plan", str(path), "--mode", "baseline", "--json"])
            self.assertEqual(code, 1)
            self.assertIn("dependency cycle", out)


class PreparationTests(unittest.TestCase):
    def test_origin_and_greenfield_route_are_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); work = root / "workspace"; work.mkdir()
            (work / "config.json").write_text(json.dumps({"team_id": "software-engineering-team", "project_key": "shop", "project_origin": "greenfield"}), encoding="utf-8")
            result = PREPARATION.inspect(root, "deliver")
            self.assertEqual(result["next_entry"], "business-analysis")
            (work / "config.json").write_text(json.dumps({
                "project_key": "shop", "project_origin": "greenfield",
                "agent_marketplace": {
                    "contract_version": CURRENT_PROJECT_CONTRACT_VERSION,
                    "team_id": "software-engineering-team",
                    "vault": {"status": "active"},
                },
            }), encoding="utf-8")
            result = PREPARATION.inspect(root, "deliver")
            self.assertEqual(result["next_entry"], "business-analysis")
            config = json.loads((work / "config.json").read_text()); config["project_origin"] = "existing"
            (work / "config.json").write_text(json.dumps(config), encoding="utf-8")
            result = PREPARATION.inspect(root, "deliver")
            self.assertEqual(result["next_entry"], "deliver")

    def test_registered_origin_change_requires_pmo_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            config = workspace / "config.json"
            config.write_text(json.dumps({
                "project_key": "shop",
                "project_origin": "unclassified",
                "agent_marketplace": {
                    "contract_version": CURRENT_PROJECT_CONTRACT_VERSION,
                    "team_id": "software-engineering-team",
                },
            }), encoding="utf-8")
            code, _, err = call(PROJECT_CONFIG, [
                "set-origin", "--config", str(config), "--origin", "existing",
            ])
            self.assertEqual(code, 1)
            self.assertIn("PMO project classify-origin", err)
            self.assertEqual(
                json.loads(config.read_text(encoding="utf-8"))["project_origin"],
                "unclassified",
            )

    def test_setup_preflight_and_closing_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            workspace = root / "workspace"
            workspace.mkdir()
            config = workspace / "config.json"
            config.write_text(json.dumps({
                "team_id": "software-engineering-team",
                "project_origin": "greenfield",
            }), encoding="utf-8")
            code, _, _ = call(SETUP_CHECK, [
                "preflight", "--project-root", str(root), "--json",
            ])
            self.assertEqual(code, 0)
            value = json.loads(config.read_text(encoding="utf-8"))
            value["project_key"] = "shop"
            config.write_text(json.dumps(value), encoding="utf-8")
            for relative in (
                "apps", "environment", "demos", "sketches",
                "docs/business-analysis", "docs/solution-design",
                "docs/system-architecture", "docs/design-system/pages",
                "docs/experience-design",
            ):
                (workspace / relative).mkdir(parents=True)
            payload = workspace / "docs" / ".obsidian"
            payload.mkdir(parents=True)
            for name in (
                "app.json", "appearance.json", "core-plugins.json",
                "graph.json", "types.json",
            ):
                (payload / name).write_text("{}\n", encoding="utf-8")
            value = json.loads(config.read_text(encoding="utf-8"))
            value.pop("team_id", None)
            contract = {
                "schema_version": 1,
                "contract_version": CURRENT_PROJECT_CONTRACT_VERSION,
                "project_id": "setup-project",
                "team_id": "software-engineering-team",
                "workspace": "workspace",
                "repository_fingerprint": "sha256:test",
                "delivery": {"requires_pull_request": False,
                             "target_branch": "master"},
                "marketplace_release": "0.1.0",
                "source_channel": "stable", "source_ref": "v0.1.0",
                "source_commit": "test",
                "components": {
                    "project-management-office": {
                        "version": "0.0.2", "build_id": "snapshot.test",
                    },
                    "software-engineering-team": {
                        "version": "0.4.0", "build_id": "snapshot.test",
                    },
                },
                "managed_surfaces": {
                    "codex:AGENTS.md": "sha256:test",
                    "claude:CLAUDE.md": "sha256:test",
                },
                "vault": {}, "upgrade_provenance": {},
            }
            contract["contract_sha256"] = hashlib.sha256(json.dumps(
                contract, sort_keys=True, separators=(",", ":")
            ).encode()).hexdigest()
            value["agent_marketplace"] = contract
            config.write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(call(VAULT_GATE, [
                "install", "--project-root", str(root),
            ])[0], 0)
            (root / ".gitignore").write_text(
                "user-rule\n\n" + SETUP_CHECK.managed_block("workspace") + "\n",
                encoding="utf-8",
            )
            code, out, _ = call(SETUP_CHECK, [
                "check", "--project-root", str(root), "--json",
            ])
            self.assertEqual(code, 1)
            self.assertIn("type designations are not configured", out)
            value = json.loads(config.read_text(encoding="utf-8"))
            value["doc_type_designations"] = complete_designations()
            config.write_text(json.dumps(value), encoding="utf-8")
            code, out, _ = call(SETUP_CHECK, [
                "check", "--project-root", str(root), "--json",
            ])
            self.assertEqual(code, 0, out)
            self.assertTrue((root / ".gitignore").read_text().startswith("user-rule"))
            code, out, _ = call(SETUP_CHECK, [
                "preflight", "--project-root", str(root), "--json",
            ])
            self.assertEqual(code, 1)
            self.assertIn("environment reconciliation", out)

    def test_portable_vault_gate_install_is_byte_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = root / "workspace" / "docs"
            docs.mkdir(parents=True)
            (docs / "home.md").write_text("# Home\n", encoding="utf-8")
            argv = ["install", "--project-root", str(root)]
            self.assertEqual(call(VAULT_GATE, argv)[0], 0)
            gate = root / ".github" / "agentrof" / "vault-gate.pyz"
            first = gate.read_bytes()
            self.assertEqual(call(VAULT_GATE, argv)[0], 0)
            self.assertEqual(first, gate.read_bytes())
            with zipfile.ZipFile(gate) as archive:
                packaged = {
                    name for name in archive.namelist()
                    if name.startswith("scripts/") and name.endswith(".py")
                }
                expected = {
                    f"scripts/{name}"
                    for name in VAULT_GATE.packaged_scripts(
                        VAULT_GATE.package_root()
                    )
                }
                self.assertEqual(packaged, expected)
                self.assertIn("scripts/marketplace_paths.py", packaged)
            completed = subprocess.run(
                [sys.executable, str(gate), "check", "--project-root",
                 str(root), "--json"], capture_output=True, text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 1)
            self.assertIn("closed-vault-schema-and-relations", completed.stdout)
            result = json.loads(completed.stdout)
            self.assertEqual(result["results"][0]["stderr"], "")


if __name__ == "__main__":
    unittest.main()
