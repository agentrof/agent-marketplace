# Source Decision Gate

These are the instructions of process switch `source_decision_gate` at
`one_gate_when_drafted`. A task binds this file only when the project's
Process Policy selects that value; at the default, `two_gates`, a decision
that changes approved analysis documents asks the owner for a direction
first and for the exact change after it is drafted and reviewed. Every task
of the Business Analysis and Backlog Planning flows binds it, and only the
orchestrating entry asks the owner.

A choice pick sets a direction only and never approves a write. This value
keeps that rule: the owner approves exact content that the gate shows, never
a summary of it.

## When one gate applies

A source decision is a question whose answer changes approved documents of
a business-analysis space, such as a gap a backlog review cannot close
without a source revision. One gate applies only when the recommended
option can be drafted without the owner's input: the recommendation names
every changed row and needs no fact, number or preference only the owner
holds. Otherwise ask as at `two_gates`.

## Steps

1. Open the revision with `ba_compile.py begin-revision` for every document
   the recommendation changes, have the Business Analyst draft it, run
   `check` and `render`, and run the flow's independent challenge on the
   changed rows until no blocking finding remains.
2. Run `ba_compile.py content-hash --space <space>` and keep its
   `content_hash`.
3. Ask one choice-gate question. Its first line states in everyday words
   what the approval does, for example "change the scoring rule to the
   weighted sum shown, keep every other rule, then continue the backlog".
   Then list the options with their tradeoffs; the recommended option comes
   first and is "approve this exact change", with the reviewed diff of every
   changed document, `git diff` of the space, the review result and the
   content hash. The other options are the alternatives the decision has and
   "change direction".
4. On "approve this exact change", move each changed document through
   `enter-review` and `approve`, then run `approve-package --expected-content-hash
   <content_hash>`. It refuses when the content differs from what the owner
   saw; then show the owner the new diff and ask again. Name the approved
   content hash in the commit message of the approved package.
5. On any other answer, fall back to `two_gates`: the answer is the
   direction, the draft is revised or discarded, and the exact change is
   shown and approved in a second gate.

Never approve a document the gate did not show, never change the content
between the gate and `approve-package`, and never treat silence or a
timeout as approval.

## Measurement

Record per source decision the owner gates asked, the minutes from the first
gate question to the approved write and whether the recommendation was
taken. The registry's promotion rule judges these.
