# Uncontested path claim amendments

At `provisional_claims` value `uncontested_delta`, the coordinator may run
`delivery_git.py amend-path-claims --delivery DLV-### --story <story> --path
<path>`, repeating `--path` for the complete batch. The command requires an
active serial Item with a current writer receipt and an open Fence without a
barrier. It refuses protected paths and every case-insensitive overlap with
another unfinished Item, including earlier amendments.

The command publishes an immutable, approval-bound path receipt and its Item
reference through the existing atomic Fence and Integration transaction. It
changes no source, contract, plan hash or approval. A lost-response retry reuses
the existing grant. Converge the Item before freezing or publishing; its task
inputs, review guard and publication guard all replay the same receipts.

A full plan approval folds an unfinished or reopened Item's grants into ordinary
claims and preserves a sealed Item's receipt and evidence. Contract changes,
parallel-lane scope changes and paths another unfinished Item owns retain the
normal plan revision. At initial planning, include the sibling tests and
supporting modules the implementation needs in the claims proposed for approval.
