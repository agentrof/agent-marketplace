# Epistemic Loop Spec

Shared by every deep primitive. Fixed intensity, no tiers. Loop runs in caller's chat context (transparency over isolation). All artifacts persisted under `.run/<uuid>/artifacts/iter-<N>/`.

## Constants

- **Max iterations**: 10
- **Questions per iteration**: 10
- **Personas per question (answerers)**: 10 (all personas from `personas.md`)
- **Critics per answer**: 10 (same 10 personas in critic role)
- **Early-exit threshold**: new-critique-delta < 20% relative to previous iteration

## Iteration pseudocode

```
for N in 1..10:
  # A. Question generation
  Generate 10 questions probing the request from different angles.
  Save to .run/<uuid>/artifacts/iter-<N>/questions.md

  # B. Expert analysis
  for Q in questions:
    for persona in 10 personas:
      Adopt persona via inline system prompt.
      Answer Q, applying chain-of-consequence analysis (10 steps deep).
      Save to .run/<uuid>/artifacts/iter-<N>/Q<id>-<persona>.md

  # C. Critical review
  for answer in answers:
    for persona in 10 critic personas:
      Adopt critic persona.
      Find problems, gaps, unverified assumptions, semantic shifts.
      Save to .run/<uuid>/artifacts/iter-<N>/Q<id>-critique-<persona>.md

  # D. Problem resolution
  for critique in critiques:
    Adopt solver persona.
    Propose concrete resolution.
    Save to .run/<uuid>/artifacts/iter-<N>/Q<id>-resolution-<persona>.md

  # E. Consensus check
  Compare current iteration's set of new critiques to previous iteration.
  if (new_critique_count / previous_critique_count) < 0.20:
    break  # consensus reached
```

## After the loop

1. Synthesize all iterations into `.run/<uuid>/artifacts/final-design.md`. Single coherent document summarizing the agreed-on design.
2. Present synthesis in chat with the actual iteration count used.
3. Ask user: Apply, Revise, or Cancel.
4. On Apply: perform the primitive's actual write (to `.claude/` for create-* / update-*).
5. On Cancel: META.md status = cancelled, no `.claude/` writes.
6. On Revise: take user feedback, re-enter at synthesis with the prior reasoning preserved; do not rerun all 10 iterations unless asked.

## Context discipline

Long iterations approach context limits. After each iteration:

- Summarize the previous iteration into a 3-line synopsis (key questions, key decisions, outstanding gaps).
- Carry only the synopsis into the next iteration's question generation prompt, not the full prior content.
- Full content stays on disk under `.run/<uuid>/artifacts/iter-<N>/` for audit and consensus check.

## Honest limits

- Simulation: all personas share the same model, same context, same biases. True epistemic diversity requires the Phase 2 runner.
- Cost: tens of thousands of tokens per invocation. Accept this as the cost of quality.
- No deterministic exit: 20% delta is a heuristic; max-iter cap prevents runaway.
