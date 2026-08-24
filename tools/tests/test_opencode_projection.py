"""Project-local OpenCode projection and lifecycle acceptance tests."""

from __future__ import annotations

import json
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "dist" / "opencode" / "software-engineering-team"


class OpenCodeProjectionTests(unittest.TestCase):
    def test_distribution_bytes_are_eol_immutable(self):
        result = subprocess.run(
            [
                "git", "check-attr", "text", "--",
                "dist/opencode/software-engineering-team/.agent-marketplace-package.json",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            result.stdout,
            "dist/opencode/software-engineering-team/.agent-marketplace-package.json: text: unset\n",
        )

    def test_runtime_plugin_accepts_windows_drive_plugin_references(self):
        plugin = (PACKAGE / "plugins/agent-marketplace-software-engineering-team.js").read_text(
            encoding="utf-8"
        )
        windows_path = "if (/^[A-Za-z]:[\\\\/]/.test(value))"
        scheme = "if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(value))"
        self.assertIn(windows_path, plugin)
        self.assertIn(scheme, plugin)
        self.assertLess(plugin.index(windows_path), plugin.index(scheme))

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        shutil.copytree(PACKAGE, self.source)
        self.project = self.root / "project"
        self.project.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def invoke(self, script: Path, *args: str, expected: int = 0) -> dict:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(
            [sys.executable, "-B", str(script), *args],
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=60,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def projector(self, *args: str, expected: int = 0) -> dict:
        return self.invoke(
            self.source / "scripts" / "project_opencode.py",
            *args,
            "--project-root", str(self.project),
            "--development-source",
            expected=expected,
        )

    def apply(self, *extra: str, expected: int = 0) -> dict:
        return self.projector(
            "apply", "--clients-stopped", *extra, expected=expected
        )

    def installed_manage(self) -> Path:
        return self.project / ".opencode/agentrof/agent-marketplace/manage.py"

    def bind_fake_runtime(self) -> Path:
        executable = self.root / "opencode"
        plugin = self.project / ".opencode/plugins/agent-marketplace-software-engineering-team.js"
        executable.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"--version\" ]; then echo 1.18.17; exit 0; fi\n"
            "if [ \"$1\" = \"debug\" ] && [ \"$2\" = \"config\" ]; then\n"
            f"  printf '%s\\n' '{json.dumps({'plugin': [str(plugin)]})}'\n"
            "  exit 0\n"
            "fi\n"
            "exit 1\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        self.invoke(
            self.installed_manage(), "bind-runtime", "--opencode", str(executable)
        )
        return executable

    def invoke_runtime_plugin(self, body: str, *, expected: int = 0) -> subprocess.CompletedProcess[str]:
        node = shutil.which("node")
        self.assertIsNotNone(node)
        script = self.root / "runtime-probe.mjs"
        script.write_text(
            "import { pathToFileURL } from 'node:url';\n"
            "const module = await import(pathToFileURL(process.argv[2]).href);\n"
            "const hooks = await module.AgentMarketplacePlugin();\n"
            + body,
            encoding="utf-8",
        )
        plugin = self.project / ".opencode/plugins/agent-marketplace-software-engineering-team.js"
        result = subprocess.run(
            [str(node), str(script), str(plugin), str(self.project)],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def evolve_source_without_command(self, command: str) -> None:
        """Make an intentionally different development package for update tests."""
        removed = self.source / "commands" / f"{command}.md"
        removed.unlink()
        manifest_path = self.source / ".agent-marketplace-package.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"].pop(f"commands/{command}.md")
        manifest["build_id"] = "snapshot." + ("f" * 64)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def stable_source(self) -> Path:
        repository = self.root / "stable-source"
        package = repository / "dist/opencode/software-engineering-team"
        package.parent.mkdir(parents=True)
        shutil.copytree(PACKAGE, package)
        shutil.copy2(ROOT / "product.json", repository / "product.json")
        version = json.loads((package / ".agent-marketplace-package.json").read_text())["version"]
        subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.name", "Projection Test"], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.email", "projection@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repository), "remote", "add", "origin",
                        "https://github.com/agentrof/agent-marketplace.git"], check=True)
        subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repository), "commit", "-m", "stable package"], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(repository), "tag", "-a", f"v{version}", "-m", "stable"],
                       check=True)
        return package

    def test_non_git_apply_is_project_local_and_survives_source_deletion(self):
        inspected = self.projector("inspect")
        self.assertTrue(inspected["ok"])
        applied = self.apply()
        self.assertTrue(applied["ok"])
        self.assertIn("/.opencode/", (self.project / ".gitignore").read_text())
        private = self.project / ".opencode/agentrof/agent-marketplace"
        installation = json.loads((private / "installation.json").read_text())
        self.assertEqual(installation["component"], "software-engineering-team")
        self.assertEqual(installation["tested_opencode_versions"], ["1.18.17"])
        self.assertTrue((self.project / ".opencode/plugins/agent-marketplace-software-engineering-team.js").is_file())
        self.assertEqual(list(private.rglob("__pycache__")), [])
        self.assertEqual(list(private.rglob("*.pyc")), [])

        shutil.rmtree(self.source)
        checked = self.invoke(self.installed_manage(), "check")
        self.assertTrue(checked["ok"])

    def test_tracked_opencode_fails_before_writes(self):
        subprocess.run(["git", "init", str(self.project)], check=True, capture_output=True)
        tracked = self.project / ".opencode" / "user.md"
        tracked.parent.mkdir()
        tracked.write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.project), "add", ".opencode/user.md"], check=True)
        result = self.projector("inspect", expected=1)
        self.assertEqual(result["code"], "tracked_projection_conflict")
        self.assertFalse((self.project / ".gitignore").exists())

    def test_source_and_target_overlap_are_rejected_before_writes(self):
        result = self.invoke(
            self.source / "scripts" / "project_opencode.py",
            "inspect", "--project-root", str(self.source), "--development-source",
            expected=1,
        )
        self.assertEqual(result["code"], "source_target_overlap")

    def test_stable_projection_requires_clean_annotated_trusted_source(self):
        source = self.stable_source()
        inspector = source / "scripts/project_opencode.py"
        inspected = self.invoke(inspector, "inspect", "--project-root", str(self.project))
        self.assertTrue(inspected["ok"])
        self.assertEqual(inspected["source"]["trust_mode"], "annotated_tag")

        (source.parents[2] / "untracked.txt").write_text("dirty\n", encoding="utf-8")
        rejected = self.invoke(inspector, "inspect", "--project-root", str(self.project), expected=1)
        self.assertEqual(rejected["code"], "source_dirty")

    def test_untracked_host_owned_files_require_ack_and_are_preserved(self):
        package_json = self.project / ".opencode" / "package.json"
        package_json.parent.mkdir()
        package_json.write_text('{"host":"owned"}\n', encoding="utf-8")
        rejected = self.apply(expected=1)
        self.assertEqual(rejected["code"], "untracked_projection_ack_required")
        self.assertEqual(package_json.read_text(encoding="utf-8"), '{"host":"owned"}\n')
        applied = self.apply("--acknowledge-untracked-opencode")
        self.assertTrue(applied["ok"])
        self.assertEqual(package_json.read_text(encoding="utf-8"), '{"host":"owned"}\n')

    def test_manage_detects_modified_public_file_and_uninstalls_only_owned_files(self):
        self.apply()
        plugin = self.project / ".opencode/plugins/agent-marketplace-software-engineering-team.js"
        plugin.write_text("modified\n", encoding="utf-8")
        drift = self.invoke(self.installed_manage(), "check", expected=1)
        self.assertEqual(drift["code"], "projection_drift")
        rejected = self.invoke(
            self.installed_manage(), "uninstall", "--clients-stopped", expected=1
        )
        self.assertTrue(rejected["code"].startswith("owned_file_modified"))
        self.assertTrue(plugin.exists())

        original = (
            self.source / "plugins/agent-marketplace-software-engineering-team.js"
        ).read_text(encoding="utf-8")
        plugin.write_text(original, encoding="utf-8")
        host_owned = self.project / ".opencode" / "package.json"
        host_owned.write_text('{"host":"owned"}\n', encoding="utf-8")
        removed = self.invoke(
            self.installed_manage(), "uninstall", "--clients-stopped"
        )
        self.assertTrue(removed["ok"])
        self.assertTrue(host_owned.exists())
        self.assertFalse((self.project / ".opencode/agentrof/agent-marketplace").exists())

    def test_update_removes_only_hash_verified_obsolete_public_files(self):
        self.apply()
        obsolete = self.project / ".opencode/commands/issue-report.md"
        self.assertTrue(obsolete.is_file())
        self.evolve_source_without_command("issue-report")

        updated = self.apply()
        self.assertTrue(updated["ok"])
        self.assertFalse(obsolete.exists())
        installation = json.loads(
            (self.project / ".opencode/agentrof/agent-marketplace/installation.json").read_text()
        )
        self.assertNotIn("commands/issue-report.md", installation["public_owned_files"])
        self.assertEqual(len(installation["retained_builds"]), 1)

    def test_update_refuses_to_delete_modified_obsolete_public_file(self):
        self.apply()
        obsolete = self.project / ".opencode/commands/issue-report.md"
        obsolete.write_text("user modification\n", encoding="utf-8")
        self.evolve_source_without_command("issue-report")

        rejected = self.apply(expected=1)
        self.assertEqual(rejected["code"], "owned_file_modified")
        self.assertEqual(obsolete.read_text(encoding="utf-8"), "user modification\n")

    def test_runtime_binding_is_identity_checked(self):
        self.apply()
        executable = self.bind_fake_runtime()
        healthy = self.invoke(self.installed_manage(), "check")
        self.assertTrue(healthy["ok"])

        executable.write_text("#!/bin/sh\necho 1.18.17 # changed\n", encoding="utf-8")
        drift = self.invoke(self.installed_manage(), "check", expected=1)
        self.assertEqual(drift["code"], "runtime_binding_drift")
        self.assertIn("runtime_binding_drift", drift["findings"])

    def test_runtime_plugin_invokes_canonical_pre_and_post_guards(self):
        self.apply()
        self.bind_fake_runtime()
        workspace = self.project / "workspace"
        workspace.mkdir()
        config = workspace / "config.json"
        original = {
            "schema_version": 1,
            "team_id": "software-engineering-team",
            "output_language": "English",
            "terminology_language": "English",
        }
        config.write_text(json.dumps(original, sort_keys=True) + "\n", encoding="utf-8")

        denied = self.invoke_runtime_plugin(
            """
const root = process.argv[3];
const args = {
  filePath: `${root}/workspace/config.json`,
  content: JSON.stringify({ schema_version: 9 }),
};
try {
  await hooks['tool.execute.before'](
    { tool: 'write', sessionID: 'pre-session', callID: 'pre-call' },
    { args },
  );
  process.exitCode = 9;
} catch (error) {
  console.log(error.message);
}
"""
        )
        self.assertIn("pre_hook_denied", denied.stdout)
        self.assertEqual(json.loads(config.read_text(encoding="utf-8")), original)

        restored = self.invoke_runtime_plugin(
            """
import { writeFileSync } from 'node:fs';
const root = process.argv[3];
const args = { command: 'mutate config', workdir: root };
const input = { tool: 'bash', sessionID: 'post-session', callID: 'post-call' };
await hooks['tool.execute.before'](input, { args });
writeFileSync(`${root}/workspace/config.json`, '{"schema_version":9}\\n');
try {
  await hooks['tool.execute.after']({ ...input, args });
  process.exitCode = 9;
} catch (error) {
  console.log(error.message);
}
"""
        )
        self.assertIn("post_hook_failed", restored.stdout)
        self.assertEqual(json.loads(config.read_text(encoding="utf-8")), original)

        allowed = self.invoke_runtime_plugin(
            """
import { writeFileSync } from 'node:fs';
const root = process.argv[3];
const args = { filePath: `${root}/ordinary.txt`, content: 'allowed' };
const input = { tool: 'write', sessionID: 'allow-session', callID: 'allow-call' };
await hooks['tool.execute.before'](input, { args });
writeFileSync(args.filePath, args.content);
await hooks['tool.execute.after']({ ...input, args });
console.log('allowed');
"""
        )
        self.assertIn("allowed", allowed.stdout)
        self.assertEqual((self.project / "ordinary.txt").read_text(), "allowed")

        patch_allowed = self.invoke_runtime_plugin(
            """
import { writeFileSync } from 'node:fs';
const root = process.argv[3];
const args = {
  patchText: '*** Begin Patch\\n*** Update File: probe-patch.txt\\n@@\\n-before\\n+after\\n*** End Patch\\n',
};
const input = { tool: 'apply_patch', sessionID: 'patch-session', callID: 'patch-call' };
writeFileSync(`${root}/probe-patch.txt`, 'before\\n');
await hooks['tool.execute.before'](input, { args });
writeFileSync(`${root}/probe-patch.txt`, 'after\\n');
await hooks['tool.execute.after']({ ...input, args });
console.log('patch-allowed');
"""
        )
        self.assertIn("patch-allowed", patch_allowed.stdout)
        self.assertEqual((self.project / "probe-patch.txt").read_text(), "after\n")

    def test_runtime_plugin_fails_closed_on_argument_drift_and_unknown_tools(self):
        self.apply()
        self.bind_fake_runtime()
        drifted = self.invoke_runtime_plugin(
            """
const root = process.argv[3];
const input = { tool: 'write', sessionID: 'drift-session', callID: 'drift-call' };
const args = { filePath: `${root}/drift.txt`, content: 'before' };
await hooks['tool.execute.before'](input, { args });
try {
  await hooks['tool.execute.after']({ ...input, args: { ...args, content: 'after' } });
  process.exitCode = 9;
} catch (error) {
  console.log(error.message);
}
try {
  await hooks['tool.execute.before'](
    { tool: 'future_mutator', sessionID: 'future-session', callID: 'future-call' },
    { args: {} },
  );
  process.exitCode = 9;
} catch (error) {
  console.log(error.message);
}
"""
        )
        self.assertIn("post_hook_failed", drifted.stdout)
        self.assertIn("unsupported_mutator", drifted.stdout)

    def test_runtime_binding_rejects_effective_third_party_plugin(self):
        self.apply()
        executable = self.root / "opencode-extra-plugin"
        plugin = self.project / ".opencode/plugins/agent-marketplace-software-engineering-team.js"
        plugins = [str(plugin), str(self.root / "third-party.js")]
        executable.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"--version\" ]; then echo 1.18.17; exit 0; fi\n"
            "if [ \"$1\" = \"debug\" ] && [ \"$2\" = \"config\" ]; then\n"
            f"  printf '%s\\n' '{json.dumps({'plugin': plugins})}'\n"
            "  exit 0\n"
            "fi\n"
            "exit 1\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)

        rejected = self.invoke(
            self.installed_manage(), "bind-runtime", "--opencode", str(executable), expected=1
        )
        self.assertEqual(rejected["code"], "unsupported_plugin_set")

    def test_tampered_owned_path_cannot_escape_uninstall(self):
        self.apply()
        private = self.project / ".opencode/agentrof/agent-marketplace"
        installation_path = private / "installation.json"
        installation = json.loads(installation_path.read_text(encoding="utf-8"))
        sentinel = self.root / "must-not-delete.txt"
        sentinel.write_text("user-owned\n", encoding="utf-8")
        installation["public_owned_files"]["../must-not-delete.txt"] = {
            "kind": "public", "sha256": "0" * 64,
        }
        installation_path.write_text(json.dumps(installation), encoding="utf-8")

        checked = self.invoke(self.installed_manage(), "check", expected=1)
        self.assertIn("unsafe_path", checked["findings"])
        rejected = self.invoke(
            self.installed_manage(), "uninstall", "--clients-stopped", expected=1
        )
        self.assertEqual(rejected["code"], "unsafe_path")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "user-owned\n")

    def test_activation_write_failure_rolls_back_all_published_files(self):
        projector = self.source / "scripts/project_opencode.py"
        spec = importlib.util.spec_from_file_location("project_opencode_failure", projector)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        original = module.atomic_write

        def fail_activation(path: Path, content: bytes) -> None:
            if path.name == "installation.json":
                raise OSError("injected activation failure")
            original(path, content)

        module.atomic_write = fail_activation
        with self.assertRaises(module.ProjectionError) as captured:
            module.apply(self.project, self.source, True, True, False, False)
        self.assertEqual(captured.exception.code, "rollback_complete")
        self.assertFalse((self.project / ".gitignore").exists())
        self.assertFalse((self.project / ".opencode/plugins/agent-marketplace-software-engineering-team.js").exists())
        private = self.project / ".opencode/agentrof/agent-marketplace"
        self.assertFalse((private / "installation.json").exists())
        self.assertFalse((private / "runtime/maintenance.json").exists())
        if (private / "packages").exists():
            self.assertEqual(list((private / "packages").glob("*")), [])

    def test_rollback_never_overwrites_a_concurrent_user_edit(self):
        projector = self.source / "scripts/project_opencode.py"
        spec = importlib.util.spec_from_file_location("project_opencode_conflict", projector)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        original = module.atomic_write
        plugin = self.project / ".opencode/plugins/agent-marketplace-software-engineering-team.js"

        def concurrent_edit(path: Path, content: bytes) -> None:
            if path.name == "installation.json":
                original(plugin, b"concurrent user edit\n")
                raise OSError("injected activation failure")
            original(path, content)

        module.atomic_write = concurrent_edit
        with self.assertRaises(module.ProjectionError) as captured:
            module.apply(self.project, self.source, True, True, False, False)
        self.assertEqual(captured.exception.code, "rollback_conflict")
        self.assertEqual(plugin.read_bytes(), b"concurrent user edit\n")
        journal = self.project / ".opencode/agentrof/agent-marketplace/runtime/maintenance.json"
        self.assertTrue(journal.is_file())


if __name__ == "__main__":
    unittest.main()
