# Root Review Scope

These are the instructions of process switch `root_review_scope` at
`revision_delta`. A task binds this file only when the project's Process
Policy selects that value; at the default, `full`, the root reader reads the
complete package as the flow describes. Where this file and the flow differ
on what the root reader reads, this file governs.

The root review stays the backlog's cross-story gate: it still covers
cross-epic overlap, dependency direction, cycles, delivery sequencing, shared
contracts, deferred criteria and global test coverage, and backlog approval
still checks the whole backlog. Only what the reader reads in full changes.

## The revision delta

`backlog_review_inputs.py --root` derives the delta from the approval stamps
of the previously approved revision:

- `changed`: every story whose story or test plan is new or no longer
  carries the approval stamp of its bytes. A changed dependency edge changes
  the story that declares it.
- `neighbours`: every story one dependency edge away from a changed story,
  in either direction.

The manifest names `root_review_scope: revision_delta` and records
`revision_delta` with both lists, `share_percent`, the owner's
`max_delta_share_percent` and `read`.

## What the reader reads

At `read: delta` the manifest's paths are the backlog root, every epic, the
changed and neighbouring stories with their test plans and the sources they
link to, the current review of each epic and the root review notes. Every
other story and test plan is read only as its summary in
`check.backlog_graph`: epic, title, criteria, scenario count, dependencies and
the `sha256` of its story and test plan, which the manifest's hash binds.
`check.backlog_graph.dependency_edges` holds every edge of the backlog, and
the rest of `check` holds the compiler facts for the root review note:
counts, the audit of its declared against expected relations, each story's
source-to-scenario map and its pending findings. A reader takes these facts
as given and never recounts them; a stale count, a missing dependency in the
root record or a wrong deferral owner is the compiler's to report.

The reader reads the whole package, at `read: full` with the `reason`
recorded, when:

- the backlog has no earlier approved revision;
- the delta holds more than `max_delta_share_percent` of the stories;
- a reader asks for it with a stated reason. Rerun the reader with
  `--full-root-reason "<reason>"` on `backlog_review_inputs.py --root`, or
  on its `task_inputs.py --epic` task, and record the reason in the root
  review note's Scope evidence.

## Review

- A reader that suspects an overlap between a delta story and an unchanged
  story's summary, or needs an unchanged story's text for any other
  cross-story judgment, names that story and why. Add it to the reader's
  task with `--input` and rerun that reader; never infer the story's content
  from its summary.
- A dependency cycle, a duplicate id or an unknown dependency target fails
  the manifest before any reader starts, as it does at `full`.
- Recompute the manifest with `--expected-hash` as the flow says. Its hash
  binds every backlog byte, unchanged stories included, so any change to the
  backlog stales it, and a manifest taken under the other value is stale.

## Measurement

The project owner measures outside every task; no role acts on it. Per
revision, record the root review's wall clock, input tokens per reader,
manifest paths and bytes, every full-read fallback with its reason and every
reader request for an unchanged story. Replay the revision as a frozen `full`
root review and compare the verdict and findings, and seed cross-story
defects, such as a new story that duplicates an unchanged story's scope and
a new edge that closes a cycle with an unchanged story, to confirm the delta
review reports them.
