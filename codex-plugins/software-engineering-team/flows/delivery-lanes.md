# Delivery Lanes Flow

State-machine procedure for the INTEGRATOR session: one session per
project, on the primary checkout, coordinating parallel work orders
across git worktree lanes. Lanes run the develop flow untouched; this
flow proposes them, opens them, watches them and closes them. The spec
it must match lives in the repository's orchestration document.

## Critical behavioral rules

You MUST follow these rules exactly. Violating any of them is a failure.

1. Execute the states in the declared order per cycle. Do NOT skip.
2. State and artifacts are the source of truth. Read from the PMO
   database and from FILES, never from conversation memory. After any
   compaction, re-run the LANES view before acting.
3. Every lane start and every merge is an explicit user choice, asked
   through the AskUserQuestion popup. Offer exactly: Approve / Skip /
   Pause.
4. Halt on failure: present the error and ask. Never continue silently.
5. Spawn only this plugin's agents.
6. Never enter plan mode. This flow IS the plan.
7. Single writer per work order. This session's database writes are
   ONLY: backlog mutations on main (item import / update / add-dep /
   add-dod) and closing writes for MERGED lanes
   (checkpoint, item update --status done / --deployed-verified,
   work-order set-status --status complete), plus work-order release as
   the recovery verb. Lane-scoped verbs (set-step, record-gate, bump,
   set-ownership, finding, task, coverage, budget) belong to the lane's
   own session; the CLI's worktree binding refuses them from here, and
   an approval request for a lane's gate is always answered with the
   lane's worktree path, never with a record-gate call.

## State contract

CLI resolution as in the develop flow: the launcher at
"${AGENTROF_HOME:-$HOME/.agentrof}/bin/pmo_cli.py"; run the idempotent
init-db subcommand before first use. project_key comes from
workspace/config.json.

Read surface: resume-info --project-key <key> --json (active work
orders with worktree, steps, gates, ownership), item ready
--project-key <key> --json (the dispatch buckets), item list /
list-deps / order for detail. max_parallel comes from
workspace/config.json when present (positive integer; absent means 3);
only the configure entry changes it.

Lane naming: worktree directory ../<project-dir>-wp-<nn> (sibling of
the primary checkout); the lane's branch wp-<nn>-<kebab-slug> is
created by develop step 0 inside the lane, never here.

## Cycle

Each delivery-lanes invocation runs one cycle: LANES, then whichever of the
other states the situation calls for. After a merge checkpoint, re-run
PROPOSE before ending the cycle.

### LANES (always first)

- Render the lane table from resume-info: story, order key, status,
  current step, worktree, pending approval, dangling.
- Pending approval: status waiting_gate with an undecided gate for the
  current step means a human approval is pending IN THAT LANE; answer
  any "approve it" request with the lane's worktree path.
- Ready to merge: status waiting_gate with step 5 done and the delivery
  gate recorded means the lane ended at a pull request; route to MERGE
  CHECKPOINT after the human merges.
- Dangling: a session_ended_with_active_work_order event for the order
  with no later activity means the lane's session died; route to
  EXCEPTIONS.

### PROPOSE

- Run item ready --json. Candidates come from its ready list, capped at
  max_parallel minus the lanes in flight; blocked, claimed and
  stale_in_development are reported, never proposed.
- Per candidate, sanity-read the story row (DoR text, dep reasons) and
  give the SHARES advisory: compare the candidate's scope and dep-edge
  reasons against in-flight orders' ownership prefixes (resume-info
  ownership maps) and their stories' scopes. The environment prefix
  (workspace/environment/) is a shared resource: two candidates with
  environment impact never run together. Verdict per candidate:
  "ownership expected disjoint" or "waits: SHARES <contract> with
  WP-## in flight".
- Present exactly in this shape: "WP-03 and WP-05 can start; ownership
  expected disjoint; WP-04 waits: SHARES orders endpoint with WP-02 in
  flight." The human approves each lane individually through the
  AskUserQuestion popup, one question per lane, the SHARES holdback
  stated in the option description; a holdback override is recorded via
  event append.
- On first PROPOSE in a project, recommend once: enable the repository's
  branch protection rule "require branches to be up to date before
  merging", the platform-side twin of the staleness check below.

### OPEN LANE (per approved candidate)

- From the primary checkout: git worktree add --detach
  ../<project-dir>-wp-<nn> <default-branch> (detached, because the
  default branch is checked out here; develop step 0 creates the story
  branch inside the lane).
- Record the advisory audit line: event append --action lane_opened
  --payload '{"story": "WP-<nn>", "worktree": "<path>"}'.
- Print the handoff, verbatim shape:

  Lane opened for WP-<nn> <story title>.
  Directory: ../<project-dir>-wp-<nn>
  1. Open a NEW agent session in that directory: a Claude Code session
     on Claude, a new Codex task selecting that worktree in the App, or
     an interactive Codex CLI session started in that worktree.
  2. Say: "request: deliver WP-<nn>"
  3. Approvals for this story happen in THAT session only.
  4. The lane ends at an opened pull request. Report back here and I
     will run its merge checkpoint after you merge.

### MERGE CHECKPOINT (strictly serialized, one lane at a time)

- Precondition, mechanical: the lane branch contains the current main
  tip: git merge-base --is-ancestor <default-branch> wp-<nn>-<slug>
  exits zero. Nonzero: "stale branch, rebase in the lane first"; stop
  for that lane, others may proceed.
- The human merges the pull request (merging is a human act; the
  merge-here-or-on-platform choice is asked through the AskUserQuestion
  popup). Then, on the primary checkout: pull main; run the configured
  test_command. Red suite: STOP all further merges, route a
  fix-atomic through the request entry, resume merging only on green.
- Environment smoke, when env_command is configured: from-scratch
  bring-up on the merged main (env_command up, then down). A failed
  bring-up stops further merges exactly like a red suite.
- Execute the develop flow's "Merge checkpoint (main line only)" list
  verbatim, including setting the work order complete.
- Cleanup: git worktree remove ../<project-dir>-wp-<nn>; git branch -d
  wp-<nn>-<slug>.
- Re-run PROPOSE: a merged dependency may have unblocked new candidates.

### RE-SLICE (when a lane halts with discovered scope)

- Backlog changes never ride story branches; they happen here, on main.
  Spawn software-engineering-team-product-owner with the approved brief and its
  bound planning skill per the develop flow's spawn template, the
  constitution pasted verbatim:

  {{constitution}}

- The re-slice passes a mini backlog gate and loads via item import.
  The affected lane then resumes with its
  corrected scope or aborts in favor of the new slices (its call, per
  the develop flow's re-slice rule).

### EXCEPTIONS

- Ownership overlap reported by a lane (the CLI refused set-ownership
  naming the holder): the lane parks itself (work-order release + item
  update --status planned). Requeue after the conflict clears: reopen a
  session in that worktree and set-status running; the CLI re-validates
  all three claims on reactivation and refuses if anything was taken
  meanwhile.
- Dangling lane: tell the human to reopen a session in that worktree;
  the request entry's pre-flight offers Resume there. Park it instead
  (work-order release from here) only when the human abandons the lane.
- Stale in_development story with no active order (item ready reports
  it): reset it (item update --status planned) after confirming no lane
  session still works that directory.
- Database lock under concurrent sessions: every CLI mutation is one
  short transaction; on a "database is locked" nonzero exit, retry the
  same call once, then halt per rule 4.
