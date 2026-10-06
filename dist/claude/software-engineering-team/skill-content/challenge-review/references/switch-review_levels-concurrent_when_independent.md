# Concurrent Independent Levels

These are the instructions of process switch `review_levels` at
`concurrent_when_independent`. A task binds this file only when the
project's Process Policy selects that value; at the default, `sequential`,
each review level starts after the earlier one is approved. Every task of an
owning flow binds it, and only the orchestrating entry acts on it.

## Which levels run together

A level runs beside an earlier one only where its flow declares it
independent: it reads the same frozen inputs and consumes no verdict or open
finding of the earlier level.

- Backlog planning: the root review starts beside the epic reviews once
  every source note is finished and the root manifest derives.
- Execution planning and Operation: an Operation contract review starts
  beside the execution-plan topology review when neither cites the other's
  open findings.

Every other level stays sequential.

## Running them

1. Derive every level's manifest first; each binds its own hash.
2. Spawn the readers of every independent level in one message, as the host
   contract names, then wait for all of them before triage or any writer
   action.
3. Approval still waits for every level. A blocking fix re-runs every level
   whose manifest hash it changes, exactly as a sequential run would; a level
   whose hash it leaves unchanged keeps its verdict.

## Measurement

Record per chain its wall clock against the levels' summed times and every
level re-run because a fix at another level staled it.
