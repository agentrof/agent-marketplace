## Scope

Describe the user-visible or operational outcome and the non-goals.

## Verification

- [ ] The staged candidate passed `make check-local` and `make verify-local`.
- [ ] Generated host distributions were checked or regenerated through the
  canonical builder.
- [ ] Relevant failure, recovery or regression tests were added.
- [ ] A release-impact changeset is present when the stable package changes.

## Review notes

List migration, rollout, compatibility or security considerations. State
`None` only after checking each category.

For a maintainer issue solution, also verify the reported root cause,
challenged alternative, host/OS impact, exact candidate checks, and that the
protocol stopped before merge.
