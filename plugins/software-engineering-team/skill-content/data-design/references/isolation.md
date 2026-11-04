# Data access and isolation

## Identity and authorization

Derive actor and tenant authority from a validated trusted identity. Treat
client-supplied tenant IDs and model output as selectors to authorize, never as
proof of access. Enforce ownership/membership on list, read, mutation, export,
background jobs and streams. Define revocation and stale-session behavior.
Fail closed when the authority source is unavailable.

## Store boundary

Use the approved tenant-isolation strategy. Where tenant data shares a
relational schema, enforce tenant identity in keys/relationships and use
database access policies when supported and selected. For PostgreSQL RLS,
verify both visibility and write checks, effective roles, owner/bypass behavior
and transaction-local context. Test through the actual non-privileged runtime
role. A superuser test cannot establish runtime isolation.

For database-per-tenant, document-store, key-value and object-store layouts,
prove routing and credentials cannot cross the tenant boundary. Namespace
prefixes alone confer no authorization. Include administrative/global data on
an explicit reviewed policy, with scoped privileged paths.

Test missing context, foreign identifiers, cross-tenant relationships and both
read/write refusal. Include schema-wide coverage when a newly added table or
collection could otherwise escape the policy. Ensure pooled connections and
background execution cannot inherit another request's identity.

## Objects, audit and retention

Bind object keys and metadata to an authoritative ownership record. Authorize
downloads and uploads at the relevant boundary, avoid unnecessarily holding a
database connection during transfer, and account for revocation during long
operations. Prevent orphaned data after partial completion.

Protect audit integrity and minimize sensitive content. Define retention,
deletion, backup and restore behavior together so deleted or restricted data
does not silently reappear through a derived copy. Keep privileged migration
credentials separate from ordinary requests. Use the host and infrastructure
controls to enforce access, then retain evidence of the exercised boundary.
