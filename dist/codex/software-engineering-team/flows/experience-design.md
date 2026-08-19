# Experience Design Flow

Spawn template: provide every role with `{{constitution}}`, exact upstream
receipts, affected Experience package paths and a required `SELF-CHECK`.

This flow maintains living, process-owned Experience packages. It does not
create releases, numbered baselines, programs or delivery state.

## Entry and scope proposal

1. Read this flow fully, `experience-modeling`, `obsidian-vault` and the
   current approved BA, Solution and Design System packages.
2. Resolve the mode exactly. An explicit `REQ-###` is Requirement mode;
   otherwise it is manual mode. Manual mode must not inspect or create a
   Requirement, Stage Impact or Stage Results.
3. In Requirement mode run `requirement_route.py` first. Accept `author` only
   for a real change; accept `bind_reuse` only after every supplied living
   Experience receipt verifies current. Do not mutate another stage.
4. Always run `experience_compile.py propose` first and retain its JSON only
   in a transient local scope-plan file. The proposal identifies
   primary BA process ownership and each `create`, `update`, `reuse`, `rename`
   or `retire` action plus exact upstream receipt hashes and, in Requirement
   mode, the Requirement semantic state. Ask the user to approve the exact
   action set before any Experience mutation. Ambiguous process ownership is a
   blocker.

## Preflight and authoring

1. Requirement mode obtains BA, Solution and Design references from the
   current Stage Results. Manual mode requires one exact approved/current BA,
   Solution and Design receipt; never choose among multiple candidates.
2. Create only `workspace/docs/experience-design/experiences/<process-slug>/`.
   The slug names the primary BA process, never a Requirement, component,
   release or `exp-` prefix. One primary process has one active Experience.
3. Use the same transient `--scope-plan <file>` and approved
   `--proposal-hash <hash>` for every create, revision, rename, retirement and
   set approval. A canonical primary process is
   `business-analysis/<space>/processes/<process-slug>-process`; it must
   resolve through the selected BA receipt. A no-op revision is prohibited.
4. UX Designer is the only writer. Child records use stable `JRN`, `FLW`,
   `SCR`, `STA` and `TRN` identities, exact refs and `record_state` only.
   Place all canonical records in the process-owned package; `_generated/`
   and `_ledger/` are compiler-owned.
5. Create bounded network-free artifacts with `init-artifact`. Render after
   each coherent change, then run compiler, artifact and scoped vault checks.

## Challenge loop

1. After an authoring milestone, invoke a fresh, read-only
   `experience-reviewer` for each applicable lens: process/criterion coverage;
   journey-state closure; failure/recovery; solution constraints; Design
   System/accessibility; cross-Experience ownership; and artifact fidelity.
2. Reviewers return structured transient findings only. They never write a
   `reviews/` tree, history note, round counter or lock.
3. UX Designer fixes every blocking finding in canonical records. A genuine
   unresolved fact belongs in `experience.md` as an assumption or open
   question. Re-run only affected review lenses; repeat until no blockers.

## Approval and handoff

1. Move every affected package to `in_review`, render, then use one atomic
   `experience_compile.py approve-set --scope-plan <file> --proposal-hash <hash>`
   invocation. Its package set must exactly equal the approved scope-plan
   create/update/rename set. All packages must be approved/current or none are
   approved.
2. Requirement mode binds all resulting `slug@rN` receipts in one
   `requirement_compile.py bind-stage` operation. Manual mode returns those
   receipts but writes no Requirement state.
3. Hand off only the exact approved receipt set to `backlog-plan`. Do not
   create stories, apps, system architecture or Delivery Items here.
