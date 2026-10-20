# Tenant Isolation

Mandatory when the system is multi-tenant. A query filter alone is not
isolation; the database refuses a cross-tenant read or write by itself.
Every unchecked box is a finding.

## Tenant Tables

- [ ] Every tenant table has `tenant_id NOT NULL`, defaulted from a transaction-local setting (`current_setting('app.tenant_id')`), so an insert without tenant context fails.
- [ ] Row level security is ENABLED and FORCED on every tenant table, so the owner is filtered too.
- [ ] One policy per table with both USING and WITH CHECK on the current tenant; no separate permissive policy widens it.
- [ ] The tenant key leads the compound indexes of tenant-scoped access paths.

## Roles

- [ ] Migrations run as the owner role; requests run as a non-owner role without BYPASSRLS.
- [ ] Readiness refuses a request connection that is a superuser, has BYPASSRLS or owns the tables.
- [ ] The application sets the tenant context transaction-local in the one helper that opens the transaction; a missing context is refused before any read or write.
- [ ] TEMP and CREATE on the public schema are revoked from PUBLIC.

## Schema-Wide Test

- [ ] One test walks the catalog and fails on any table missing `tenant_id`, ENABLE or FORCE row level security, or the policy.
- [ ] Global tables (no tenant) sit on a reviewed allowlist in that test, each with its reason.

## Audit

- [ ] Audit tables are append-only by trigger: UPDATE and DELETE are refused for every role, the owner included.
- [ ] The audit row is written in the same transaction as the effect; an audit write failure stops the action.
- [ ] Denials are audited too.

## Invariants and Migrations

- [ ] Invariants live in constraints, not code: uniqueness and "one active row" as partial unique indexes (`UNIQUE (tenant_id, x) WHERE active`).
- [ ] Migrations are numbered, run under an advisory lock and carry checksums; an applied migration whose checksum changed stops the run.
- [ ] Two branches that add the same migration number renumber on merge; numbers never collide on the main branch.
