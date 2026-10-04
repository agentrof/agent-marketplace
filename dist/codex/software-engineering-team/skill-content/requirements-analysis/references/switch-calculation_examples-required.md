# Calculation Examples

These are the instructions of process switch `calculation_examples` at
`required`. A task binds this file only when the project's Process Policy
selects that value; at the default, `off`, a calculation rule follows the
space standard alone. Every task of the Business Analysis and Backlog
Planning flows binds it: the analyst writes the example, the analysis
challenger checks it, and backlog test planning reads it as its oracle.

## Calculation rules

A calculation rule is an active BR whose outcome is a number derived from
inputs or from parameters someone can configure: a score, a price, a fee, a
priority, a rank, a quota, a limit computed from other values. A rule that
only compares a value with a fixed threshold is not one.

Each calculation rule carries, in rows the space standard already defines:

1. Its formula in the BR `statement`, with every input and parameter named
   as the space names it, how the components combine, and its rounding,
   bounds, tie order and units, or
2. At least one AC that cites the BR and whose `criterion` is a worked
   example: the named input values, the parameter values, the expected
   output, then one named parameter change and the expected output after it.

Prefer both. A parameter the owner or a tenant configures is named with its
default and its allowed range; an example uses the default unless it names
another value. Never write an example whose output cannot be computed from
the rows the space holds, and never leave the combination to "as configured".
A gap the owner must close is an open question, not a guessed formula.

## Analysis challenge

The analysis challenger checks every BR for its calculation example
besides its assigned lens. A calculation rule with neither a formula nor a
worked example as above, an example whose output does not follow from the
stated inputs and parameters, or a parameter change whose second output is
missing, is a major finding with the BR id as its anchor. A finding names
the missing part, never a formula of its own.

## Backlog test plans

A test plan scenario that exercises a calculation rule takes its expected
value from the rule's formula or from the cited worked example, and its
`source_refs` cite that BR or AC. The Product Owner and the QA Engineer
never invent a formula, a weight or an expected number. When the approved
sources give neither, the writer stops the scenario, reports a source gap
that names the BR id for the analysis flow, and the backlog reviewer treats
an expected value with no cited source as a major finding.

## Measurement

Record per backlog revision the findings that send a calculation gap back
to analysis, and per analysis review the calculation example findings
raised. The registry's promotion rule judges these.
