# Flow Metrics

[conditional] Read only when the owner asks for schedule forecasting
("when will this be done", "how long is the rest"). These concepts are NOT
used for backlog ordering in this flow: ordering is dependency order plus
risk-adjusted value ([prioritization](prioritization.md)), there are no
sprints, and no estimate field exists in any artifact. A forecast request
gates nothing; answer it and leave the backlog ordered as it is.

## The Three Concepts

- Cadence: the rhythm of checkpoints. This flow's natural cadence is the
  merge checkpoint after each package; forecasts count checkpoints, not
  calendar days, unless the owner supplies dates.
- Throughput: packages marked done per checkpoint interval, read straight
  from the backlog's status fields.
- Cycle time: how long one package takes from leaving ready to being
  marked done at its merge checkpoint.

## Forecasting From the Backlog

1. Count the packages marked done and the intervals they took; that ratio
   is the observed throughput.
2. Divide the remaining non-deferred packages by the observed throughput;
   the result is intervals remaining.
3. Give the answer as a range built from the best and worst observed
   intervals, never a single number.
4. State the assumptions inside the answer: packages stay one review unit
   each, no new criteria arrive, the deferred list stays deferred. If any
   assumption breaks, the forecast is void, not adjusted quietly.
5. With fewer done packages than it takes to see variation, say the sample
   is too small and decline to forecast; a made-up range is worse than
   none.

## What Not to Do

- DON'T reorder the backlog to improve a metric; ordering answers only to
  dependencies and risk-adjusted value.
- DON'T add an estimate field, points, or sizing numbers to any artifact
  to sharpen a forecast; the sizing rule (one review unit) is the only
  size that exists.
- DON'T convert a forecast into a commitment in the backlog summary; write
  it as an owner-facing range with its assumptions, then leave it out of
  the artifact.
