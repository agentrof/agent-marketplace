---
name: challenge-review
description: Knowledge skill for the analysis challenge loop. Loaded by the business-analysis entry and its challenger and expert roles; not user-facing.
user-invocable: false
---

# Challenge Review

The adversarial refinement loop for analysis spaces: who challenges,
with what context, how findings return, and how they enter the space.

## When to Use
- Loaded by the business-analysis entry before a domain gate, and by the
  analysis-challenger and domain-expert roles it spawns.

## The Loop

1. Cast the panel: pick lenses from the lens bank sized to the domain,
   and cast topic-specific expert profiles; record both in the round
   record with a one-line why each.
2. Fan out: one fresh-context, read-only spawn per lens or profile
   (analysis-challenger role; domain-expert for named questions).
3. Triage: the analyst, the space's single writer, disposes every
   finding: covered / fix / assumption / question / rejected, each with
   its resolving target ids; the round record holds the full table.
4. Audit the burial paths: one fresh-context spawn re-judges only the
   covered and rejected dispositions against their cited evidence;
   disagreements are flagged in the record and surface at the gate.
5. Apply fixes, run the compiler's check and render, close the record
   locked. Verdict converged means zero blocking findings this round.
6. Rounds: 1 is mandatory per domain; run another only while the last
   round produced blocking findings; hard cap 3; residue becomes open
   questions at the gate. Cross-domain lenses run once at space level
   before the space closes.

## Core Rules

- Context isolation is the point: challengers get FILES ONLY (the target
  subtree read fully; the space overview, glossary and generated
  registry summary-only), never the authoring conversation. DON'T brief
  a challenger on what the analysis "means"; the documents must carry it.
- Spawned members are read-only by constitution and by capability; every
  space write in the loop is the analyst's.
- Severity travels untouched: the challenger's blocking/minor verdict is
  recorded as returned. A blocking finding forces the next round even
  when fixed this round; fresh eyes verify fixes.
- Expert output enters the space only as assumption rows (source
  labeled), open questions, or draft edits awaiting the gate; a proposal
  becomes a fact only through the owner's ruling.
- DON'T pad rounds: a small domain takes a small panel (the lens bank
  names the floor); zero findings is a legal, welcome round.

## References

- [lens-bank](references/lens-bank.md): the standard lenses with their question stems and panel sizing. Read when casting a round's panel.
- [expert-casting](references/expert-casting.md): how to write a grounded expert profile for a topic and what to ask it. Read when a domain needs practitioner knowledge the owner cannot supply.
- [triage](references/triage.md): disposition semantics, the triage audit, and the round record's shape. Read when findings return from a round or when closing a record.
