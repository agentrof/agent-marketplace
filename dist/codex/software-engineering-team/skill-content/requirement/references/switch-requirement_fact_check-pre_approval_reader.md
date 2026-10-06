# Fact-check a technical Requirement before approval

These are the instructions of process switch `requirement_fact_check` at
`pre_approval_reader`. The Requirement entry follows this file only when the
project's Process Policy selects that value; at the default, `off`, only the
compiler checks a Requirement before its approval question. Read the value
with `process_policy.py value --switch requirement_fact_check`.

## When it runs

After the Requirement compiles clean and before its approval question, when
any Outcome or Acceptance sentence names a contract, script, file path, test
id, hash or matching rule. A Requirement whose outcomes state intent only
skips the reader.

## The reader

1. Start one read-only reader on the low tier with the Requirement's Outcome
   and Acceptance sentences and the files they cite, at the current checkout.
2. The reader checks each cited claim against the cited text only: a value,
   hash input, id rule, supersession or limit the cited file states otherwise,
   or a cited file or id that does not exist. It returns contradictions only,
   each with the sentence, the cited file and the conflicting line. It never
   judges intent, scope or wording and never proposes new outcomes.
3. The coordinator fixes each contradiction in the draft, recompiles, and
   names the fixes when it presents the Requirement. A contradiction that needs
   an owner choice becomes an open question in the presentation, never a
   silent fix.

The approval gate itself is unchanged: the owner still approves, requests
changes or stops.

## Measurement

The project owner measures outside every task; no role acts on it. Record per
technical Requirement the contradictions found, the Requirement revisions
Backlog Planning later forced, and the owner gates before the backlog handoff.
