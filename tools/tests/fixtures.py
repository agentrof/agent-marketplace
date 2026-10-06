"""Small single-team repository fixtures for release tests."""

from __future__ import annotations

import json
import shlex
import shutil
import sys
from pathlib import Path
from typing import Mapping

import build_distributions


REAL_REPOSITORY = Path(__file__).resolve().parents[2]
PLUGIN = "software-engineering-team"
REFRESH_N_VERSION = "0.0.1"
REFRESH_NEXT_VERSION = "0.0.2"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def copy(relative: str, root: Path) -> None:
    source = REAL_REPOSITORY / relative
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(
            source,
            target,
            ignore=build_distributions.ignore_python_cache,
        )
    else:
        shutil.copy2(source, target)


def make_valid_root(
    root: Path, version: str = "0.0.1", *, build: bool = True
) -> None:
    copy("product.json", root)
    copy("package-modes.json", root)
    copy("tools/data/limits.json", root)
    copy("tools/data/models.json", root)
    copy("tools/data/host-cli-versions.json", root)
    copy("AGENTS.md", root)
    copy("CLAUDE.md", root)
    copy(".gitattributes", root)
    copy(f"plugins/{PLUGIN}", root)
    # The fixture must exercise the same dynamic adapter registry as production.
    # Copying only individual legacy host trees would make product.json and the
    # discovered platform registry disagree as soon as a new host is added.
    copy("platforms", root)

    for host, adapter in build_distributions.load_adapters(root).items():
        if adapter.metadata["artifact_kind"] != "native_marketplace":
            continue
        manifest_path = root / "platforms" / host / PLUGIN / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["version"] = version
        write(manifest_path, json.dumps(manifest, indent=2) + "\n")

    write(root / "versions.json", json.dumps({
        "schema_version": 1,
        "marketplace": version,
        "plugins": {PLUGIN: version},
    }, indent=2) + "\n")
    write(root / ".claude-plugin" / "marketplace.json", json.dumps({
        "name": "agent-marketplace",
        "owner": {"name": "Agentrof"},
        "metadata": {"description": "fixture", "version": version},
        "plugins": [{
            "name": PLUGIN,
            "source": f"./dist/claude/{PLUGIN}",
            "description": "fixture",
            "version": version,
            "license": "Apache-2.0",
        }],
    }, indent=2) + "\n")
    write(root / ".agents" / "plugins" / "marketplace.json", json.dumps({
        "name": "agent-marketplace",
        "interface": {"displayName": "Agent Marketplace"},
        "plugins": [{
            "name": PLUGIN,
            "source": {"source": "local", "path": f"./dist/codex/{PLUGIN}"},
            "policy": {"installation": "INSTALLED_BY_DEFAULT", "authentication": "ON_INSTALL"},
            "category": "Engineering",
        }],
    }, indent=2) + "\n")
    write(root / ".changes" / "fixture.json", json.dumps({
        "summary": "Fixture baseline.",
        "components": {},
    }, indent=2) + "\n")
    write(root / "CHANGELOG.md", "# Changelog\n")
    if build:
        build_distributions.replace_generated(root, root / "dist")


def make_refresh_pair(n_root: Path, next_root: Path) -> None:
    """Build deterministic packages with one real N to N+1 contract delta."""
    make_valid_root(n_root, REFRESH_N_VERSION, build=False)
    plugin = n_root / "plugins" / PLUGIN

    write(
        plugin / "templates/vault/.obsidian/snippets/brand.css",
        "/* N-only brand payload */\n:root { --agentrof-accent: #000001; }\n",
    )

    projected_plugin = (
        plugin / "templates" / "vault" / ".obsidian" / "plugins"
        / "obsidian-front-matter-title-plugin"
    )
    license_path = projected_plugin / "LICENSE"
    license_path.unlink()
    license_path.mkdir()
    write(license_path / "retired-license-fragment.txt", "N-only directory\n")
    write(projected_plugin / "retired-after-n.js", "N-only package asset\n")

    write(plugin / "agents" / "refresh-retired-probe.md", (
        "---\n"
        "name: refresh-retired-probe\n"
        "description: N-only agent used to prove package refresh cleanup.\n"
        "reasoning: low\n"
        "output_contract: prose\n"
        "---\n\n"
        "# Refresh Retired Probe\n\n"
        "## Principles\n- Preserve the fixture boundary.\n\n"
        "## Boundaries\n- Does only the N refresh probe.\n\n"
        "## Approach\n1. Return the probe result.\n\n"
        "## Output Contract\n- Probe result.\n"
    ))
    team_instructions = plugin / "templates" / "project-instructions" / "team.md"
    write(
        team_instructions,
        team_instructions.read_text(encoding="utf-8")
        + "\nN-only managed project instruction.\n",
    )
    build_distributions.replace_generated(n_root, n_root / "dist")

    make_valid_root(next_root, REFRESH_NEXT_VERSION)


