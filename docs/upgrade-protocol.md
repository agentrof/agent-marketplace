# Upgrade protocol

## Choose the required operation

A package release replaces installed package content. Project setup is needed
for first integration, incompatible or missing managed inputs, and explicit
migration. A release number alone never requires project setup.

Launch projections use a behavior stamp derived from the definition, renderer
and resolved model/effort. Definition, model or renderer changes need the
affected host projection refreshed. Skill-body and hook updates need the host
reload that makes the new package active. Neither implies a rewrite of authored
project documents. [Host architecture](architecture.md)

## Inspect, apply and verify

1. Build and validate every registered host distribution from the same canonical
   source. Identify the installed candidate and active host before inspecting
   project integration.
2. Run `setup_project.py inspect --project-root <root> --json` when setup or
   migration is required. Inspect exact key changes and owned-file hashes.
   Resolve actual blockers. Native Windows may require the explicit
   `git.core_longpaths` choice before apply.
3. Run `setup_project.py apply --project-root <root> --json`, followed by
   `setup_project.py check --project-root <root> --json`. Both use the shared
   planner. Apply rebuilds its plan under the mutation guard and checks each
   preimage before atomic replacement.
4. Inspect and refresh the affected host projection. Preserve unrelated host
   configuration, user instruction companions and authored files. Retired
   package-owned launch files are removed in the rollback-capable transaction.
   A conflicting user-owned file remains a reported blocker.
5. Verify the remaining plan is empty. Review and commit only the intended
   tracked changes. Reload or restart the host as required, then verify the
   loaded package, hooks, definitions and effective settings.

Closing-check failure rolls back setup-owned paths that still match their
postimage. Observed concurrent changes are preserved and reported as conflicts.
Pause unrelated editors on setup-managed targets during apply. Portable atomic
replacement cannot exclude an uncooperative writer between every check and
write. Repeated apply against unchanged inputs must converge to an empty plan.

The first managed `.gitattributes` addition may require a one-time re-checkout
when Git already converted governed files. Follow the setup procedure for the
identified case. [Setup](../plugins/software-engineering-team/skill-content/setup/SKILL.md)

## Skill and profile migration

The public entry names remain stable. Internal responsibilities move into six
methods and their conditional references. Historical task records remain
readable. New work explicitly loads current methods and records its own evidence.
Preserving old history never establishes fresh completion for a new candidate.

The four compute profiles are `light`, `work`, `review` and `critical`.
`class_models.<host>.<profile>` retains project model and effort overrides.
Profile selection leaves the launch access boundary unchanged.

Preview old model overrides through the migration planner. Several old slots
can collapse into one profile only when their explicit values agree. Conflicting
values, persona-specific mappings and unsupported slots require an explicit
migration decision. Preserve their original values until resolved. Migration
is a one-time translation, with no permanent class aliases in new authoring.

For a reviewed replacement, use `project_config.py migrate --config
<workspace/config.json> --config-file <reviewed-replacement.json>
--expected-sha256 <current-file-sha256> --dry-run --json`. Inspect the full
before/after values and dropped keys, then apply the same command without
`--dry-run`. The maintenance guard, exact pre-write hash and closed-schema
validation protect this one-time replacement. Conflicting trigger mappings
and customized retired settings require this explicit resolution.

Retain valid project language, layout, proof and trigger settings allowed by the
closed config schema. Surface removed or incompatible fields in inspect before
apply. Keep datastore choices and business contracts in their authored sources.
The four supported companions are mandatory at their applicable workflow step
when installed and enabled. A migration records unavailable integrations without
installing packages or removing independently installed companions.

Local runtime caches are reconstructible. Losing a claim, receipt or session
record requires reconciliation against tasks, candidate commits and external
outcomes before retrying side effects. Package versions and cache presence do
not grant authority or establish delivery compatibility.

## Version and build identity

- `.changes/*.json` declares release impact. The release command is the sole
  writer of `versions.json`. Host manifests expose its calendar release version.
- Schema-v4 `.agent-marketplace-package.json` binds the closed file inventory,
  hashes, executable set and Delivery read/write capability. It verifies package
  contents and selects supported record adapters.
- Generated UTF-8 text uses canonical LF. Unknown and binary files remain
  byte-exact. Release preparation uses the fixed Git checkout policy and the
  tracked executable-mode contract.
- Per-file hashes occupy separate lines. Packages carry no per-commit source
  identity. Independent package edits can still conflict where sorted inventory
  entries meet, so ordinary Git conflict resolution remains necessary.
- The canonical snapshot `build_id` is recorded in `.release/stable.json` by the
  release commit. PR replay and release verification recompute it. It is separate
  from a local launch behavior stamp.
- Setup writes no package version or build ID into project configuration and
  performs no remote mutation. Package publication and release follow the
  maintainer protocol and its exact-candidate validation, authority, ref leases,
  immutable reconciliation and final clean-state checks. [Maintainer operations](maintainer-operations-protocol.md)
