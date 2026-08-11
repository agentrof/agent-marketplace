# Engagement Session

Read this reference completely before starting or resuming solution design.
The solution tree is one living project-level landscape. An engagement studies
one topic; decision notes and the landscape record its accepted outcome.

## Preflight

1. Resolve the PMO CLI and dispatcher (`$RUN`, `$TEAM`) through the develop
   flow's state contract. Invoke scripts as
   `"$RUN" run "$TEAM" scripts/<name>.py`.
2. Read `workspace/config.json`. If it is missing or has no `project_key`,
   route to setup and stop. `output_language` governs Markdown body prose;
   `terminology_language` governs names and technical terms. Filenames, ids,
   and Status lines stay English.
3. Ground every cited analysis domain with `ba_compile.py check --space
   workspace/docs/business-analysis/<slug> --gate approval --node <domain>`.
   Route nonzero results to business-analysis. Pre-analysis groundwork may
   proceed without a space only when flagged ungrounded and every decision
   cites named assumptions.
4. Mint a kebab-case topic slug. A reopened topic appends `-2`, `-3`, and so
   on; never reuse a closed slug.
5. Use UPDATE mode for an existing tree. On first run, use the
   `solution-architecture` skill's `landscape-docs` birth skeletons. For an
   existing system, baseline the repository before engagement one and create
   one dated baseline decision cited by the Current component rows.
6. Resume from the landscape, generated decision index, and open engagement
   list. Read only the active engagement's documents in full.
7. Sweep staleness every session. Compare live decisions' revisit triggers and
   cited budgets against the analysis space. Open a re-evaluation engagement
   for every breach before a new topic.
8. Run `vault_check.py check --vault workspace/docs --scope solution-design`.
   Treat every finding as session repair work; use `migrate` for deterministic
   classes.

## Engagement work

1. Maintain `engagements/<slug>.md` with framing, touched components, cited
   requirements and constraints, an options matrix, and a verdict.
2. Encode traceability in frontmatter. The landscape `derives_from` every
   approved analysis scope it uses. An engagement derives from its landscape
   and relevant analysis nodes. Each decision links its engagement and uses
   exact `satisfies` or `constrained_by` criterion and budget aliases.
3. Follow the `solution-architecture` references for evaluation method,
   landscape document contract, and worked engagement. Build, buy, or
   integrate per component. Route a configured-stack enum change through
   `configure`, never around it.
4. Debate in rounds. Land every accepted verdict as an atomic note under
   `decisions/`, update the landscape, and supersede through `stamp-decision`.
   Nothing decided may remain conversation-only.
5. After each milestone run `artifact_check.py --path <doc>
   --require-sections` and repair findings immediately.

## Mandatory challenge

Before every gate, read the `solution-architecture` skill's `challenge-lenses`
reference. Spawn `analysis-challenger` once per lens with fresh context,
read-only access, and files only. Route named practitioner questions to
`domain-expert` with an explicit expert profile.

Triage every finding in conversation as fix, reject with a one-line reason, or
defer with a named gate note. Record the round at
`reviews/<slug>-round-<n>-review.md`. Round 1 is mandatory; continue only while
blocking findings remain, with a cap of 3. Run a landscape-wide round on owner
demand or when fold-in reveals a contradiction.

## Solution gate

Run all mechanical checks before asking the owner:

- `artifact_check.py` on every touched document.
- `landscape_check.py --tree workspace/docs/solution-design` with exit 0.
- `vault_check.py check --vault workspace/docs --scope solution-design` with
  every frozen path passed as `--exclude`.
- `ba_compile.py resolve --space <space> --ids <every-cited-id>` with exit 0.
- Every challenge round recorded and every finding dispositioned.

Present each verdict with its strongest rejected alternative, sustainability
judgment, cited constraints, impact on in-flight or planned work, and deferred
findings. Read PMO `resume-info --project-key <key> --json`; name affected
stories and route invalidated scope to the product owner for re-slicing.

Ask Approve, Request changes, or Pause through the choice gate with tradeoffs
in the option descriptions. On approval:

1. Stamp the engagement with `landscape_check.py --tree <tree>
   --stamp-engagement <slug> --status approved`; never type the UTC date.
2. Record deferred questions under Verdict with revisit notes.
3. Fold the outcome into `landscape.md` and run `render-decisions` and
   `render-relations`.
4. Update `maps/solution-design.md`; when the first content births the tree,
   add its dynamic `home.md` map edge.
5. Commit the whole tree together.

## PMO pulse and scope

At every round and gate close, append a PMO event naming the engagement, round,
and finding counts. Resolve the launcher from the develop flow's state
contract. Keep content in files and process pulses in the database.

Writes are limited to `workspace/docs/solution-design/`, plus `home.md`, its
map note, and missing per-file vault payload. Route requirement gaps to
business-analysis, implementation to deliver, stack changes to configure, and
per-story design to develop step 1.
