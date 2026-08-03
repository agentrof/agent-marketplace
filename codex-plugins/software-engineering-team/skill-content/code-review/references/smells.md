# Smells and Named Moves

Maintainability findings on the diff, each paired with the named refactoring move that goes in the finding's Fix field. A named move indexes a runbook the developer can execute; "clean this up" does not.

## Smell-to-Move Table

| Smell | Symptom in the diff | Named move (Fix field) |
|---|---|---|
| Long method | One function accumulates branches and blocks, with comment headers marking phases | Extract method: one function per phase, named for its intent |
| Feature envy | A method reads several fields of another object and few of its own | Move method to the class whose data it envies |
| Shotgun surgery | One logical change lands as parallel small edits across many files | Consolidate: move the scattered behavior behind one owner |
| Primitive obsession | Domain concepts passed as bare strings and numbers (ids, money, emails) | Introduce value type carrying its validation and formatting |
| Duplicated code | The same logic pasted, now on its third occurrence | Extract shared function (third-repeat rule: two copies may stand) |
| Long parameter list | A signature grows past a handful; callers pass values in a fixed clump | Introduce parameter object |
| Data clumps | The same field group travels together through several signatures | Group into one object with a domain name |
| Divergent change | One module keeps getting edited for unrelated reasons | Split module by reason for change |
| Message chain | `a.b().c().d()` reaching through intermediaries | Hide delegate: the first receiver exposes what the caller needs |
| Speculative generality | Hooks, parameters, or abstractions no caller uses | Inline or delete the unused generality |
| Magic values | Unexplained literals inside logic | Extract named constant |
| Dead code | Unreachable branches or unused exports left behind by the change | Delete |

## Severity Rule

- Smells are MINOR. They never block the verdict on their own.
- Exception: a smell masking a correctness or conformance defect is reported as THAT defect at its real severity, with the move riding in the Fix field. Test: would the code still be wrong if it were clean? Then the finding is the wrongness, not the smell.
  - Primitive obsession that lets an unvalidated value cross a boundary is a correctness or security finding, not a style note.
  - Shotgun surgery caused by a second writer to owned data is a conformance finding with an ownership impact rating, possibly an escalation.
  - Duplicated logic whose copies have already diverged is a correctness finding: the copies disagree and at least one is wrong.
- DON'T stack MINOR smell findings to justify blocking; that is severity inflation (see [pitfalls](pitfalls.md)).

## Writing the Finding

- Fix field: the named move plus its concrete target ("Extract method: split validate, persist, notify out of order_service.create"), never the move name alone.
- Verification field: behavior unchanged and the suite still green. A refactoring finding whose fix changes observable behavior was misclassified; re-file it under correctness.
