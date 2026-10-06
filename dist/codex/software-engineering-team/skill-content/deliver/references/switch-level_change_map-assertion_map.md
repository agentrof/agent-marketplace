# A converted scenario keeps every assertion of its Then

These are the instructions of process switch `level_change_map` at
`assertion_map`. A task binds this file only when the project's Process Policy
selects that value; at the default, `off`, the writer rewrites a converted
scenario's test on trust and the readers count coverage by scenario id, as the
flow describes. Inside a Delivery read the value with `process_policy.py value
--switch level_change_map --delivery DLV-###`; the Delivery pins the policy it
runs under.

A scenario whose level moves, for example from `live` to `unit`, gets a new
setup and often new assertions. A rewritten test that still names the
scenario's target counts as covering it, whatever it asserts. At this value
the writer records what proves each Then before and after the change, and the
runner checks that record before any reader starts.

## Which scenarios are converted

`freeze` and `assertion-map` derive them; no role chooses them. A scenario is
converted by the Item when all of these hold:

- it is automation-required in the candidate's Test Plan;
- the file of its automation target, the part before `::`, is a file the Item
  changes against its integration base;
- its `level` differs from the one in the newest Test Plan revision that an
  integrated Item of its story recorded as `test_plan_source_hash`. A missing
  level counts as none, so a move from none to `unit` converts too.

A scenario no integration bound yet is new, not converted. A story whose
integrated revision neither the candidate nor its history holds refuses, so no
conversion is dropped silently.

## The assertion map

The writer of the Item records the map in the Item's verification runtime, at
the `assertion_map_file` that `assertion-map --delivery DLV-### --story
<story>` names. That verb prints the converted scenarios, the current check, the
kinds and a template with every converted scenario and its Then:

```json
{"schema_version": 1, "scenarios": {"ST-002-TS-004": {
  "then": "the refusal names the expired account",
  "before": [{"path": "tests/live/test_access.py", "code": "assert body[\"refusal\"] == \"ACCOUNT_EXPIRED\"",
              "kind": "error", "expected": "ACCOUNT_EXPIRED"}],
  "after": [{"path": "tests/unit/test_access_rules.py", "code": "assert decide(expired).refusal == \"ACCOUNT_EXPIRED\"",
             "kind": "error", "expected": "ACCOUNT_EXPIRED"}]}}}
```

- `then`: the scenario's Then, exactly as its Test Plan states it.
- `before`: each assertion that proved the Then in the test at the
  integration base; `after`: each one that proves it in the candidate. Name
  every assertion of the Then on both sides, at least one each.
- `path` and `code`: a repository path and one line of that file, the
  assertion or the table row that holds its expected value. A before line must
  be a line of the file at the integration base, an after line a line of the
  file in the candidate, compared without leading and trailing blanks.
- `kind`: what the assertion compares, one of the kinds in
  `deliver/data/assertion-kinds.json`: `value` and `error` compare the outcome
  with an expected value or refusal; `count` and `existence` check only how
  many results there are or that one exists.
- `expected`: the expected value as its code line writes it, or `null` when
  the assertion has none.

Write the map after the converted tests are committed and before `freeze`.
Run `assertion-map` again until it reports no problem; it reads the committed
candidate only. The map is runtime scratch, never committed, and the candidate
binds its hash, so a map changed after the freeze makes the candidate drift.

## What the runner checks

`freeze` refuses, naming each problem, while:

- a converted scenario has no map entry, or the map names a scenario the Item
  does not convert;
- an entry's `then` is not its Test Plan's Then;
- a side names no assertion, or an assertion lacks a field, names no
  normalized path, holds more than one line, names an unknown kind or an
  expected value its code line does not hold;
- an assertion line is not a line of its file on its side.

A complete entry is flagged, never refused, when every after assertion is of a
weak kind, `count` or `existence`, while a before one was not
(`weakened_kind`), or when an expected value of a before assertion is no after
assertion's (`expected_value_lost`). The candidate carries
`assertion_map.converted_scenarios` and `assertion_map.flagged` into every
reader's manifest.

## The code reviewer

The code reviewer reads each flagged pair against the frozen candidate and the
integration base, with `inspect --path <file>` and `inspect --path <file>
--base`, and decides whether the after assertions still prove the Then. A
converted assertion that no longer proves its Then, by a count in place of the
rows it checked, a clock left unpinned for a time-dependent rule, an expected
value dropped or a finding that no longer names its object, is a major
finding. A converted scenario the check did not flag needs no re-audit beyond
the review's normal reading. The map's kinds and expected values are the
writer's own claims: a flag the reviewer finds unwarranted is no finding, and
a mislabeled kind that hides a weakened assertion is a major finding.

## Measurement

The project owner measures outside every task; no role acts on it. For each
Item that converts scenarios, record the converted scenarios, the pairs
flagged, the flagged pairs the code reviewer confirmed as weakened, the
weakened or vacuous expectations a later audit or review round found that the
check did not flag, and the wall clock from the conversion to the first passing
full test run. The registry's promotion rule judges them over at least 3 such
Items.
