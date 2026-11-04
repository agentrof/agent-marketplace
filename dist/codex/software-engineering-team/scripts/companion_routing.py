"""Project-local native catalog routing for an installed companion capability."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import atomic_file
import file_lock
import project_config

PACKAGE = Path(__file__).resolve().parents[1]
POLICY = Path("templates/companion-routing.json")


def policy(plugin_root: Path | None = None) -> dict:
    path = (plugin_root or PACKAGE) / POLICY
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"routes": []}


def managed(root: Path) -> bool:
    path = root / "workspace/config.json"
    if not path.is_file():
        return False
    return json.loads(path.read_text(encoding="utf-8")).get("team_id") == project_config.TEAM


def sources(host: str, plugin_root: Path | None = None) -> list[Path]:
    rule = next((row for row in policy(plugin_root)["routes"] if row["host"] == host), {})
    return [Path.home() / name for name in rule.get("source_paths", []) if (Path.home() / name).is_file()]


def source(host: str, plugin_root: Path | None = None) -> Path:
    found = sources(host, plugin_root)
    if not found:
        raise ValueError("no installed companion source for this host; provide --source or explicit unavailable evidence")
    return found[0]


def safe_target(root: Path, relative: str) -> Path:
    target = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("companion routing target must remain project-local")
    for path in (target, *target.parents):
        if path == root:
            break
        if path.is_symlink():
            raise ValueError(f"companion routing target is a symbolic link: {path}")
    return target


def render(text: str, rule: dict) -> str:
    if rule["format"] != "json":
        raise ValueError("unsupported companion routing format")
    data = json.loads(text) if text.strip() else {}
    if not isinstance(data, dict):
        raise ValueError("companion routing settings must be an object")
    current = data
    for key in rule["setting_path"][:-1]:
        current = current.setdefault(key, {})
        if not isinstance(current, dict):
            raise ValueError("companion routing conflicts with existing settings shape")
    key, value = rule["setting_path"][-1], rule["setting_value"]
    if key in current:
        if current[key] in rule.get("accepted_values", [value]):
            return text
        if current[key] not in rule.get("replace_values", []):
            raise ValueError(f"unsupported companion routing setting {key}")
    current[key] = value
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def plan(root: Path, host: str, plugin_root: Path | None = None) -> dict:
    result = {"changes": {}, "current_surfaces": {}, "target_surfaces": {}, "notes": []}
    rules = policy(plugin_root)
    rule = next((row for row in rules["routes"] if row["host"] == host), None)
    paths = sources(host, plugin_root)
    if not rule or not paths or not managed(root):
        return result
    if rule.get("catalog_control") is False:
        result["notes"].append("This host's project-local native companion suppression is unavailable; the bounded CLI route remains available.")
        return result
    target = safe_target(root, rule["target"])
    text = target.read_text(encoding="utf-8") if target.is_file() else ""
    rendered = render(text, rule)
    key = "local:" + rule["target"]
    result["current_surfaces"][key] = hashlib.sha256(text.encode()).hexdigest()
    result["target_surfaces"][key] = hashlib.sha256(rendered.encode()).hexdigest()
    if rendered != text:
        result["changes"][target] = rendered
        result["notes"].append("Start a new host session to apply project-local companion catalog routing.")
    return result


def active(root: Path, host: str, plugin_root: Path | None = None) -> bool:
    result = plan(root, host, plugin_root)
    return bool(result["target_surfaces"]) and not result["changes"]


def apply(root: Path, plugin_root: Path | None = None) -> list[str]:
    if not managed(root) or not policy(plugin_root)["routes"]:
        return []
    directory = atomic_file.real_directory(root, Path(".agentrof/agent-marketplace/.runtime"))
    lock = directory / "companion-routing.lock"
    if lock.is_symlink():
        raise ValueError("companion routing lock is a symbolic link")
    with lock.open("a+", newline="\n") as handle:
        if not file_lock.acquire(handle.fileno(), timeout=3):
            raise ValueError("companion routing is being refreshed by another owner")
        package = plugin_root or PACKAGE
        provenance = json.loads((package / ".agent-marketplace-package.json").read_text(encoding="utf-8"))
        host = provenance.get("host")
        if host not in {rule["host"] for rule in policy(plugin_root)["routes"]}:
            raise ValueError("installed package has no registered host for companion routing")
        changes = plan(root, host, plugin_root)["changes"]
        snapshots = {path: path.read_bytes() if path.is_file() else None for path in changes}
        written = []
        try:
            for path, text in changes.items():
                atomic_file.real_directory(root, path.parent.relative_to(root))
                if (path.read_bytes() if path.is_file() else None) != snapshots[path]:
                    raise ValueError("companion routing settings changed during refresh")
                atomic_file.replace_text(path, text)
                written.append(path)
        except Exception:
            for path in reversed(written):
                if path.read_text(encoding="utf-8") != changes[path]:
                    continue
                if snapshots[path] is None:
                    path.unlink()
                else:
                    atomic_file.replace_bytes(path, snapshots[path])
            raise
    return [path.relative_to(root).as_posix() for path in written]
