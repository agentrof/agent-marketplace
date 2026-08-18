---
name: solution-architecture
description: Landscape-level solution architecture expertise loaded by software-engineering-team agents. Use when evaluating technologies, platforms or products, deciding product-wide mechanisms (extension and customization architecture, runtime data placement, queueing, scale posture, intelligent-agent or workflow constructs), judging sustainability, cost and lock-in, framing build-buy-integrate verdicts, or documenting the solution landscape and its decision log.
exposure: internal
---

# Solution Architecture

**Given:** a landscape-level question, the analysis space's requirements and budgets, and the current solution tree.
**Produces:** an engagement study with an options matrix, decision records with alternatives and exit paths, and a landscape document the team plans and designs against.

## When to Use

- Loaded by the solution-design entry for interactive foundation sessions
- Evaluating a technology, platform, product or method choice
- Deciding topology, integration or orchestration constructs
- Judging sustainability, operability, cost trajectory or lock-in
- Framing what the team builds versus buys versus integrates

## Decision Territories

The recurring families of solution-level questions. Each is decided once at this altitude, recorded, and binds every component; the list is representative, not closed:

- **Extension and customization architecture:** how the product is extended without forking it: named extension points, hook and plug-in surfaces, decorator-style layering, what integrators and customers may override, and how customizations survive upgrades.
- **Runtime data placement:** what lives in memory versus a durable store, what is cached where, locality and consistency of hot data, and what volume growth does to each choice.
- **Asynchronous work and queueing:** what runs synchronously versus queued, ordering and retry semantics, backpressure, and where a workflow construct earns its place over plain queues.
- **Scale and performance posture:** how the solution degrades under high volume, which remedies apply at which thresholds (partitioning, read models, precomputation), and what the stated budgets make mandatory versus premature.
- **Intelligent-agent and workflow constructs:** how agent-based capabilities are orchestrated, why a given runtime or framework family is preferred, and what that choice costs in operability and exit.
- **Cost posture:** where the run-rate concentrates, which choices are usage-priced versus fixed, and what the cost trajectory looks like at the analysis space's stated scale.
- **Maintainability and evolution:** what keeps change cheap: seams, ownership, upgrade paths, and the ease of customization per deployment where the product demands it.

## Altitude Rule

This skill works at landscape altitude: components and their interplay, platforms, products, methods, topologies. Entities, fields, endpoints, code structure and per-story deltas belong to the software-architecture skill and the software architect. When an evaluation needs that depth to decide, name the question and hand it down; never answer it here.

## The Dimension Set

Every evaluation scores its options against all six dimensions; a skipped dimension is stated and justified, never silent:

1. **Requirement fit:** which cited requirements and quantified budgets the option satisfies, misses or exceeds.
2. **Sustainability and operability:** who runs it, how it fails, how it is observed, what keeps it healthy in year two.
3. **Team capability:** what the team can build and operate today within its configured stacks; learning cost is a real cost.
4. **Cost and lock-in:** acquisition, run-rate trajectory, and what leaving costs; pricing-model risk is named.
5. **Security and compliance:** trust boundaries the option moves, data it holds, obligations it creates.
6. **Evolution and exit:** the named exit path; a component that cannot be replaced or retired was never evaluated, only adopted.

## Verdict Rules

- Every verdict names its strongest rejected alternative; "no alternative exists" is a claim that must be defended, not a default.
- Build, buy or integrate is explicit per component. Built components stay within the configured stacks; a verdict needing a new stack routes to the configure entry and arrives as a maintainer release, never as a silent exception.
- Prefer boring proven pieces; novelty buys its place with a named, measured advantage.
- Unquantified constraints escalate to the owner; an assumed number is a defect in the record.
- Decision records follow the software-architecture skill's decision-records mechanics (Y-statement or full record; supersede, never edit); solution decisions live as individual notes under the tree's decisions/ directory, and decision-log.md is the generated index.

## References

- [evaluation-method](references/evaluation-method.md): the options matrix shape, dimension scoring, the ungrounded-engagement rule, worked example. Read when starting or reviewing an evaluation.
- [landscape-docs](references/landscape-docs.md): the solution tree's doc contract (files, mandated sections, fold-in rules). Read when creating or updating any solution-design document.
- [challenge-lenses](references/challenge-lenses.md): the four adversarial lenses and exact read-only challenge input/output shape. Read when approaching the final solution approval gate.
- [worked-engagement](references/worked-engagement.md): a complete miniature engagement, framing through fold-in; the calibration bar for depth and format. Read when writing a project's first engagement or judging whether a study is deep enough.

## Related Skills

Pairs with the software-architecture skill downstream (approved landscape decisions constrain its per-story deltas), with the product-planning skill (build-buy-integrate verdicts shape slicing) and with the docker-compose skill (the landscape's service and store choices eventually materialize as environment definitions).
