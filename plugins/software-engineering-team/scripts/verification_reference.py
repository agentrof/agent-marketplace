#!/usr/bin/env python3
"""Generate verification verb inputs, guards and output keys from the engine itself."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path

import delivery_verification as engine


def message(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant)
                       else "{" + ast.unparse(part.value) + "}" for part in node.values)
    return ast.unparse(node)


def catalog(source: str | None = None) -> dict:
    source = source if source is not None else Path(engine.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    parser = engine.argument_parser()
    subcommands = next(action.choices for action in parser._actions
                       if isinstance(action, argparse._SubParsersAction))
    handlers = {}
    for node in ast.walk(functions["main"]):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        comparison = node.test
        if (ast.unparse(comparison.left) != "args.command" or len(comparison.comparators) != 1
                or not isinstance(comparison.ops[0], ast.Eq)
                or not isinstance(comparison.comparators[0], ast.Constant)):
            continue
        verb = comparison.comparators[0].value
        handlers[verb] = sorted({call.func.id for statement in node.body for call in ast.walk(statement)
                                 if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                                 and call.func.id in functions})
    handlers["status"] = ["status_summary", "read_session"]
    missing = set(subcommands) - set(handlers)
    if missing:
        raise ValueError("verbs with no engine handler: " + ", ".join(sorted(missing)))
    guards = {}
    for name, function in functions.items():
        refusals = sorted({message(node.exc.args[0]) for node in ast.walk(function)
                           if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                           and node.exc.args})
        returned = {node.value.id for node in ast.walk(function)
                    if isinstance(node, ast.Return) and isinstance(node.value, ast.Name)}
        values = [node for node in ast.walk(function) if isinstance(node, ast.Return)]
        values.extend(node.value for node in ast.walk(function) if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id in returned for target in node.targets))
        outputs = sorted({key.value for node in values for mapping in ast.walk(node) if isinstance(mapping, ast.Dict)
                          for key in mapping.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)})
        calls = sorted({call.func.id for call in ast.walk(function)
                        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                        and call.func.id in functions and call.func.id not in {"main", "argument_parser"}})
        guards[name] = {"summary": (ast.get_docstring(function) or "").split("\n\n", 1)[0],
                        "refusals": refusals, "output_keys": outputs, "calls": calls}
    commands = {}
    for verb, command in sorted(subcommands.items()):
        arguments = []
        for action in command._actions:
            if isinstance(action, argparse._HelpAction):
                continue
            argument = {"flags": action.option_strings, "required": action.required}
            if action.choices is not None:
                argument["choices"] = list(action.choices)
            if action.help and action.help != argparse.SUPPRESS:
                argument["help"] = action.help
            arguments.append(argument)
        commands[verb] = {"arguments": arguments, "handlers": handlers[verb]}
    return {"schema_version": 1, "source_hash": "sha256:" + hashlib.sha256(source.encode()).hexdigest(),
            "commands": commands, "functions": guards}


def reference(data: dict, verb: str) -> dict:
    if verb not in data["commands"]:
        raise ValueError("unknown verification verb: " + verb)
    pending = list(data["commands"][verb]["handlers"])
    found = {}
    while pending:
        name = pending.pop()
        if name in found:
            continue
        found[name] = data["functions"][name]
        pending.extend(found[name]["calls"])
    return {"verb": verb, "source_hash": data["source_hash"], **data["commands"][verb],
            "guards_and_outputs": {name: row for name, row in sorted(found.items())
                                   if row["refusals"] or row["output_keys"] or name in data["commands"][verb]["handlers"]},
            "limits": "Static local call graph. Conditional refusals are possibilities, not unconditional requirements; imported guards and dynamic dispatch require the owning contract."}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verb", help="return one verb and its local guard closure; omit for the command index")
    args = parser.parse_args(argv)
    try:
        data = catalog()
        result = reference(data, args.verb) if args.verb else {
            "source_hash": data["source_hash"], "commands": data["commands"]}
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