def install_fixture_package(root: Path, host: str, install_root: Path) -> Path:
    """Replace one fixture install from its generated host distribution."""
    if host not in build_distributions.HOSTS:
        raise ValueError(f"unsupported fixture host: {host}")
    source = root / "dist" / host / PLUGIN
    provenance = json.loads(
        (source / build_distributions.PROVENANCE).read_text(encoding="utf-8")
    )
    if provenance.get("component") != PLUGIN or provenance.get("host") != host:
        raise ValueError("fixture distribution provenance does not match install")
    target = install_root / PLUGIN
    if target.exists():
        marker, _ = build_distributions.packaging_names(root)
        if not (target / marker).is_file():
            raise ValueError("refusing to replace an unmanaged fixture install")
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    return target


def validator_findings(root: Path, *checks: str) -> list:
    """The findings of the named validator checks on ``root``, in a full run's order.

    A mutation test reads the check its mutation targets; a full run adds the
    cost of every other check and proves nothing more about that one.
    """
    import validate

    tree = validate.build_tree(root)
    found: list = []
    for name in checks:
        validate.CHECKS[name](tree, found)
    return sorted(found, key=lambda finding: (finding.path, finding.line, finding.check,
                                              finding.message))


# A fake host binary: a sh wrapper around this program, which logs its
# arguments and stdin and answers from the JSON file beside it. A case with
# `reply` answers Claude Code's `initialize` request with its request id.
FAKE_HOST = r'''
import json, os, subprocess, sys, time
from pathlib import Path
config = json.loads(Path(__file__).with_suffix(".json").read_text(encoding="utf-8"))
args = sys.argv[1:]
stdin = sys.stdin.read()
with open(config["log"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"argv": args, "stdin": stdin}) + "\n")
case = config["cases"].get(" ".join(args), config["cases"].get("*", {}))
if case.get("pid_file"):
    Path(case["pid_file"]).write_text(str(os.getpid()), encoding="utf-8")
if case.get("orphan_file"):
    orphan = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    Path(case["orphan_file"]).write_text(str(orphan.pid), encoding="utf-8")
time.sleep(case.get("sleep", 0))
out = case.get("stdout", "")
if "reply" in case:
    try:
        request_id = json.loads(stdin.splitlines()[0])["request_id"]
    except (ValueError, IndexError, KeyError, TypeError):
        request_id = None
    response = {"subtype": case.get("subtype", "success"),
                "request_id": case.get("request_id", request_id), "response": case["reply"]}
    out += json.dumps({"type": "control_response", "response": response}) + "\n"
sys.stdout.write(out)
sys.stdout.flush()
sys.exit(case.get("exit", 0))
'''


class FakeHost:
    """One fake host executable named ``name`` in ``directory``."""

    def __init__(self, directory: Path, name: str) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / name
        self.program = directory / f"{name}-fake.py"
        self.log = directory / f"{name}-calls.jsonl"
        self.cases: dict = {}
        self.program.write_text(FAKE_HOST, encoding="utf-8")
        self.path.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " "
                             + shlex.quote(str(self.program)) + ' "$@"\n', encoding="utf-8")
        self.path.chmod(0o755)
        self.save()

    def answer(self, args: str, **case) -> None:
        self.cases[args] = case
        self.save()

    def save(self) -> None:
        self.program.with_suffix(".json").write_text(
            json.dumps({"log": str(self.log), "cases": self.cases}), encoding="utf-8")

    def calls(self) -> list:
        if not self.log.is_file():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]


# No Codex release has this version, so the project generators' model check
# skips any real codex process above the tests and any CLI an installed app
# bundles; only a fake that prints it is a candidate.
ISOLATED_CODEX_VERSION = "0.0.0-isolated"
ISOLATED_FAKES = {
    "claude": "9.9.9 (Claude Code)",
    "codex": f"codex-cli {ISOLATED_CODEX_VERSION}",
}


def isolated_hosts(env: Mapping[str, str], directory: Path) -> dict:
    """``env`` in which the project generators' model check reaches no real host.

    CLAUDE_CODE_EXECPATH and CODEX_CLI_PATH name fakes that print a version
    and list no model, CLAUDE_PID goes, CODEX_VERSION names no release and
    CODEX_HOME an empty home, so every pinned model stays unverified and keeps
    its pin, whatever runs the tests.
    """
    directory.mkdir(parents=True, exist_ok=True)
    isolated = {key: value for key, value in env.items() if key != "CLAUDE_PID"}
    for name, version in ISOLATED_FAKES.items():
        fake = directory / name
        fake.write_text(
            "#!/bin/sh\n"
            f"if [ \"$1\" = --version ]; then echo {shlex.quote(version)}; exit 0; fi\n"
            "cat >/dev/null\n"
            "exit 1\n", encoding="utf-8")
        fake.chmod(0o755)
    isolated.update({
        "CLAUDE_CODE_EXECPATH": str(directory / "claude"),
        "CODEX_CLI_PATH": str(directory / "codex"),
        "CODEX_VERSION": ISOLATED_CODEX_VERSION,
        "CODEX_HOME": str(directory / "codex-home"),
    })
    return isolated
