"""Fail-closed eligibility for explicitly absent visual planning packages."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import landscape_check
import requirement_compile
from ba_compile import without_code


def absence_findings(docs: Path, props: dict, requirement: Path | None,
                     bound_stages: set[str], policy: dict, *,
                     allow_historical: bool = False) -> list[str]:
    """Check static declarations and, for new handoffs, current eligibility.

    Historical collection must finish with ``historical_absence_findings``
    after the owning compiler has collected the complete approved package.
    """
    declared = props.get("absent_input_stages", [])
    contract = props.get("input_contract", "")
    if not {"absent_input_stages", "input_contract"}.intersection(props):
        return []
    errors: list[str] = []
    if contract != policy.get("contract"):
        errors.append("absent inputs need the supported input_contract")
    if not isinstance(declared, list) or not all(isinstance(stage, str) for stage in declared):
        return errors + ["absent_input_stages must be a stage list"]
    if not declared or len(set(declared)) != len(declared):
        errors.append("absent_input_stages must be nonempty and unique")
    allowed = set(policy.get("absent_stages", []))
    if set(declared) - allowed:
        errors.append("only policy-declared visual input stages may be absent")
    if props.get("planning_mode") != "requirement" or requirement is None:
        return errors + ["absent inputs require an approved Requirement"]
    req_props, req_body = requirement_compile.split_note(requirement)
    errors.extend(requirement_compile.requirement_findings(
        requirement, require_approved=True, allow_historical_reuse=allow_historical))
    if req_props.get("status") != "approved" or req_props.get("request_kind") not in policy.get("request_kinds", []):
        errors.append("absent inputs require an approved technical or defect Requirement")
    impacts = {stage: disposition for stage, disposition, _refs, _reason
               in requirement_compile.impact_rows(req_body)}
    for stage in declared:
        if impacts.get(stage) != "not_applicable":
            errors.append(f"{stage} may be absent only when the Requirement marks it not_applicable")
        if stage in bound_stages:
            errors.append(f"{stage} cannot be both absent and bound")
    for required in policy.get("required_stages", []):
        if required not in bound_stages:
            errors.append(f"absent visual inputs still require a bound {required} package")
    if allow_historical:
        if props.get("status") != "approved":
            errors.append("historical absent inputs require an approved backlog")
        # Current topology and later visual packages are not the old Delivery's
        # inputs. The complete collected package must still pass its original
        # approval hashes and committed-source proof before this read succeeds.
        return sorted(set(errors))
    component_errors: list[str] = []
    components = landscape_check.component_rows(docs / "solution-design", component_errors, enforce=True)
    built = [row for row in components if row.get("sourcing") == "build"]
    if component_errors or not built:
        errors.append("absent visual inputs require an explicit valid Solution component topology")
    allowed_kinds = set(policy.get("app_kinds", []))
    if any(row.get("app_kind") not in allowed_kinds for row in built):
        errors.append("absent visual inputs require a headless Solution topology")
    project = next((parent for parent in (docs, *docs.parents) if (parent / ".git").exists()), None)
    if project is None:
        return errors + ["absent inputs require Git history to prove no previous package was removed"]
    shallow = subprocess.run(["git", "--no-replace-objects", "rev-parse", "--is-shallow-repository"],
                             cwd=project, capture_output=True, text=True, check=False)
    if shallow.returncode or shallow.stdout.strip() != "false":
        return errors + ["absent inputs require complete Git history"]
    for stage in sorted(set(declared) & allowed):
        subtree = docs / stage
        if subtree.is_symlink() or (subtree.exists() and (
                not subtree.is_dir() or any(path.is_file() or path.is_symlink() for path in subtree.rglob("*")))):
            errors.append(f"{stage} has existing package content and cannot be declared absent")
        history = subprocess.run(
            ["git", "--no-replace-objects", "log", "-1", "--format=%H", "HEAD", "--",
             subtree.relative_to(project).as_posix()], cwd=project, capture_output=True, text=True, check=False)
        if history.returncode or history.stdout.strip():
            errors.append(f"{stage} has previous or unverifiable package history and cannot be declared absent")
    # Authored links are the declared planning boundary, including existing
    # backlog records. Generated navigation is not an upstream dependency.
    for note in sorted((docs / "backlog").rglob("*.md")):
        if "_generated" in note.relative_to(docs / "backlog").parts:
            continue
        body = re.sub(
            r"(?m)^(?:##[^\n]*)?<!-- sec: nav -->[^\n]*(?:\n|$).*?(?=^## |\Z)",
            "", without_code(note.read_text(encoding="utf-8")), flags=re.S,
        )
        for stage in sorted(set(declared) & allowed):
            if re.search(r"\[\[" + re.escape(stage) + r"/", body):
                errors.append(f"{note.relative_to(docs)} references absent {stage} content")
    return sorted(set(errors))


def historical_absence_findings(docs: Path, record: dict) -> list[str]:
    """Verify the existing approved backlog before honoring historical absence.

    No baseline is reconstructed or trusted from a caller-supplied hash. The
    same authored package, approval stamps and package digest used by Delivery
    are verified against its current, committed bytes. New visual work outside
    that package cannot revoke its original approved input contract.
    """
    import backlog_compile
    import stage_package

    backlog = record.get("backlog")
    if not isinstance(backlog, dict) or not isinstance(backlog.get("props"), dict):
        return ["historical absent inputs require a readable approved backlog"]
    props = backlog["props"]
    if not {"absent_input_stages", "input_contract"}.intersection(props):
        return []
    try:
        errors = backlog_compile.approval_findings(record, docs)
        paths = backlog_compile.package_paths(record, docs)
        if not stage_package.paths_are_committed(paths):
            errors.append("historical absent inputs require byte-exact committed backlog sources")
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        return [f"historical absent input approval cannot be verified: {exc}"]
    return sorted(set(f"historical absent input approval: {error}" for error in errors))
