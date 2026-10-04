# Reader Waves

These are the instructions of process switch `reader_waves` at
`all_at_once`. A task binds this file only when the project's Process Policy
selects that value; at the default, `as_slots_free`, readers start as the
host lets them. Every task of an owning flow binds it, and only the
orchestrating entry acts on it: a role never starts or closes another agent.

A wave is every independent reader one review or recheck step starts over
the same frozen inputs: a review panel, the primary reviewer with its
specialist readers, or the readers of a recheck. Readers that wait for
another reader's result belong to a later wave.

## Starting a wave

1. Wait until the writer's pass that the wave reads has ended, as the flow
   requires. No writer runs while its readers run.
2. Close every finished agent the session still holds open, the writer
   between its passes included, as the host contract names. A closed writer
   starts its next pass fresh from its task manifest and the returned
   findings, never from its earlier transcript.
3. Count the free agent threads. Start every reader of the wave before
   waiting on any of them. When fewer threads are free than the wave has
   readers, start the readers with the largest inputs first, say so in the
   progress message, and start each remaining reader as soon as a reader
   finishes and is closed.
4. Wait for every reader of the wave before triage or any writer action.

The wave changes when readers start, never who reads, what each reader is
given or how findings are triaged: switches `review_panels` and
`review_loop` keep deciding those.

## Measurement

Each wave's progress message names the wave size and how many of its
readers run at once. Record per wave its wall time, its slowest reader's
time, the most readers running at once and the minutes between the first
and the last reader's start. The registry's promotion rule judges these.
