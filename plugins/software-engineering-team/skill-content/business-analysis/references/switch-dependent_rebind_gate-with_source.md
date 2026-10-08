# Dependent Rebind Gate

These are the instructions of process switch `dependent_rebind_gate` at
`with_source`. A task binds this file only when the project's Process Policy
selects that value; at the default, `separate`, an approved Experience
package that a source approval makes stale is found while bindings are
refreshed and gets its own scope gate. Every task of the Business Analysis,
Solution Design, Design System and Experience Design flows binds it, and only
the orchestrating entry asks the owner. A source is a Business Analysis
space, the Solution landscape or the Design System MASTER.

The Experience Design rule stays: the owner approves the complete action set
before any lifecycle mutation. This value shows that set in the source gate
instead of after it.

## Before the source gate

1. With the source change drafted in the working tree, run
   `experience_compile.py source-impact --root workspace/docs/experience-design
   --source-ref <source>`, where `<source>` is `business-analysis/<space>/space`,
   `solution-design/landscape` or `design-system/MASTER`. It lists every approved
   Experience package that binds the source's current receipt, the changed
   source rows and documents, the package notes that cite them, and per
   package `rebind`: `mechanical` when no note cites a changed row or
   document, else `semantic`.
2. A `mechanical` dependent joins the gate's action set as one update: move
   the package's source binding to the new source receipt and keep every
   record, revision, behavior and prototype byte. A `semantic` dependent is
   named in the gate as a later Experience update with its own gate, never
   approved here.
3. Name the Requirement and backlog bindings that must advance after the
   Experience receipts, through their normal revisions.

## The gate

Open the question with one sentence in everyday words, for example "approve
the scoring rule change, then update the design package's source link to
it, keeping every screen and behavior, and continue the backlog". Then show
the source change as its gate requires, each mechanical rebind with the
`source-impact` evidence and each semantic dependent that will follow.
Approving the gate approves the source change and every mechanical rebind
in it: their proposals, their final snapshot review and their atomic
publication.

## After approval

1. Approve and commit the source as its flow requires.
2. For each approved rebind, run `propose` with the update action. When its
   action set is exactly the approved rebinds and nothing else, it needs no
   second owner gate: begin the revision, change no record or artifact, run
   `source-impact` again, and continue only while it reports
   `package_change` `source_rebind_only` and `rebind` `mechanical`.
3. Otherwise stop and ask the owner through the Experience Design flow's
   own gate. Never widen the approved set, and never let an authored record
   or artifact change ride on this approval.

## Measurement

Record per source change the owner gates opened after the source approval
for mechanical rebinds and the minutes from the source approval to the
backlog rebind. The registry's promotion rule judges these.
