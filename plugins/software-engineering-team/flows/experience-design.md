# Experience Design Flow

This flow turns approved analysis, solution decisions and design-system tokens
into an approved, project-local experience baseline. It ends before backlog
planning and never creates delivery state.

Spawn template: paste `{{constitution}}`, the frozen input paths, output paths
and required `SELF-CHECK` into every role prompt.

## Preconditions

1. Confirm `workspace/config.json` is owned by the Software Engineering Team.
2. Run `preparation_check.py status --project-root <root> --json`; the named
   predecessor must be `experience-design`.
3. Run the Business Analysis approval gate for every referenced scope, the
   solution landscape checker, the design-system compiler and the scoped vault
   checker. Any failure routes to its owning entry and stops this flow.
4. Read `experience-modeling` and `obsidian-vault`. The approved tracked
   experience documents are the complete stage state.

## Authoring

1. Use `experience_compile.py init-program` and `init-release` to create the
   program and release skeletons under `workspace/docs/experience-design/`.
2. Model the experience domain-first. For each space, domain, journey,
   flow-set and screen, create compiler stubs, link the approved BA criteria,
   solution decisions and design-system references, and keep the owning map
   current.
3. Create bounded HTML drafts beside their manifest with
   `experience_compile.py init-artifact`. Edit the draft in place. Approve it
   only through `approve-artifact`, which verifies declared IDs, navigation,
   local assets and hashes.
4. Run `experience_compile.py check`, artifact checks and the scoped vault
   check after each bounded change. Record challenge rounds in the owning
   `reviews/` folder, fix all blocking findings, and cap the loop at three
   rounds unless the owner explicitly reopens the scope.

## Gates and handoff

Close in this order: leaf domain, parent domain, space, multi-space, release,
then program. At every gate render the registry, coverage, navigation and
typed relations, ask the owner for an explicit choice, and commit the complete
tracked subtree. When the program is approved, report `backlog-plan` as the
next entry. Do not create stories, tasks, lanes, release ledgers or hidden
records in this flow.
