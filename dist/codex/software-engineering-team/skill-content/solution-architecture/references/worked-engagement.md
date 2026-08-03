# Worked Engagement

A complete miniature engagement, framing through fold-in: the calibration bar for depth and format. Component and product names are generic archetypes; a real engagement names real candidates.

## engagements/order-event-distribution.md

```markdown
# Order Event Distribution

## Summary
Status: approved 2025-11-04
How order events reach the fulfillment and notification components
without coupling them to the order service. Decided: managed streaming
service, integrate verdict; minted
[[solution-design/decisions/order-event-distribution-decision|SD-007]].

## Framing
- Question: one distribution mechanism for order events, consumed by
  two components today, more expected.
- Out of scope: event payload schema (software architect), retention
  policy for analytics (no cited requirement yet).
- Components touched: order service (build), fulfillment worker (build),
  notification sender (build), the distribution mechanism (this verdict).
- Citations: [[business-analysis/shop/domains/orders/rules/order-events|BR-ORD-014]]
  ordering guarantee per customer;
  [[business-analysis/shop/budgets#^event-volume|throughput budget]]
  peak event volume.
- Dimension priority: requirement fit, then sustainability and
  operability, then cost and lock-in; the rest tie-break.
- Horizon: structural (consumers multiply); full matrix.

## Options
| Option | Requirement fit | Sustainability | Team capability | Cost and lock-in | Security | Evolution and exit | Verdict |
|---|---|---|---|---|---|---|---|
| Managed streaming service | Ordering per key meets BR-ORD-014 (vendor doc "Ordering semantics", fetched 2025-11-04) | Vendor-operated; mature | No new stack; client library only | Usage-priced at stated volume, 3-year horizon; egress cost ASSUMED | In-region storage (vendor doc, same fetch) | Standard protocol; consumers portable | LEAD |
| Self-hosted broker | Meets BR-ORD-014 (own benchmark needed: UNDECIDABLE without spike) | Team operates; on-call load named | New operational skill | License-free; ops run-rate UNVERIFIED | Full control | Portable; exit is ops divestment | REJECTED: sustainability |
| Store-as-queue on the existing database | Polling meets volume budget only at 10x cost (measured against budgets.md figure) | Reuses incumbent | Zero new pieces | Cheapest short-term; scales worst | Incumbent posture | Trivial exit | REJECTED: requirement fit at stated scale |

Incumbent check: the existing database was evaluated (row 3) and does
not serve at the cited volume; this is not a solved territory.

## Verdict
Managed streaming service, integrate. Strongest rejected alternative:
self-hosted broker; deciding dimension: sustainability and operability
(no operator on the team). Egress cost stays a named risk on
[[solution-design/decisions/order-event-distribution-decision|SD-007]]. Deferred:
analytics retention, revisit when a requirement cites it.
UNDECIDABLE benchmark dropped:
the spike became unnecessary once the lead option's vendor ordering
semantics verified against BR-ORD-014.
```

## decisions/order-event-distribution-decision.md

```markdown
---
type: decision
title: Order event distribution via managed streaming service decision
status: accepted
owner_role: solution_architect
decided_at: 2025-11-04
territory: asynchronous work and queueing
revisit_trigger: event volume crossing 5x the cited budget, or the vendor's pricing model changing
engagement: "[[solution-design/engagements/order-event-distribution]]"
tags:
  - doc/decision
  - status/accepted
aliases:
  - SD-007
---

# Order event distribution via managed streaming service decision

**Decision:** In the context of distributing order events to multiple
consumers, facing BR-ORD-014's per-customer ordering guarantee and the
event-volume budget, we chose a managed streaming service (integrate)
and neglected a self-hosted broker, to achieve ordering and throughput
without an operations burden the team cannot staff, accepting
usage-based pricing and an ASSUMED egress cost carried as a risk.
**Rests on:** [[business-analysis/shop/domains/orders/rules/order-events|BR-ORD-014]],
[[business-analysis/shop/budgets#^event-volume|event-volume budget]]
**Exit path:** standard protocol; consumers re-point to any compatible
broker; data replay from the order store.
**Sustainability:** vendor-operated; team touches client config only.
**Risks:** egress cost ASSUMED, verify on first invoice.

## Baglantilar <!-- sec: nav -->
[[maps/solution-design|Solution Design]] -
[[solution-design/engagements/order-event-distribution|engagement]] -
[[solution-design/landscape|Landscape]]
```

Status, decided_at, the tag mirror and any supersede chain are written
by stamp-decision, never typed. The generated decision-log.md gains its
row at the next render-decisions run:

```markdown
| [[solution-design/decisions/order-event-distribution-decision\|SD-007]] | Order event distribution via managed streaming service decision | accepted | asynchronous work and queueing | 2025-11-04 | event volume crossing 5x the cited budget, or the vendor's pricing model changing | |
```

## landscape.md fold-in excerpt

```markdown
| component | verdict | decision | engagement | status |
|---|---|---|---|---|
| event distribution | integrate | [[solution-design/decisions/order-event-distribution-decision\|SD-007]] | [[solution-design/engagements/order-event-distribution\|order-event-distribution]] | decided |
```

Transition gains a step citing SD-007 (an aliased wikilink, escaped-pipe in table cells): adopt event distribution before the fulfillment story lands; precondition: none.

## One challenge finding and its disposition

Round 1, cost-and-lock-in lens: "Egress cost is ASSUMED and the verdict's 3-year horizon depends on it; no source named." Disposition: fix; the risk moved onto SD-007 as a named risk with a first-invoice verification note, and the matrix cell now marks the assumption explicitly. Recorded in reviews/order-event-distribution-round-1.md.

## Why this is the bar

Framing pinned scope, citations, priority and horizon before candidates; three options including the incumbent; every cell carries a source, a measurement or a marked assumption; the UNDECIDABLE outcome appeared and was resolved honestly; the record carries exit, sustainability, revisit trigger and risks as fields; every citation is an aliased wikilink edge; the landscape shows outcomes only. An engagement thinner than this on a structural question is below the bar.
