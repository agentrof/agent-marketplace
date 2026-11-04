---
name: issue-report
description: Diagnose and locally verify a plugin repair, then prepare an anonymous linked upstream issue and pull request.
exposure: entry
project_scope: external
---

# Issue report and repair

## Outcome

A reproducible plugin problem, an optional locally verified source repair and an
anonymous upstream contribution under the user's publication authority.

## When to use

Use for a confirmed plugin defect or repeated process friction. Project defects
belong to the consuming project's backlog. Group duplicate symptoms by cause.

## Inputs and authority

Capture expected and actual behavior, package identity, host and a minimal
reproduction. Read the Marketplace repository's instructions before changing it.
Consumer code, identities, local paths, logs and business data stay private.

## Method

1. Separate observation from hypothesis. Check the smallest decisive reproduction.
   Continue unrelated delivery while a scoped repair worker investigates.
2. For an authorized repair, use a separate source checkout and modify canonical
   sources. Follow [local repair](references/local-repair.md) to build and load an
   identified development candidate. Preserve the installed release's provenance.
3. Verify the original failure through its normal entry point and the affected
   host/package checks. Keep consumer evidence local. A candidate that only passes
   an isolated script test has not proved the full repair.
4. Prepare synthetic reproduction, root cause, source diff and verification.
   Scan all outgoing text and files for consumer identity and secrets. Run
   `file_issue.py --preview --title <title> --project-root <consumer-root>` with the
   body on stdin. Review its exact payload and digest before publication.
5. Under explicit publication authority, file the approved payload once with
   `--approved-payload-sha256`. Open the verified source PR under the Marketplace
   protocol and link the returned issue. An uncertain network result requires
   reconciliation before retry. Preserve review and merge authority boundaries.

## Evidence and completion

Report local regression evidence, the loaded candidate identity and the actual
issue/PR URLs. Restore the intended installed package after the trial. Failed or
incomplete trials remain visible and cannot be reported as verified fixes.

## Conditional references

- [Local repair](references/local-repair.md) covers candidate loading and upstream handoff.
