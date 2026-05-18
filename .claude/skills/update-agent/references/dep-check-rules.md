# Dependency Check Rules

Procedural checklist Claude follows for every dependent surfaced by the mechanical scans. Used by `update-skill` and `update-agent`. Mechanical = deterministic gate, advisory = Claude judgment.

## Mechanical checks (run by scripts, output JSON)

These produce the deterministic `## Mechanical findings` section of the impact report.

1. **Dependency graph walk** (`scripts/dep_scan.py`):
   - Read `.claude/skills/*/manifest.yaml` and discover edges from `depends_on`.
   - From the target id, walk inverse graph (who depends on me) BFS with a visited set for cycle detection.
   - Output: `direct_dependents` (depth 1), `transitive_dependents` grouped by depth (2..N), `cycles_detected` (list of cycle nodes), `forward_dependencies` (what the target itself depends on).

2. **Content grep** (`scripts/content_scan.py`):
   - For every `SKILL.md`, `AGENT.md` (subagent file in `.claude/agents/*.md`), `README.md`, `manifest.yaml` under the marketplace, grep for:
     - The target id as a substring.
     - The `--rename-from` string (if a rename is in the change).
   - Output: list of `{file, line, matched_text}` records.

3. **Path scan** (`scripts/path_scan.py`):
   - When the change moves or renames a file inside the target, grep all components for the old repo-root-relative path.
   - Output: list of `{file, line, matched_text}` records pointing at stale path references.

## Advisory checks (Claude judgment, non-deterministic)

These produce the `## Advisory findings (Claude review)` section. Marked clearly as advisory.

For each direct dependent surfaced by the mechanical scan:

A. **Behavior shift check**: read the target's old description and body and the new (proposed) description and body. Ask: would the dependent's usage break if it expected the old behavior? If yes, name the specific usage point.

B. **Contract drift check**: if the target's body documented inputs, outputs, or invariants in prose (we have no typed contracts in Phase 1), check whether those prose contracts changed in meaning. Surface any change that downstream prose relies on.

C. **README/example drift check**: if the dependent's `README.md` or `examples/` reference the target by id or by behavior, verify the references still hold after the change.

D. **Caveman violation check**: did the change introduce em dash, hardcoded paths, or prose that violates CLAUDE.md? If yes, flag.

## Reporting format

`.run/<uuid>/artifacts/impact-report.md` shape:

```markdown
# Impact Report

Target: <target_id>
Change: <one-line summary>

## Mechanical findings (deterministic)

### Forward dependencies (what target needs)
- <id> ...

### Direct dependents (depth 1)
- <id>
  - SKILL.md line 23: "..."
  - manifest.yaml line 8: depends_on entry

### Transitive dependents
- Depth 2: <id>, <id>
- Depth 3: <id>

### Cycles
- None detected.
  OR
- <id> -> <id> -> <id> (cycle, will require per-step approval if cascading)

### Path references (if change includes path move)
- <file>:<line> references old path "<old>"

## Advisory findings (Claude review)

### Behavior shift
- <dependent>: <observation>

### Contract drift
- <dependent>: <observation>

### README/example drift
- <dependent>: <observation>

### Caveman violations
- <file>:<line>: <violation>
```

## Decision options surfaced to user

After report, ask:

- `Apply target only` - write only the target's change; do not modify dependents. Surface any breakage in chat afterward.
- `Cascade to all affected` - apply target plus auto-update direct dependents per advisory findings. Cycles still require per-step approval.
- `Cancel` - leave repo untouched.

Cycles never auto-resolve.
