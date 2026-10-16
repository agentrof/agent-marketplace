"""Read-only wiring clues for a frozen Python change, with explicit unassessed paths."""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess


def is_test(path: str) -> bool:
    return (any(part in {"test", "tests", "testing"} for part in Path(path).parts)
            or Path(path).name.startswith("test_") or path.endswith("_test.py"))


def facts(sources: dict[str, str]) -> dict:
    result = {"functions": {}, "calls": {}, "reads": {}, "writes": {},
              "commands": {}, "dispatch": set(), "unassessed": []}
    for path, source in sorted(sources.items()):
        if not path.endswith(".py") or is_test(path):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            result["unassessed"].append(path)
            continue
        names = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                call = node.value
                if isinstance(call.func, ast.Name) and call.func.id == "Path" and call.args and isinstance(call.args[0], ast.Constant):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and isinstance(call.args[0].value, str):
                            names[target.id] = (call.args[0].value if target.id not in names
                                                or names[target.id] == call.args[0].value else None)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                result["functions"][(path, node.name)] = node.lineno
            if isinstance(node, ast.Compare) and (
                    isinstance(node.left, ast.Attribute) and node.left.attr in {"command", "func", "verb"}
                    or isinstance(node.left, ast.Name) and node.left.id in {"command", "verb"}):
                result["dispatch"].update(value.value for value in node.comparators
                                          if isinstance(value, ast.Constant) and isinstance(value.value, str))
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
            if name:
                result["calls"].setdefault(name, []).append({"path": path, "line": node.lineno})
            if name == "add_parser" and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                result["commands"][(path, node.args[0].value)] = node.lineno
            record = None
            if name == "open" and node.args and isinstance(node.args[0], ast.Constant):
                record = node.args[0].value
                mode_node = (node.args[1] if len(node.args) > 1 else
                             next((item.value for item in node.keywords if item.arg == "mode"), ast.Constant("r")))
                mode = mode_node.value if isinstance(mode_node, ast.Constant) else None
                writing = isinstance(mode, str) and any(flag in mode for flag in "wax+")
            elif name in {"read_text", "read_bytes", "write_text", "write_bytes"} and isinstance(node.func, ast.Attribute):
                receiver = node.func.value
                if isinstance(receiver, ast.Name):
                    record = names.get(receiver.id)
                elif isinstance(receiver, ast.Call) and isinstance(receiver.func, ast.Name) and receiver.func.id == "Path" and receiver.args and isinstance(receiver.args[0], ast.Constant):
                    record = receiver.args[0].value
                writing = name.startswith("write_")
            else:
                continue
            if isinstance(record, str):
                if writing:
                    result["writes"].setdefault(record, []).append({"path": path, "line": node.lineno})
                else:
                    result["reads"][(path, record)] = node.lineno
            else:
                result["unassessed"].append(path)
    return result


def inspect_sources(before: dict[str, str], after: dict[str, str]) -> dict:
    old, new = facts(before), facts(after)
    findings, symbols, records = [], [], []
    for (path, name), line in sorted(new["functions"].items()):
        if (path, name) in old["functions"]:
            continue
        symbols.append({"path": path, "line": line, "symbol": name,
                        "non_test_callers": new["calls"].get(name, [])})
        if not new["calls"].get(name):
            findings.append({"kind": "no_production_caller", "path": path, "line": line,
                             "symbol": name, "non_test_callers": []})
    for (path, record), line in sorted(new["reads"].items()):
        if (path, record) in old["reads"]:
            continue
        records.append({"path": path, "line": line, "record": record,
                        "writers": new["writes"].get(record, [])})
        if record not in new["writes"]:
            findings.append({"kind": "no_record_writer", "path": path, "line": line, "record": record})
    for (path, command), line in sorted(new["commands"].items()):
        if (path, command) not in old["commands"] and command not in new["dispatch"]:
            findings.append({"kind": "check_command_dispatch", "path": path, "line": line, "command": command})
    changed = sorted(path for path, source in after.items() if source != before.get(path))
    return {"findings": findings, "new_symbols": symbols, "new_record_reads": records,
            "unassessed": sorted(set(new["unassessed"]) | {path for path in changed if not path.endswith(".py") and not is_test(path)}),
            "limits": "Python literal call and file-I/O clues only, not a reachability proof. Check imports, callbacks, dynamic paths, external producers and every unassessed path against a production entry point."}


def inspect(root: Path, base: str, head: str) -> dict:
    def git(*args):
        run = subprocess.run(["git", "--no-replace-objects", *args], cwd=root, capture_output=True, encoding="utf-8")
        if run.returncode:
            raise RuntimeError(run.stderr.strip() or "cannot read frozen wiring inputs")
        return run.stdout
    def sources(commit):
        paths = git("ls-tree", "-r", "--name-only", "-z", commit).split("\0")
        return {path: git("show", commit + ":" + path) for path in paths
                if path.endswith(".py") and not is_test(path)}
    result = inspect_sources(sources(base), sources(head))
    changed = git("diff", "--name-only", "--no-renames", "-z", base, head).split("\0")
    result["unassessed"] = sorted(set(result["unassessed"]) | {
        path for path in changed if path and not path.endswith(".py") and not is_test(path)
        and not path.startswith("workspace/docs/")})
    return result
