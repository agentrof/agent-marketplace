# Experience Design Flow

Spawn context includes `{{constitution}}`, exact upstream receipts, current
`application@rN` when one exists, the affected process packages, the complete
author-owned `experience-design/artifacts/` tree and a required `SELF-CHECK`.

This flow maintains living, process-owned Experience packages. It does not
create releases, delivery code, numbered baselines or product architecture.

## Entry and scope proposal

1. Read this flow, `experience-modeling`, `obsidian-vault` and approved BA,
   Solution and Design System inputs.
2. Resolve Requirement mode only from an explicit `REQ-###`; otherwise use
   manual mode. Manual mode never creates Requirement state.
3. Run `experience_compile.py propose` before mutation. It binds primary
   process ownership, create/update/reuse/rename/retire actions, any independent
   application revision, upstream receipt hashes and current application
   receipt. Obtain approval for the entire action set.

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
