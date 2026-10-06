# Light Backlog Path

These are the instructions of process switch `backlog_path` at
`light_when_eligible`. A task binds this file only when the project's Process
Policy selects that value; at the default, `standard`, every revision runs the
epic and root reviews as the flow describes. Where this file and the flow
differ on who reviews a revision, this file governs.

## Eligibility

Run `backlog_compile.py light-path-status --docs <workspace>/docs` after the
revision's stories and test plans are written. It reports `eligible`, the
`changed` stories, their `epic` and, when it is not eligible, every `reasons`
entry. A revision is eligible only when all of these hold:

- the backlog is in Requirement mode and has an earlier approved revision;
- the Requirement's `request_kind` is one of the `work_kinds` that
  `skill-content/backlog-plan/data/light-backlog-path.json` lists;
- the revision changes or adds at least one and at most
  `max_changed_stories` stories, a story counting as changed when it or its
  test plan is new or no longer carries the approval stamp of its bytes;
- every changed story's `work_kind` is one of those `work_kinds`;
- every changed story sits in one epic.

A revision that is not eligible takes the standard path; nothing in this file
applies to it.

## Review

- Open the changed epic's next review round with `stub-epic <epic-slug>
  --new-review`. One fresh `backlog-reviewer` reviews it, reading only the
  changed stories and their test plans with the sources they cite, and the
  Product Owner records its verdict as the flow describes. Its findings keep
  their severities, and a critical or major finding blocks as on the standard
  path.
- No root reader runs. After every epic's latest review is approved, run
  `backlog_compile.py record-light-root-review --docs <workspace>/docs`. It
  refuses an ineligible revision and a package with any structural finding,
  and otherwise writes the current root review round from the compiler's
  checks: every epic in `related_to`, every cross-epic dependency edge in
  `dependency_refs`, the Requirement Coverage row of the Stories that
  implement the Requirement, each section's evidence and conclusion and a
  `Light Path` section naming the changed stories.
- Approval re-checks the eligibility. A light root review whose revision has
  since outgrown the light path is refused; open a fresh root round with
  `stub-backlog-review` and run the standard root review.

The root review stays the backlog's cross-story gate in substance: cycles,
duplicate ids, unknown dependency targets, relation coverage and Requirement
coverage still fail the compiler, and backlog approval still checks the whole
backlog. What the light path gives up is a reader's judgment of cross-story
overlap between the delta and unchanged stories.

## Measurement

The project owner measures outside every task; no role acts on it. Per light
revision, record the wall clock from `begin-revision` to approval, review
passes and readers, and every fallback with the reason `light-path-status`
reported. Replay the revision as a frozen standard root review and compare
the verdict and findings.
