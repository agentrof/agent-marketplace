"""Candidate-bound native review with bounded, inspectable source delivery."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import atomic_file
import companion_routing
import file_lock
import graph_evidence
import model_class
import project_config
import role_settings

POLICY_FILE = Path(__file__).resolve().parents[1] / "skill-content/review/data/review-policy.json"

def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def encoded(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def policy(lane) -> dict:
    return json.loads(POLICY_FILE.read_text(encoding="utf-8"))


def source_excerpt(source: Path, companion: str | None, lane) -> dict:
    """Keep whole selected sections and their original line ranges, never a byte prefix."""
    raw = source.read_bytes()
    text = raw.decode("utf-8")
    lines = text.splitlines(keepends=True)
    settings = policy(lane)
    complete = companion is None or len(raw) <= settings["source_max_bytes"]
    if complete:
        ranges = [(1, len(lines))]
    else:
        name = lane.parse_frontmatter(text)[0].get("name")
        companion_policy = next((row for row in settings["companions"] if row["id"] == companion), {})
        selectors = next((row["sections"] for row in companion_policy.get("sources", []) if row["id"] == name), None)
        if not selectors:
            raise lane.Refused(f"no scoped procedure selectors for {companion} source {name!r}")
        headings, fence = [], None
        for index, line in enumerate(lines):
            marker = re.match(r"^\s*(`{3,}|~{3,})", line)
            if marker:
                token = marker.group(1)[0]
                fence = None if fence == token else token if fence is None else fence
            if fence is None and (match := re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)):
                headings.append((index, len(match.group(1)), match.group(2)))
        ranges = []
        for selector in selectors:
            matches = [(index, level) for index, level, title in headings if title == selector]
            if len(matches) != 1:
                raise lane.Refused(f"scoped procedure heading {selector!r} is missing or ambiguous in {source}")
            start, level = matches[0]
            end = next((index for index, depth, _ in headings if index > start and depth <= level), len(lines))
            ranges.append((start + 1, end))
    content = "\n".join("".join(lines[start - 1:end]) for start, end in ranges)
    if not content.strip() or len(content.encode("utf-8")) > settings["source_max_bytes"]:
        raise lane.Refused(f"scoped procedure exceeds its source budget or is empty: {source}")
    return {"path": str(source.resolve()), "sha256": digest(raw), "line_ranges": ranges,
            "delivery": "complete_source" if complete else "scoped_sections", "content": content}


def graph_path(root: Path, value: str | None, lane) -> Path:
    candidates = [Path(value).expanduser()] if value else [
        Path(os.environ.get("GRAPHIFY_OUT", ".agentrof/graphify")) / "graph.json",
        Path("graphify-out/graph.json")]
    for candidate in candidates:
        path = candidate if candidate.is_absolute() else root / candidate
        if path.is_file():
            return path.resolve()
    raise lane.Refused("no existing Graphify graph; record the unavailable capability and core fallback evidence")


def run_graph(root: Path, task: str, question: str, graph: str | None, lane, *, seed=None, context=None) -> dict:
    settings = policy(lane)
    head = lane.run("git", "rev-parse", "HEAD", cwd=root)
    record = graph_evidence.run_query(root, graph=graph_path(root, graph, lane), question=question,
        output_dir=root / lane.REVIEWS / "graph", budget=settings["graph_budget"], seed=seed, context=context,
        max_output_bytes=settings["graph_output_max_bytes"])
    record["head"] = head
    if lane.run("git", "rev-parse", "HEAD", cwd=root) != head:
        record.update(ok=False, inputs_changed=True, reason="candidate changed during Graphify query")
    path = lane.methods_path(root, task)
    atomic_file.real_directory(root, path.parent.relative_to(root))
    with path.with_suffix(".lock").open("a+", newline="\n") as handle:
        if not file_lock.acquire(handle.fileno(), timeout=3):
            raise lane.Refused("companion evidence is being updated")
        records = lane.read_methods(root, task)
        if "companion/graphify" not in records:
            raise lane.Refused("load the scoped Graphify source before its query")
        records["companion/graphify"]["action"] = record
        atomic_file.replace_text(path, json.dumps(records, indent=2) + "\n")
    return record


def selection(args, root: Path, lane) -> tuple[Path, list[dict]]:
    if getattr(args, "batch", None) is None:
        return root, [{"task": args.task, "root": str(root)}]
    import proof_train
    state = proof_train.State(proof_train.home(root, lane), read_only=True)
    batch = state.batch(args.batch, lane)
    if batch["status"] != proof_train.GREEN or batch.get("base") != args.base:
        raise lane.Refused("native aggregate review requires the proved batch and its target branch")
    tree = proof_train.home(root, lane) / f"wt-{batch['id']}"
    if lane.head_state(tree) != (batch["head"], True):
        raise lane.Refused("the integration candidate changed after proof")
    return tree, [{"task": member["task"], "root": member["root"], "head": member["head"]}
                  for member in batch["members"]]


def selected_methods(records: dict, lane) -> set[str]:
    """Preserve explicit method choice across package refresh, then load current content."""
    selected = set()
    for name, record in records.items():
        if not isinstance(record, dict) or not re.fullmatch(r"[a-z]+(?:-[a-z]+)*", name) \
                or record.get("load_source") != "method_tool":
            continue
        source = lane.SKILLS / name / "SKILL.md"
        if source.is_file() and lane.parse_frontmatter(source.read_text(encoding="utf-8"))[0].get("exposure") == "internal":
            selected.add(name)
    return selected


def prepare(root: Path, members: list[dict], base: str, lane, *, diff_path: Path | None = None) -> dict:
    head, clean = lane.head_state(root)
    if not clean or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
        raise lane.Refused("native review requires a clean committed candidate")
    base_head = lane.run("git", "merge-base", f"origin/{base}", head, cwd=root)
    files = set(lane.run("git", "diff", "--name-only", f"{base_head}...{head}", cwd=root).splitlines())
    diff = lane.run("git", "diff", f"{base_head}...{head}", cwd=root, strip_output=False)
    sources, tasks, evidence, graphs = {}, [], [], []
    critical = False

    def include(name, source, task):
        key = f"{name}/{source['sha256'][:12]}"
        entry = sources.setdefault(key, {"source_id": key, "tasks": [], **source})
        if task not in entry["tasks"]:
            entry["tasks"].append(task)

    for member in sorted(members, key=lambda item: item["task"]):
        task, owner = member["task"], Path(member["root"])
        path = lane.task_path(root, task)
        props, body = lane.task_props(path), lane.task_body(path)
        records = lane.read_methods(owner, task)
        member_files = files
        if member.get("head"):
            member_files = set(lane.run("git", "diff", "--name-only",
                                       f"{base_head}...{member['head']}", cwd=root).splitlines())
        methods = sorted(set(lane.required_methods(root, props, member_files, body)) | selected_methods(records, lane))
        critical |= props.get("rigor") == "high" or bool(lane.field_items(props, "risks"))
        tasks.append({"task": task, "path": str(path.relative_to(root)), "sha256": digest(path.read_bytes()),
                      "title": props.get("title", ""), "contract": body, "risks": lane.field_items(props, "risks")})
        for name in sorted(set(methods) | {"review"}):
            include(f"method/{name}", source_excerpt(lane.SKILLS / name / "SKILL.md", None, lane), task)
        for row in lane.companion_evidence(owner, task, methods, props, member_files, head=member.get("head", head)):
            name = row["companion"]
            record = records.get(f"companion/{name}", {})
            if not row["ok"]:
                raise lane.Refused(f"{task} {name} needs current application or explicit core fallback evidence")
            proof = owner / record["evidence"]
            evidence.append({"task": task, "path": str(proof.resolve()), "sha256": digest(proof.read_bytes())})
            if record.get("unavailable_reason"):
                content = f"Capability unavailable: {record['unavailable_reason']}\nCore fallback evidence: {proof.resolve()}"
                include(f"fallback/{name}", {"path": str(proof.resolve()), "sha256": digest(proof.read_bytes()),
                    "line_ranges": [], "delivery": "reported_unavailable", "content": content}, task)
            else:
                include(f"companion/{name}", source_excerpt(Path(record["source_path"]), name, lane), task)
                if name == "graphify":
                    action = record.get("action", {})
                    if action.get("head") != member.get("head", head) or not graph_evidence.validate(action):
                        raise lane.Refused(f"{task} requires a successful current Graphify query")
                    graphs.append({"task": task, "action": action})
    proof_contract = {"commands": lane.proof_plan(root, base, files=files)["commands"],
                      "receipts": [{"task": member["task"],
                                    "path": str(lane.proof_path(Path(member["root"]), member["task"]))}
                                   for member in members],
                      "validation": "Proof completion is checked separately against this candidate at merge."}
    binding = {"head": head, "base": base, "base_head": base_head, "members": members, "tasks": tasks,
               "sources": list(sources.values()), "evidence": evidence, "graphs": graphs,
               "diff_sha256": digest(diff.encode("utf-8")), "policy_sha256": digest(encoded(policy(lane)))}
    binding["proof_contract"] = proof_contract
    runtime_inputs = [Path(__file__), Path(__file__).with_name("review_host.py"),
                      root / "workspace/config.json", role_settings.CLASS_MAP]
    binding["runtime_inputs"] = {str(path): digest(path.read_bytes()) if path.is_file() else None
                                 for path in runtime_inputs}
    diff_path = diff_path or root / lane.REVIEWS / f"{head}.diff"
    packet = ("Perform pre-merge review. Proof completion gates merge separately, and readiness follows merge. "
              "Review this immutable candidate read-only. Apply every supplied source to its listed tasks. "
              "Read the full diff and affected current files. Report each source/task assessment with actual "
              "current project-relative file and line citations with an exact short quote from that line. "
              "Before returning the report, check every proposed path, line and quote against the current file. "
              "Copy literal contiguous text from that one line; paraphrases and incorrect line numbers reject the report. "
              "Citations must reference tracked source, test or contract files. Describe runtime proof in assessment text. "
              "Report findings and limitations honestly. "
              "Source delivery is observed; application is your report. Do not edit or launch other agents.\n"
              f"Candidate {head}; base {base_head}; project {root}; full diff {diff_path}.\n")
    packet += json.dumps({"tasks": tasks, "procedures": list(sources.values()), "evidence": evidence,
                          "proof_contract": proof_contract,
                          "graph_results": [{"task": item["task"], "text": item["action"]["text"],
                                             "stdout_path": item["action"]["stdout_path"]} for item in graphs]},
                         ensure_ascii=False, indent=2)
    if len(packet.encode("utf-8")) > policy(lane)["packet_max_bytes"]:
        raise lane.Refused("review procedure packet exceeds its byte budget; split the candidate scope")
    return {"binding": binding, "binding_sha256": digest(encoded(binding)), "packet": packet,
            "packet_sha256": digest(packet.encode("utf-8")), "diff": diff, "diff_path": diff_path,
            "profile": "critical" if critical else "review"}


def schema(prepared: dict) -> dict:
    def obj(properties):
        return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
    string = {"type": "string"}
    binding = prepared["binding"]
    source_id = {"type": "string", "enum": [row["source_id"] for row in binding["sources"]]}
    task_id = {"type": "string", "enum": [row["task"] for row in binding["tasks"]]}
    citation = obj({"path": {**string, "description": "Tracked source, test or contract file relative to the project; runtime proof belongs in assessment text."}, "line": {"type": "integer", "minimum": 1}, "quote": string})
    check = obj({"source_id": source_id, "tasks": {"type": "array", "items": task_id}, "assessment": string,
                 "citations": {"type": "array", "items": citation}})
    finding = obj({"severity": {"type": "string", "enum": ["critical", "major", "minor"]},
                   "path": string, "line": {"type": "integer", "minimum": 1}, "quote": string, "detail": string})
    return obj({"head": {"type": "string", "enum": [binding["head"]]}, "verdict": {"type": "string", "enum": ["approve", "fix_required"]},
                "checks": {"type": "array", "items": check}, "findings": {"type": "array", "items": finding},
                "limitations": {"type": "array", "items": string}})


CITATION_DRIFT = 3


def validate_report(report: dict, prepared: dict, root: Path, lane) -> None:
    binding = prepared["binding"]
    if not isinstance(report, dict) or set(report) != {"head", "verdict", "checks", "findings", "limitations"} \
            or report.get("head") != binding["head"]:
        raise lane.Refused("native review report has no matching candidate")
    if any(not isinstance(report[key], list) for key in ("checks", "findings", "limitations")):
        raise lane.Refused("native review report lists are malformed")
    expected = {(source["source_id"], task) for source in binding["sources"] for task in source["tasks"]}
    covered = set()
    tracked = set(lane.run("git", "ls-tree", "-r", "--name-only", binding["head"], cwd=root).splitlines())

    def citation(item):
        if not isinstance(item, dict):
            raise lane.Refused("review citation is malformed")
        path, line, quote = item.get("path"), item.get("line"), item.get("quote")
        if not isinstance(path, str) or path not in tracked or type(line) is not int or line < 1:
            raise lane.Refused("review citation is not a current tracked file and line")
        target = (root / path).resolve()
        if not target.is_relative_to(root.resolve()):
            raise lane.Refused("review citation is outside the current candidate")
        lines = target.read_text(encoding="utf-8").splitlines()
        if line > len(lines) or not isinstance(quote, str) or not 1 <= len(quote.strip()) <= 240 or "\n" in quote:
            raise lane.Refused("review citation quote does not match its current file line")
        # Reviewers often miscount by a line or two; the nearest exact match within the window is the cited line.
        near = sorted((n for n in range(max(1, line - CITATION_DRIFT), min(len(lines), line + CITATION_DRIFT) + 1)
                       if quote in lines[n - 1]), key=lambda n: abs(n - line))
        if not near:
            raise lane.Refused("review citation quote does not match its current file line")
        item["line"] = near[0]

    for check in report.get("checks", []):
        if not isinstance(check, dict) or not isinstance(check.get("assessment"), str) \
                or not check["assessment"].strip() or not isinstance(check.get("citations"), list) \
                or not check["citations"] or not isinstance(check.get("tasks"), list) or not check["tasks"]:
            raise lane.Refused("each review procedure needs an assessment, tasks and current citations")
        for task in check["tasks"]:
            if not isinstance(task, str) or not isinstance(check.get("source_id"), str):
                raise lane.Refused("review procedure identity is malformed")
            pair = (check.get("source_id"), task)
            if pair not in expected or pair in covered:
                raise lane.Refused("review procedure coverage is unknown or duplicated")
            covered.add(pair)
        for item in check["citations"]:
            citation(item)
    if covered != expected:
        raise lane.Refused("native reviewer omitted required procedure/task coverage")
    for finding in report.get("findings", []):
        citation(finding)
        if finding.get("severity") not in ("critical", "major", "minor") \
                or not isinstance(finding.get("detail"), str) or not finding["detail"].strip():
            raise lane.Refused("native review finding is incomplete")
    if report.get("verdict") not in ("approve", "fix_required") or any(not isinstance(item, str) for item in report["limitations"]):
        raise lane.Refused("native review verdict or limitations are missing")
    if report["verdict"] == "approve" and any(item["severity"] in ("critical", "major") for item in report.get("findings", [])):
        raise lane.Refused("blocking findings cannot accompany approval")


def adapter():
    path = Path(__file__).with_name("review_host.py")
    spec = importlib.util.spec_from_file_location("review_host", path)
    if spec is None or spec.loader is None or not path.is_file():
        raise ValueError("native review needs the installed host adapter")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record_path(root: Path, head: str, lane) -> Path:
    return root / lane.REVIEWS / f"{head}.native-review.json"


def execute(args, root: Path, lane) -> dict:
    root, members = selection(args, root, lane)
    companion_routing.apply(root)
    directory = root / lane.REVIEWS
    atomic_file.real_directory(root, directory.relative_to(root))
    with (directory / "native-review.lock").open("a+", newline="\n") as handle:
        if not file_lock.acquire(handle.fileno(), timeout=0.1):
            raise lane.Refused("a native review already owns this candidate workspace")
        if (directory / "cleanup-unconfirmed.json").exists():
            raise lane.Refused("native review process cleanup is unconfirmed; reconcile its recorded process before retry")
        return run_review(args, root, members, lane)


def process_group_snapshot(pid: int) -> dict:
    """Retain bounded member metadata without arguments or environment values."""
    try:
        result = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,pgid=,stat=,lstart=,comm="],
                                capture_output=True, text=True, timeout=1, check=False)
        if result.returncode:
            return {"pgid": pid, "observation": "ps_failed"}
        members = []
        for line in result.stdout.splitlines():
            fields = line.split(None, 9)
            if len(fields) == 10 and fields[2] == str(pid):
                members.append({"pid": int(fields[0]), "ppid": int(fields[1]), "state": fields[3],
                                "started_at": " ".join(fields[4:9]), "name": fields[9][:256]})
        return {"pgid": pid, "members": members[:32], "truncated": len(members) > 32}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"pgid": pid, "observation": "unavailable"}


def stop_review(process, directory: Path, lane) -> None:
    try:
        lane.stop_proof(process)
    except BaseException:
        atomic_file.replace_text(directory / "cleanup-unconfirmed.json",
                                 json.dumps({"pid": process.pid, "at": lane.now()}) + "\n")
        raise


def run_review(args, root: Path, members: list[dict], lane) -> dict:
    head = lane.head_state(root)[0]
    for member in members:
        owner, task = Path(member["root"]), member["task"]
        record = lane.read_methods(owner, task).get("companion/graphify", {})
        if record.get("source_path") and not record.get("unavailable_reason"):
            action = record.get("action", {})
            if action.get("head") != member.get("head", head) or not graph_evidence.validate(action):
                files = sorted(name for name in lane.changed_files(owner, args.base) if not name.endswith(".md"))
                question = action.get("question") if action.get("ok") else None
                retained_query = isinstance(question, str) and bool(question.strip())
                run_graph(owner, task, question if retained_query else "Affected callers and dependencies",
                          action.get("graph_path"), lane,
                          seed=action.get("seed") if retained_query else (files[0] if files else None),
                          context=action.get("context") if retained_query else None)
    destination = record_path(root, head, lane)
    atomic_file.real_directory(root, destination.parent.relative_to(root))
    attempt = Path(tempfile.mkdtemp(prefix=f"{head[:12]}-", dir=destination.parent))
    prepared = prepare(root, members, args.base, lane, diff_path=attempt / "candidate.diff")
    if prepared["binding"]["head"] != head:
        raise lane.Refused("candidate changed while review inputs were prepared")
    settings = model_class.resolve(role_settings.load_class_map(),
        project_config.load(root / "workspace/config.json"), args.host, prepared["profile"], access="read")
    host = adapter()
    if getattr(host, "HOST", args.host) != args.host:
        raise lane.Refused("requested review host differs from this installed host adapter")
    packet_path, schema_path = attempt / "packet.txt", attempt / "schema.json"
    packet_path.write_bytes(prepared["packet"].encode("utf-8"))
    schema_path.write_bytes(encoded(schema(prepared)))
    prepared["diff_path"].write_bytes(prepared["diff"].encode("utf-8"))
    record = {"status": "pending", "host": args.host, "head": head, "binding_sha256": prepared["binding_sha256"],
              "packet_sha256": prepared["packet_sha256"], "packet_path": str(packet_path),
              "diff_path": str(prepared["diff_path"]), "diff_sha256": prepared["binding"]["diff_sha256"],
              "source_delivery": "prepared", "application_source": "reviewer_report", "requested": settings,
              "started_at": lane.now()}
    atomic_file.replace_text(destination, json.dumps(record, indent=2) + "\n")
    stdout, stderr = attempt / "stdout.jsonl", attempt / "stderr.log"
    try:
        command = host.command(root, settings, schema_path, attempt)
        environment = host.environment() if hasattr(host, "environment") else os.environ.copy()
        record.update(command=command, filtered_environment_keys=sorted(set(os.environ) - set(environment)))
        with packet_path.open("rb") as prompt, stdout.open("wb") as out, stderr.open("wb") as err:
            process = subprocess.Popen(command, cwd=root, stdin=prompt, stdout=out, stderr=err,
                                       start_new_session=os.name == "posix", env=environment)
            record["source_delivery"] = "submitted_to_host"
            try:
                code = process.wait(timeout=policy(lane)["timeout_seconds"])
            except BaseException as error:
                try:
                    stop_review(process, destination.parent, lane)
                except BaseException as cleanup_error:
                    raise error from cleanup_error
                raise
        if os.name == "posix":
            observation_error = None
            try:
                gone = lane.wait_process_group(process.pid, 1)
            except OSError as error:
                gone, observation_error = False, error
            if not gone:
                record["process_cleanup"] = process_group_snapshot(process.pid)
                stop_review(process, destination.parent, lane)
                if observation_error is not None:
                    raise observation_error
                raise lane.Refused("native review left child processes running; its process group was stopped")
        if code:
            raise lane.Refused("native review failed. Inspect captured stderr. If the outer sandbox blocked host transport, "
                               "use the host's normal per-command permission flow for this review command, preserving child read-only access")
        if stdout.stat().st_size > policy(lane)["host_output_max_bytes"]:
            raise lane.Refused("native review failed or exceeded its output budget; inspect its captured output")
        result = host.parse_output(stdout.read_text(encoding="utf-8"))
        if not isinstance(result.get("session_id"), str) or not result["session_id"]:
            raise lane.Refused("native review result has no host session identity")
        validate_report(result["report"], prepared, root, lane)
        current = prepare(root, members, args.base, lane, diff_path=prepared["diff_path"])
        if current["binding_sha256"] != prepared["binding_sha256"]:
            raise lane.Refused("candidate or review inputs changed during native review")
        if digest(prepared["diff_path"].read_bytes()) != record["diff_sha256"]:
            raise lane.Refused("the reviewer diff artifact changed during review")
        record.update(result=result, status="approved" if result["report"]["verdict"] == "approve" else "changes_requested")
    except (OSError, ValueError, subprocess.SubprocessError, lane.Refused) as error:
        record.update(status="failed", error=str(error))
    record["finished_at"] = lane.now()
    for name, path in (("stdout", stdout), ("stderr", stderr)):
        if path.is_file():
            record[f"{name}_path"], record[f"{name}_sha256"] = str(path), digest(path.read_bytes())
    atomic_file.replace_text(destination, json.dumps(record, indent=2) + "\n")
    return {"ok": record["status"] == "approved", "status": record["status"], "head": head,
            "record": str(destination), "packet_bytes": len(prepared["packet"].encode("utf-8")),
            **({"result": record["result"]} if "result" in record else {"error": record.get("error")})}


def problems(root: Path, members: list[dict], base: str, lane) -> list[str]:
    try:
        path = record_path(root, lane.head_state(root)[0], lane)
        record = json.loads(path.read_text(encoding="utf-8"))
        prepared = prepare(root, members, base, lane, diff_path=Path(record["diff_path"]))
        if record.get("status") != "approved" or record.get("binding_sha256") != prepared["binding_sha256"]:
            return ["native review is missing, unsuccessful or stale"]
        if record.get("packet_sha256") != prepared["packet_sha256"]:
            return ["native review procedure packet changed"]
        for name in ("packet", "diff", "stdout", "stderr"):
            artifact = Path(record[f"{name}_path"])
            if not artifact.resolve().is_relative_to((root / lane.REVIEWS).resolve()) \
                    or digest(artifact.read_bytes()) != record[f"{name}_sha256"]:
                return ["native review captured evidence changed"]
        observed = adapter().parse_output(Path(record["stdout_path"]).read_text(encoding="utf-8"))
        if observed != record.get("result") or not observed.get("session_id"):
            return ["native review result does not match host output"]
        validate_report(observed["report"], prepared, root, lane)
        return []
    except (OSError, ValueError, KeyError, TypeError, lane.Refused) as error:
        return [f"native review evidence unavailable: {error}"]
