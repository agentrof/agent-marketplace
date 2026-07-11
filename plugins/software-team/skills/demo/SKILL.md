---
name: demo
description: Pre-sales package builder. Produces a customer-ready, multi-screen, navigable single-file demo with realistic mock data; no code is written. An approved demo later seeds real development.
disable-model-invocation: true
---

# Demo

A product story the customer can click through: one self-contained file,
zero code.

## When to Use
- The user needs to SHOW a product to a customer or buyer before building
  it: pre-sales, concept approval, stakeholder pitch.

## Procedure

1. Pre-flight: read workspace/config.json (missing: route to the setup
   entry and stop).
2. Preconditions, in order:
   a. Approved brief for the topic; missing: run the business-analysis
      entry flow first (pre-sales briefs may leave technical sections
      thin; the skeleton is the same), then continue.
   b. Design master at workspace/docs/design-system/MASTER.md; missing:
      stop, say "no design system yet", route the user into the
      design-system entry, and continue here once it exists.
3. Execute ${CLAUDE_PLUGIN_ROOT}/flows/design.md in demo mode:
   directions, pick, refinement, then expansion of the chosen direction
   into a multi-screen navigable package: one self-contained file with
   in-file navigation, realistic placeholder data, zero external
   requests, opening standalone in a browser.
4. Commit the final package as workspace/demos/<slug>/demo.html. It can
   be sent to a customer as a single file, and it seeds real development
   later: request inherits the brief and the chosen direction.
