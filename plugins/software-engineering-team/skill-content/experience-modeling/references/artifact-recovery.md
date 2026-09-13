# Approved artifact recovery

An inventory mismatch on an otherwise approved, committed application has one
explicit recovery path. `propose --recover-artifacts --application-action update
--reason <explanation>` binds the immutable predecessor registry/ledger hashes,
complete before/after inventories, exact meaningful added/changed/removed rows,
historical policy exclusions, unchanged process receipts and current upstream
inputs. Approve that exact schema-v4 proposal before opening the next draft.
Meaningful retained or new sources must be tracked and byte-exact in `HEAD`;
meaningful deletions must be committed. Only historical metadata excluded by
policy may be absent without a deletion commit. No history, receipt or prototype
bytes are rewritten by opening recovery.

The existing begin, enter-review and approve commands recheck the full proof.
Any new artifact, source or receipt drift invalidates it; an open lifecycle,
interrupted transaction, malformed history or no-op delta fails closed. The
fresh read-only reviewer assesses the visible recovery delta and attests the
normal proposal/application hashes. Ordinary approval appends a new application
revision, preserves all historical receipt rows and process revisions, then
requires downstream Requirement/backlog revision and rebinding.


## Process receipt compatibility

New process revisions exclude the same policy metadata. Existing approved or retired process receipts retain their legacy hash only when the original bytes reproduce it exactly. Missing legacy process metadata cannot be recovered from a hash alone and remains an explicit validation failure.

## Repository ignore rules

Git ignore rules do not define approval content. Meaningful ignored prototype files remain snapshot candidates and must be committed before approval. Only the exact operating-system metadata policy is excluded. The sibling vault `.obsidian/` UI state and project runtime scratch are outside these product snapshot roots.
