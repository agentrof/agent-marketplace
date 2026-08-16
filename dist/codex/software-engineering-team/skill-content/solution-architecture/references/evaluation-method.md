# Evaluation Method

How an engagement turns a landscape question into a defensible verdict. The
unit of work is the options matrix; the committed final document is the current
reasoning trail and Git retains prior revisions.

## Framing First

An engagement doc opens by pinning, before any option is named:

- The question, in one sentence, and what is explicitly out of scope.
- The components of the landscape it touches.
- The cited requirements and quantified constraints; what the decision must satisfy is fixed before candidates enter. Requirement ids follow the space's namespaced grammar (`BR-<CODE>-###` and kin) and are read from the space's `_generated/registry.json`, never from memory; budgets are cited as block-id wikilinks (`[[business-analysis/shop/budgets#^event-volume|volume budget]]`), since budget rows carry no ids.
- The dimension priority for this question: which dimensions dominate when they conflict, fixed before any candidate is named. A verdict that flips the stated priority to fit its winner is post-hoc reasoning; the tie rule reads the framing's priority first, and only a genuine residual tie escalates to the owner.
- The decision horizon: is this reversible next quarter or structural for years? Reversible decisions get lighter treatment; structural ones get the full matrix.

## The Options Matrix

One row per candidate, one column per dimension from the skill's dimension set, plus a verdict column. Cells carry a short judgment with its evidence or assumption marker, never a bare score:

```markdown
| Option | Requirement fit | Sustainability | Team capability | Cost and lock-in | Security | Evolution and exit | Verdict |
|---|---|---|---|---|---|---|---|
| Managed queue service | Meets BR-ORD-012 latency budget (vendor SLA) | Vendor-operated; mature | No new stack | Usage-priced; egress lock-in ASSUMED | Data stays in region (verified) | Standard protocol, portable consumers | LEAD |
| Self-hosted broker | Meets budget (benchmark needed) | Team operates; on-call cost | New operational skill | License-free; ops cost UNVERIFIED | Full control | Portable, higher exit effort from ops investment | REJECTED: ops cost |
```

- Two candidates minimum; a single-candidate matrix is a bias record, not an evaluation.
- Every ASSUMED or UNVERIFIED marker either gets verified before the gate or survives into the decision record as a named risk.
- The verdict row states the leading option AND its strongest rejected alternative with the deciding dimension; ties escalate to the owner with the tradeoff framed, never coin-flipped.
- When the deciding cell cannot be settled from any obtainable source, the honest verdict is UNDECIDABLE: the engagement parks with the named measurement question, the owner routes it as a spike story through product planning, and the engagement reopens when the measurement lands. Guessing the cell to avoid the park is the defect this outcome exists to prevent.

## Dimension Scoring Discipline

- Judgments cite their source: a requirement id, a budget link, a vendor document, a measurement, or an explicit assumption. Training-memory claims about products and pricing are assumptions until checked.
- Verification has one meaning: a source obtained in this session and named in the cell (a fetched vendor or standards document with title and date, a recorded measurement, or an owner statement at the gate). Clearing an ASSUMED or UNVERIFIED marker without a named source is a defect; training memory never verifies anything.
- Options are judged against the landscape that exists: a candidate duplicating a capability an adopted component already provides states why the incumbent does not serve; a second technology in a solved territory is sprawl until the matrix proves otherwise.
- Team capability is judged against the configured stacks and the team's recorded skills, not optimism.
- Cost includes the run-rate trajectory at the analysis space's stated scale, not the free-tier snapshot. Cost judgments state their horizon: structural decisions default to three years of run-rate, reversible ones to one.

## The Ungrounded-Engagement Rule

When no analysis space exists yet (pre-analysis groundwork), the engagement proceeds but every requirement citation becomes a named assumption in the doc's framing, and the doc carries the flag `ungrounded-by-analysis` in its Summary. Once the analysis space approves the relevant domain, the engagement's assumptions are re-verified against real ids and the flag is removed or the decision superseded. An ungrounded engagement never silently becomes a grounded one.

## From Verdict to Record

An accepted verdict produces, in the same session: the decision note under
decisions/ (alternatives, tradeoffs, exit path, sustainability judgment) and
the index re-render, the landscape.md update (component list,
build-buy-integrate verdicts, topology), and any handed-down questions for the
software architect named at the gate. If the engagement reopens, update the
same study to the new final truth and commit it with the affected outputs; Git,
not an in-document audit stream, retains the prior revision.
