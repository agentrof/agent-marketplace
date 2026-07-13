# Service Boundaries

How boundaries are found, mapped, and turned into ownership rows. A boundary decision changes the ownership map and the decision log; nothing in this file changes code.

## Bounded Contexts

- One context, one model, one vocabulary. Decision test: pick a core term ("order", "customer", "account") and ask whether it means exactly the same thing on both sides of the proposed seam. IF the meaning shifts, THEN a boundary belongs there and each side keeps its own model of the term.
- DON'T share one entity model across contexts to save typing; a shared model couples every consumer to every field change. Each context models only the fields it owns or reads.
- Language check inside a context: contract names, entity names, and flow names use the same terms the requirements use. A translation happening inside a context is the symptom of a hidden second context.

## Boundary Heuristics

| Heuristic | Test | Boundary verdict |
|---|---|---|
| Data ownership | Every field has exactly one writing module | A second writer means the boundary is wrong or a copy is undeclared |
| Change cadence | Which files moved together across recent deltas? | Things that change together belong inside one boundary |
| Transaction scope | Does an invariant require atomic updates across the seam? | An invariant never spans a boundary; redraw, or declare eventual consistency |
| Vocabulary shift | Same term, different meaning on each side | Boundary between the meanings |
| Audience split | Do the two sides answer to different principals or tenants? | Split where the audience splits |

- The transaction rule is the hard one. IF two writes must be atomic, THEN they live in one boundary over one store. IF they may be eventually consistent, THEN the seam is legal and the staleness tolerance is declared in the decision that draws it.

## Context Mapping

Every seam in the ownership map names how the two sides relate. The options, in preference order:

- Anti-corruption layer (default for a foreign or unstable neighbor): the consuming context defines a local type holding only the fields it consumes and translates at the seam. DO put the translation in the consumer, at the seam. DON'T import the neighbor's entities; a neighbor's model change must never compile-break the consumer's domain.
- Open host: the owning context publishes one stable contract for all consumers, who adopt it as published. Choose when many consumers read the same stable model; the contract then carries the versioning stance from the rulebook.
- Shared kernel: a small sub-model shared and co-governed by exactly two contexts. Every shared type names one owner and a change protocol. Growth by convenience is the failure mode: any addition to the kernel is a recorded decision, not a shortcut.
- Conformist (downstream adopts the upstream model wholesale): last resort, when translation costs more than the coupling. Record it with the exit condition that would justify an anti-corruption layer.

## Monolith-First Decomposition

- Extract along an existing, enforced module boundary only. IF the boundary does not hold inside the monolith (cross-module table reads, imports of internals), THEN fix the boundary first; distribution multiplies boundary defects, it never fixes them.
- Extraction order: the module with its own data, the fewest inbound dependencies, and the recorded forcing symptom. DON'T extract the core domain first; peel from the edges.
- Strangler sequence: route the seam through the declared interface, move the capability behind it, run old and new paths in parallel until parity is verified, then retire the old path. Every step is reversible until the last; a step that cannot be rolled back is two steps merged.
- Data moves with the module: an extracted module takes its tables or collections along. DON'T leave two deployables writing one store; that is a second writer, an ownership map violation.

## From Boundaries to the Ownership Map

- Each boundary yields exactly one owner row: module or file group, owning role, and the interface contract named at every seam.
- Every seam also names its context-mapping relation (anti-corruption, open host, shared kernel, conformist) so the reviewer can check imports against the declared relation.
- No overlaps: a file group appearing in two rows blocks parallel implementation. Resolve the overlap before handing the map to the flow.
- Copies across rows are snapshots: any field read from another row's data and stored locally carries the constitution's snapshot declaration (refresh path, staleness tolerance), recorded in the decision log.
