# Mechanical Pass Tier

These are the instructions of process switch `mechanical_pass_tier` at
`mechanical`. A task binds this file only when the project's Process Policy
selects that value; at the default, `role_tier`, every writer pass and
compiler step runs as its flow and role files describe. Where this file and a
flow differ on a step that names switch `mechanical_pass_tier`, this file
governs.

A mechanical pass decides nothing. It does work whose result the returned
findings, a compiler or an approval already fix, so it runs on a lower tier or
without a role. The review before it runs as switch `review_panels` selects,
and every review, re-check and calibration keeps its roles and tiers.

## Pass kinds

| kind | work | runs as |
|---|---|---|
| `apply_findings` | applies the fixes a returned review verdict names in the owning writer's documents, each finding stating its exact change | the owning writer's `-mechanical` variant |
| `render` | a compiler's render verb for generated views | a direct entry command |
| `stamp` | a compiler's relation, confirmation or approval verb, after every gate the flow puts before it | a direct entry command |
| `check` | the owning compilers and vault gate, reported as returned | a direct entry command |

Never mechanical: authoring or rewriting text no finding dictates, design or
a choice between alternatives, triage of findings, code and architecture
repairs, and every review, re-check or calibration.

## Writer variants

| owning writer | variant | documents | flow |
|---|---|---|---|
| `product-owner` | `product-owner-mechanical` | backlog source and review notes | Backlog Planning |
| `qa-engineer` | `qa-engineer-mechanical` | Verification Contract | Operation |
| `devops-engineer` | `devops-engineer-mechanical` | Environment Contract | Operation |
| `solution-architect` | `solution-architect-mechanical` | Solution landscape, components and decisions | Solution Design |

A variant is its writer: the same body, boundaries and identity on the
`mechanical` tier, never a separate fixer, so writer ownership is unchanged.
Every other role, and every other flow's writer, keeps its own tier. A
variant never reads for a review, re-check or calibration: the Operation
counterpart that reviews the other contract runs as its base role.

## Dispatch

1. Wait for every reader, as the flow requires. The pass that resolves the
   returned findings is `apply_findings` only when each finding it must
   resolve names its exact fix, every fix stays inside the owning writer's
   documents, and the pass writes nothing the findings do not dictate: no
   review-note evidence or conclusion, new section, assumption, question,
   decision, criterion or scenario of its own. Otherwise the base writer runs
   the whole pass as the flow describes.
2. Derive the variant's manifest exactly as the flow derives the writer's,
   with the base role, `--mode revise` and `--skill challenge-review`, so the
   task binds this file. Give the variant that manifest, the constitution, the
   returned findings verbatim with their ids, severities, anchors and fixes,
   and the exact documents they change. Pass no conversation history and no
   earlier writer transcript. Name the pass kind first in the task
   description, for example `apply_findings Verification Contract`.
3. The variant applies each named fix as written, takes the disposition `fix`
   for it where the flow records dispositions, runs the owning compiler check
   and fixes only the compiler findings its own edits caused. It returns the
   changed paths and, per finding, `applied` or `returned` with the reason. It
   returns a finding instead of applying it when the fix is ambiguous,
   conflicts with another finding or a source, needs a document outside its
   scope or needs any judgment excluded above. It never changes a severity,
   never disputes a finding and never edits text no finding names.
4. A returned finding goes to the base writer, which triages and resolves it
   in one normal pass.
5. The flow's re-check is unchanged: the affected reviewer confirms each fix
   and reads the changed text. A fix it finds incomplete or wrong is rework
   and goes back to the base writer.

A `render`, `stamp` or `check` step runs as a direct command of the entry with
the flow's exact arguments. The entry spawns no role for it and passes the
result on: compiler findings go to the owning writer under step 1's rule.
Every gate before a stamp stays, including the reader barrier, the
`--expected-hash` recheck and the owner's approval. A command the flow gives a
writer inside its own pass stays in that pass.

## Measurement

The project owner runs this outside every task; no role acts on it. The
`mechanical` tier's host settings in `platforms/<host>/execution-profiles.json`
are starting values that the frozen-task A/B sets before a project selects
`mechanical`.

1. Freeze each task from Git history: the commit before an accepted fix
   pass, the review findings that pass applied, and the accepted fix commit
   as the reference result. Use Operation contract and backlog fix passes.
2. Run each task once per candidate on an unchanged checkout: the base writer
   as it ran, resumed where its session still exists, and fresh `-mechanical`
   candidates at each setting under test. Pin a candidate's effort with the
   session-level setting the host contract names; where role files keep their
   own effort over the session's, apply the `inherit` execution profile in a
   scratch copy first.
3. A fresh, read-only judge on the strongest tier compares each result with
   the accepted fix: every finding applied, no unrelated edit, compilers
   green. Record wall time and output tokens per run.
4. The cheapest candidate that applies every finding correctly with no
   unrelated edit sets the `mechanical` rows in a package change.

After the owner selects `mechanical`, record per mechanical pass its kind,
wall time, output tokens, re-check outcome and any unrelated edit the re-check
found, and compare with the base writer's passes of the same kind. The
registry's promotion rule judges these over at least 3 Deliveries.
