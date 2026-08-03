---
name: build-docs-vault
description: On-demand reorganization of the whole workspace docs vault. Full-width audit, an owner-gated rename batch, deterministic migration, re-rendered generated views and in-session curation, closing on a green re-check. The vault librarian; scoped stewardship at each docs gate stays where it is.
disable-model-invocation: true
---

# Build Docs Vault

Reorganize the consuming project's docs vault as one deliverable: names,
titles, links, maps, nav and payload brought back to the vault law in a
single owner-gated pass. Deterministic rewrites run first through the
checker's verbs; judgment work happens in-session under the per-write
hook; nothing here ever changes what a document claims.

## When to Use
- The owner asks for a full pass over the vault: naming, graph labels,
  maps, navigation or payload have degraded beyond one subtree.
- The setup entry found pre-existing content findings and routed them
  here as vault degradation.
- NOT for scoped repair: each docs-producing entry's stewardship still
  repairs its own subtree at its own gate. An optional focus request
  narrows step 4c curation only, never the audit.

## Procedure

1. Pre-flight.
   - Load the obsidian-vault skill: it owns the title, alias, naming and
     hub law this entry restores, and every write below follows it.
   - Read workspace/config.json for project_key and output_language;
     missing or without a project_key: stop and route to the setup
     entry. output_language governs curated map, nav and home
     prose; the machine layer stays English.
   - Resolve the PMO CLI and the dispatcher ("$RUN", "$TEAM") per the
     develop flow's state contract; scripts below run as
     "$RUN" run "$TEAM" scripts/<x>.py. An
     unresolvable PMO only skips the event pulses below and
     is named honestly as a residual at close. Freeze truth never
     depends on it: the migrate verb globs
     workspace/work-orders/*/freeze.json mechanically before any write.
   - When the PMO resolves, read resume-info --project-key <key> --json:
     the active work orders explain the frozen docs behind any deferred
     rename when the gate is presented.
2. AUDIT, always full width, never --scope.
   - "$RUN" run "$TEAM" scripts/vault_check.py check --vault
     workspace/docs --json for the finding inventory (designation_drift
     findings inventory stale or double-suffixed titles).
   - "$RUN" run "$TEAM" scripts/vault_check.py migrate --vault
     workspace/docs --rename --dry-run --json for the rename plan: old
     and new names, per-rename referrer counts, the manual list and the
     blocked entries (blocked_by_frozen_referrer) with their blocking
     paths.
   - When the config map exists, a no-op-change designation dry run
     (reconcile-designations --set <type>=<current value> per type,
     --dry-run --json) surfaces the retitle, manual and locked lists
     mechanically.
3. PLAN and GATE, asked through the AskUserQuestion popup (recommended
   option first, tradeoffs in every description).
   - The rename batch is an explicit user choice: present every planned
     rename with its new name and referrer count, then ask Approve
     batch / Skip renames / Adjust list.
   - Blocked-by-frozen renames are shown as deferred, never as
     approvable options: they wait for their work orders to close.
   - When the designation dry run lists locked records, the relabel
     batch is its own question: relabel (PMO-audited, title and H1
     only, recommended) or leave them as named warnings.
   - The curation program is summarized per area (maps, nav peers,
     titles and aliases, home localization, empty maps)
     and asked as Approve / Adjust / Pause.
4. EXECUTE. Mint the designation map first WHEN ABSENT: render the
   canonical table (obsidian-vault metadata) into output_language,
   owner-approved, and write it through reconcile-designations (one
   --set per type, --actor build-docs-vault): the designation keys are
   hook-guarded with the verb as sole writer (present, leave it; stale
   titles heal through the same verb, approved above, BEFORE migrate so
   the append step never meets a retired tail). Then deterministic
   first.
   a. "$RUN" run "$TEAM" scripts/vault_check.py migrate --vault
      workspace/docs, freeze-safe by construction: citation cells,
      scalar doc-ref keys, nav hub retargeting, de-id-leading of
      decision titles and H1s, appending each typed title's missing
      designation and its H1 (challenge records excepted, retitled by
      judgment), deletion of notes the law has retired
      (legacy scaffold and delivery-view files, with home's links to
      them stripped; runs unconditionally, even under --scope) and
      payload reconciliation. Then the approved batch via
      migrate --vault workspace/docs --rename: every referrer is
      rewritten vault-wide in the same operation.
   b. Re-render every generated surface:
      "$RUN" run "$TEAM" scripts/vault_check.py render-decisions
      --vault workspace/docs;
      "$RUN" run "$TEAM" scripts/ba_compile.py render --space
      workspace/docs/business-analysis/<slug> for EVERY analysis space,
      because a post-rename registry must match the new filenames or
      alias ownership checks poison. Materialize a subtree's map seed
      only when its tree bears content: maps are born with their tree.
   c. Judgment in-session, every write under the per-write hook: curate
      map notes (grouped, annotated, output_language), fix contextual
      nav peers, stamp titles and aliases per the law, retitle the
      challenge records the reconcile verb listed as manual (a round
      number not closing the title is judgment; canonical tails
      transition mechanically) and anything auto-append could not
      shape, sync home to vault reality and localize its prose, and
      settle each empty map (fill or retire) with owner approval.
5. CLOSE.
   - Re-run the full check: green, or every residual named with its
     reason (frozen docs, unresolved PMO, deferred judgment).
   - Summarize the changed files, then pulse the database via the PMO
     CLI: event append --project-key <key> --action vault_reorg (when
     the CLI resolved).
   - Content gaps discovered on the way route to their owning entries
     (business-analysis, solution-design, design-system); this entry
     never writes prose meaning.

## Hard Scope

- The ONE entry allowed to write vault-wide: the whole docs tree plus
  its committed payload, never a byte outside workspace/docs/ save the
  owner-approved reconcile-designations invocations (mint and heal),
  the designation map's single writer.
- The PMO database is touched only through event append; no other
  state writes.
- Prose meaning stays untouched: reorganization is names, links,
  metadata, maps and payload, never content claims.
- Renames go only through migrate --rename, the rename-as-migration
  runbook; a shell move bypasses referrer rewriting and is a defect.
