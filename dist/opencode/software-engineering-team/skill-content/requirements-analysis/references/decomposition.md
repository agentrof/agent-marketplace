# Decomposition: How a Topic Becomes a Tree

The method for splitting an enterprise-scale ask (an ERP, a banking
core, a hospital system) into the analysis space's node tree, and for
deciding where any new fact lives. The space standard defines the legal
shapes; this reference decides WHICH shape.

## Space-first, domains-on-evidence

1. Always create the space skeleton first (the five root files, status
   draft). Never pre-create domains from the org chart, a module list or
   a competitor's menu: domains are discovered from evidence, not
   assumed from structure.
2. Run the purpose and actor rounds at space level; seed the glossary
   and the actor roster as the answers land.
3. Start ALL content analysis in the root node. A small feature ends
   where it started: one node and one gate.
4. Split a domain out only when the project decision authority approves a split proposal:
   a signal NOMINATES the split, the owner's explicit approval (asked
   through a choice gate) creates it. The tree is a
   consequence of the analysis, never a promise made before it.

## Domain split signals

Judgment signals, each with the question that tests it:

- Vocabulary conflict: the same term needs two definitions for two actor
  groups ("order" to sales versus "order" to procurement). The glossary
  is the tripwire: a term acquiring a second meaning names the split
  candidate.
- Data ownership boundary: a different actor group is the source of
  truth for a record cluster. Test: "when this record is wrong, who
  fixes it?"
- Cycle boundary: a record cluster lives on a different business
  calendar (month-close versus continuous stock movements).
- Compliance boundary: a regulation names a subset (payroll,
  e-invoicing) whose rules change on an external schedule.

No document count, rule count, nesting depth or line count automatically
nominates a split. A split remains a human decision grounded in the semantic
signals above. When a process no longer has a coherent owner or lifecycle,
record the split rationale and obtain the owner's explicit approval.

When a domain splits out: move its docs, re-mint their ids under the new
code per the space standard's runbook, and record the split as a
decision doc naming what moved and why.

## When a process doc splits

- Split by workflow stage (goods-receipt versus put-away) or variant
  (standard versus consignment receipt) when one process is no longer a
  comprehensible coherent flow.
- A process spanning two domains does not pick a side: it moves to the
  deepest common ancestor node (usually the root) and links into both
  domains' entities and rules.

## When an entity earns its own page

An entity starts as a row in its domain overview's data notes table.
Promote it to entities/<slug>.md when ANY of these holds:

- it has lifecycle states (the constitution's data-lifecycle mandate
  then also requires a state machine and a lifecycle rule_set);
- it is referenced from more than one independent process context;
- its fields participate in rules owned by distinct business concerns;
- any of its fields freeze at issue time or propagate on update
  (propagation semantics need the propagation section).

Never demote. A promoted page that shrinks to nothing is superseded
explicitly, successor named.

## Rule_set and acceptance_set cuts

- One rule_set per governed target: one entity lifecycle, one process's
  constraints, one decision-table cluster. Split by target when concerns are
  unrelated.
- One acceptance_set per process doc by default; split main flow versus
  exceptions when the scenarios serve distinct business outcomes. Cross-entity scenarios
  live at the deepest common ancestor node.

## Where does a new fact live? (the routing test)

Run in order; first match wins:

1. A definition of a term: glossary row.
2. Who may do something: actor roster (the vocabulary) plus a BR row in
   the governing rule_set (the constraint).
3. A quantified quality expectation: budgets.md row (space or domain).
4. A step or exception in how work flows: the owning process doc.
5. A field, freeze or propagation fact: the owning entity page.
6. A testable constraint: a BR row in the rule_set governing its target.
7. How the built thing will be judged: an AC row citing its BRs.
8. A choice between alternatives, once ruled: a decision doc.
9. Unconfirmed inference: an AS row where the inference was made.
10. Unanswered and blocking: an OQ row where the gap was found.

A fact with two plausible homes gets ONE home and a link from the other
place. Restating is a defect the zero-duplication rule already names.

## Session resume protocol (long-running analyses)

An enterprise analysis spans many sessions and compactions. The
generated views are the working memory; conversation is not.

- On every entry: read _generated/status.md and
  _generated/open-questions.md first, then read ONLY the target domain's
  subtree fully; everything else summary-only via _generated/index.md.
- Before every exit: flush open questions and assumption rows into their
  owning docs, run check + render, commit. A fact that exists only in
  conversation does not exist.
- Parallel analysts partition by domain: one owner per domain at a time.
  Two sessions minting in the same domain collide on id_unique at merge,
  loudly, by design.
