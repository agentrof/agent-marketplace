# Experience Design Flow

Spawn context includes `{{constitution}}`, exact upstream receipts, current
`application@rN` when one exists, the affected process packages, the complete
author-owned `experience-design/artifacts/` tree and a required `SELF-CHECK`.

This flow maintains living, process-owned Experience packages. It does not
create releases, delivery code, numbered baselines or product architecture.
Every `experience_compile.py` mutation uses the active host runtime's exact
absolute Python executable and the installed script's absolute path. Bare
Python names, `/usr/bin/env` indirection and direct shebang execution do not
receive machine-writer authorization.
`recover-open-scope` additionally receives authorization only after the hook
attests its exact lifecycle/navigation postimage and verifies that the complete
application prototype tree is byte-identical to its pre-command snapshot.
`rehydrate-published-scope` uses the same post-attestation boundary. It may
restore only package roots whose exact historic source bytes reproduce the
immutable application receipt selected by `application@rN`; it never changes
that application receipt or author-owned artifacts.

## Entry and scope proposal

1. Read this flow, `experience-modeling`, `obsidian-vault` and approved BA,
   Solution and Design System inputs.
2. Resolve Requirement mode only from an explicit `REQ-###`; otherwise use
   manual mode. Manual mode never creates Requirement state.
3. Run `experience_compile.py propose` before mutation. It binds primary
   process ownership, create/update/reuse/rename/retire actions, any independent
   application revision, upstream receipt hashes and current application
   receipt. Obtain approval for the entire action set.

## Stale open-scope recovery

If a scope consists only of `draft` or `in_review` non-retire package
mutations bound to an obsolete proposal, generate a fresh recovery proposal
from the old plan plus current upstream and application receipts. A scope that
also contains `retirement_pending` or a retire action is outside this recovery
boundary and fails closed. Use `propose --recover-scope-plan <old-plan>
--recover-proposal-hash <old-hash>` together with the normal root, origin-mode
and current input selectors; do not pass new package action selectors. Then run
`recover-open-scope --from-scope-plan <old-plan> --from-proposal-hash
<old-hash> --scope-plan <fresh-plan> --proposal-hash <fresh-hash>`. Recovery
proves the old plan, every open package and the fresh plan name the same
complete mutation set, binds the old proposal hash into the fresh plan,
rebinds that set and the open application atomically, and resets review state
to `draft`. Never recover only part of the set or edit compiler-owned state
directly. Recovery preserves authored child records and prototype/package
artifact bytes and does not change approved ledgers or receipts; run the full
review flow again. For a legacy scope only, packages may already carry the
fresh plan's exact current input bindings; every other package identity,
revision, Requirement binding and scope guard remains exact.

If recovery reports that the exact open package revisions are already
published by an immutable application receipt, do not reset the scope or edit
compiler-owned files. Run `rehydrate-published-scope --scope-plan <old-plan>
--proposal-hash <old-hash> --application-ref application@rN`. It proves every
package in the complete old scope still reproduces its published package hash,
restores those package roots to the published approved revision, and leaves the
application receipt unchanged. Then create a current scope and use the normal
`begin-revision` path. A hash mismatch, partial scope, conflicting receipt or
stale open application state fails closed.

## Preflight and authoring

1. Validate the selected upstream receipts and canonical BA primary process.
   One active Experience owns one primary process.
2. Author stable journey, flow, screen, state and transition records in their
   process-owned package. Keep `_generated/` and `_ledger/` compiler-owned.
3. Begin the proposal-bound application revision. The compiler creates only
   machine lifecycle state; it neither creates nor edits prototype content.
4. Build or revise the prototype freely below
   `workspace/docs/experience-design/artifacts/`. The author chooses files,
   folder layout, page topology, technologies, assets, dependencies and runtime
   behavior. `index.html`, separate HTML pages, CSS, JS and asset folders are
   useful possible conventions, not requirements.
5. Do not encode lifecycle state, receipt hashes or delivery constraints into
   the prototype. Its files are evidence for review, not delivery source code.
6. The mechanical snapshot boundary accepts regular non-symlink files inside
   the tree and hashes their exact bytes and relative paths. It does not parse,
   execute, lint, sandbox, normalize or constrain their contents.
7. Enter application review only when the prototype is ready for human review.
   The compiler captures the current artifact-tree and package-set receipt.

## Challenge loop

1. Invoke a fresh, read-only `experience-reviewer` after each meaningful
   authoring milestone.
2. Challenge process and criterion coverage, journeys and state closure,
   failures and recovery, solution constraints, accessibility, responsive and
   localization behavior, cross-Experience ownership, and prototype fidelity.
3. Treat naming, folder layout, framework, HTML/CSS/JS structure and visual
   implementation choices as reviewer advice or author judgment. They never
   become compiler rejection rules.
4. Reviewers write no review tree, history note, counter or lock. Their
   findings are advisory and never prevent a prototype receipt from being
   approved.
5. The final reviewer emits a transient schema-v4 JSON attestation bound to
   proposal hash, artifact-tree hash, package-set hash, application hash and
   revision. Its `advisories` are informational and may be empty or non-empty.

## Approval and handoff

1. Move affected packages and the application lifecycle to `in_review`, then
   run one `experience_compile.py approve-set` invocation with the approved
   proposal and reviewer attestation.
2. Approval is atomic across process records, artifact snapshot receipt,
   `_generated/open-application-revision.json`, registry and ledger. It never
   alters the author-owned artifact files.
3. A successful approval creates current `application@rN` and returns it with
   the exact current zero-or-more process receipts. Application-only revisions
   do not increment process revisions.
4. Requirement mode binds the returned set in one `requirement_compile.py
   bind-stage` operation. Manual mode returns it to `backlog-plan` without
   Requirement mutation. Do not create delivery artifacts here.
