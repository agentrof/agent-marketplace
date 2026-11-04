# Authoring guide

## Canonical layout

Keep portable methods and scripts in the plugin. Put host invocation,
permissions and generated wrappers in the platform adapter.

```text
plugins/software-engineering-team/
  constitution.md
  flows/
  skill-content/
    <entry-or-method>/
      SKILL.md
      references/       # Conditional depth
      scripts/          # Only when this capability needs executable logic
      data/             # Validated policy values
  scripts/
  templates/
platforms/
  shared/software-engineering-team/launch-profiles.json
  <host>/adapter.json
  <host>/adapter.py
  <host>/execution-profiles.json
  <host>/model-catalog.json
  <host>/software-engineering-team/host-contract.md
```

Create components through `tools/scaffold.py`. Keep one source of each rule.
A reference holds substantial optional detail. A script owns a repeatable
mechanical operation. Do not repeat a reference book inside a skill or worker.

## Skill template

Every skill uses the following sections. Describe the task that should select
it and the concrete outcome. Scope its authority, stopping condition and evidence.

```markdown
---
name: <stable-name>
description: <task and condition that make this skill relevant>
exposure: <entry-or-internal>
---

# <Title>

## Outcome

## When to use

## Inputs and authority

## Method

## Evidence and completion

## Conditional references
```

Descriptions provide retrieval cues. The method supplies short imperative
steps. Conditional references say when to read each file and use working
relative links. Keep critical authority and data boundaries in the active
brief even when optional context is omitted. Token budgets are product targets
measured on the rendered package, with exceptions justified by required behavior.
No section count or word count establishes model compliance.

The public entries are `setup`, `configure`, `deliver`, `autopilot`,
`retrospective`, `issue-report` and `companions-uninstall`. The internal methods
are `analyze`, `solution-design`, `data-design`, `experience-design`, `verify`
and `review`. Preserve these names in cross-references and migrations.
An internal method may be registered differently across hosts. Its canonical
file remains explicitly loadable through the delivery method command.

## Technical workers and model selection

The shared launch registry defines five identities and their allowed pairs.

| Identity | Compute profile | Access |
| --- | --- | --- |
| light-worker | light | write |
| worker | work | write |
| reviewer | review | read |
| critical-worker | critical | write |
| critical-reviewer | critical | read |

Resolve a supported pair with `model_class.py resolve --profile <profile>
--access <read-or-write> --host <host> --json`. Pass the resulting identity to
the native launcher. An unsupported pair fails instead of inventing a fallback.
Keep role-specific domain content in a skill. The parent coordinates the
workers and retains ownership of the final result.

`class_models.<host>.<profile>` overrides model and effort together with package
defaults for omitted values. It cannot alter access. Retired persona mappings
require explicit migration, with conflicts preserved for resolution.
`role_settings.py` validates the effective settings. Host catalogs and effort
policies own allowed IDs, supported levels, confirmations and refused levels.
Keep model IDs and version-specific behavior out of canonical method prose.

The resolver returns requested settings and unverified observations. Record
actual model and effort only when the host exposes them. A model availability
probe establishes catalog availability, with its account and freshness limits.
It cannot establish which model executed an attempt. An unavailable pin stays
visible. A safe fallback is a separately resolved attempt after observing that
the failed attempt made no changes. It never rewrites shared configuration.

Claude's read launch definitions restrict tools to `Read`, `Grep` and `Glob`.
Verify that the host loaded the intended identity. Codex custom definitions
inherit parent permissions and cannot establish a read-only boundary by naming
one in the file. Use a separately verified read-only execution boundary for
independent review. Report a missing boundary as pending verification.
Read the active host contract for exact invocation and reload behavior.

## Evidence and behavioral evaluation

`task_lane.py method` returns the method content and records its digest and
load time. `--evidence` records a worker-supplied file reference and digest.
That file still needs semantic verification. Report loaded, reported applied
and verified separately. A source or evidence change invalidates the recorded
hash match. Describe exactly which command enforces each gate and its scope.
`task_lane.py companion --task <id> --name <catalog-id> --host <host>
--source <SKILL.md>` returns exact operation-specific source sections with their
provenance. Its namespaced receipt cannot satisfy an internal method requirement.
Source loading establishes neither publisher authenticity nor successful
application. Graphify adds `--graph <graph.json> --query <question>` to record
actual executable, graph, process result and output identity. A bounded preview
labels omitted output and retains the complete artifact.
`--unavailable <reason> --evidence <project-file>` records a worker-reported
availability problem and concrete core fallback. A missing receipt remains a
gap. `task_lane.py review --task <id> --host <host>` supplies the applicable
procedures directly to the native reviewer, with a bounded input packet. The
development candidate raises that packet limit to 64 KiB while retaining the
8 KiB per-source limit, exact source content and required source/task coverage.
An oversized packet still refuses review. The runner
records supplied content separately from the reviewer's assessments and source
citations. Missing coverage, stale inputs or an unsuccessful host result cannot
satisfy the review gate. The record does not prove semantic compliance or resist
a malicious owner with the same filesystem authority. Reviewers retain their
native read-only boundary and do not write the owner's review record.
The development candidate gives POSIX helpers a bounded exit grace, records
member metadata on failure, and blocks retry when process-group cleanup is
unconfirmed. Persistent zombie groups are not accepted as successful cleanup.
The companion completion record binds its exact candidate head. A changed
candidate requires renewed evidence. A knowledge task that changes code receives
the code-work companion requirements.

Evaluate routing and outcomes independently. Use positive and negative task
examples, ordinary entry points, the intended host configuration and recorded
candidate identity. Include data migration, denied operations, partial failure,
retry, concurrency and clean-case false positives where affected. Compare
latency, cost and quality on matched inputs before claiming improvement.
A synthetic harness passing cannot establish behavior of an untested live host.

## Project integration and portability

Setup preserves authored files and owns only its declared generated targets.
Per-target preimage checks, atomic writes and rollback protect cooperative
concurrent edits. Pause other editors on those targets during apply, since a
portable filesystem cannot atomically compare arbitrary content and replace
against every external writer. Model or launch-definition changes require only
the affected projection refresh. A release number alone does not make it stale.

An `artifacts/` directory under project documents holds opaque author-owned
files. Its contents never become executable workflow authority merely by
being linked. Keep project-specific techniques and data out of package code.
Ponytail, Graphify, Impeccable and security-audit have mandatory, scoped routing
when installed and enabled. Record availability separately from source loading
and application. Missing capabilities need a reason and sufficient core-method
evidence. Installations and removals retain their own explicit authority.

## Build and validation

Generate every registered distribution, then stage the complete candidate.

```sh
python3 tools/build_distributions.py
make counts
git add <complete-candidate-paths>
make check-local
make verify-local
```

Use `make counts` only for derived README counts. Never edit `dist/` manually.
Local gates require candidate consistency. PR CI owns the full suite and all
required platform and host lifecycle checks. Reviewers stay read-only and use
the owner's exact staged candidate and evidence. Additional local reproduction
follows the repository's single-finding test rule.

Declare executable paths in `package-modes.json`. Keep generated text LF and
binary payloads byte-exact. Scan the complete public diff and history for
consumer-identifying content. Publication, merge and release require their
existing authority. [Maintainer operations](maintainer-operations-protocol.md)
