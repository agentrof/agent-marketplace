# Python + FastAPI Review Checklist

Checkbox assertions for reviewing server-side code. Every unchecked box is a finding; report it with file, line, and the failing assertion.

## Architecture

- [ ] Layer separation enforced: router -> service -> repository (no router calling the database directly)
- [ ] Layer boundaries not crossed (no service importing a router, no repository importing a service)
- [ ] Dependency injection used for shared resources (DB session, current user, settings, external clients)
- [ ] Lifespan handler used for startup/shutdown, not deprecated event hooks
- [ ] Repository pattern provides real abstraction (no raw queries in the service layer)
- [ ] Middleware registration order correct (CORS -> auth -> logging -> error handling)
- [ ] No circular imports between modules
- [ ] Router files contain only endpoint definitions and parameter extraction
- [ ] Service files contain all business logic and orchestration
- [ ] Repository files contain only data access operations
- [ ] Base model or mixin used for common persistence fields (created_at, updated_at, soft-delete flag)

## Type Safety

- [ ] All function signatures fully annotated, parameters and return types (including `-> None`)
- [ ] Validation models on every data boundary: request, response, persistence
- [ ] No `Any` without an explicit justification comment
- [ ] Enum types for fixed value sets (status, roles, permissions)
- [ ] Optional types paired with proper None guards
- [ ] Modern generic syntax (`list[str]`, `dict[str, int]`) not the legacy typing aliases
- [ ] Protocols or type variables used for generic abstractions where they earn their keep

## Error Handling

- [ ] Global exception handler registered on the app
- [ ] Validation errors return 422 with field-level details
- [ ] Database errors caught and mapped to appropriate status codes
- [ ] No bare `except:`; specific exception types only
- [ ] Error responses follow one consistent envelope across all endpoints
- [ ] Custom exception classes defined for domain-specific errors
- [ ] Every caught exception logged with context (correlation id, user id, operation)
- [ ] Retry logic for transient failures (connection drops, upstream timeouts)
- [ ] Stack traces never exposed in responses; production returns generic messages
- [ ] 500 responses carry a correlation id so failures are debuggable without leaking internals

## Performance

- [ ] Frequently queried fields are indexed
- [ ] No N+1 query patterns; batch or join where needed
- [ ] Connection pooling configured
- [ ] No sync I/O inside async functions (event loop never blocked)
- [ ] Pagination on every list endpoint
- [ ] Long-running work moved to background tasks, not the request path
- [ ] Response models exclude unneeded fields (no over-fetching)
- [ ] Sessions and connections closed on every code path, including error paths
- [ ] Aggregation pushed to the database instead of application-level loops
- [ ] Bulk operations for batch writes, not per-item writes in loops

## Security

- [ ] Passwords hashed with a memory-hard or adaptive algorithm (bcrypt or the argon family), never reversible
- [ ] Token validation checks expiry, signature, and claims
- [ ] All request bodies validated through models; no raw dict access
- [ ] No injection paths: parameterized queries only
- [ ] Secrets loaded from environment via the settings object, never hardcoded
- [ ] No sensitive data in logs (passwords, tokens, personal data)
- [ ] CORS restrictive: explicit origins from configuration, never a wildcard in production
- [ ] Rate limiting on authentication endpoints
- [ ] Security headers set (content-type options, frame options, strict transport security)
- [ ] Request body size limits configured
- [ ] File uploads validated (type whitelist, size limit) where applicable
- [ ] No debug mode or verbose tracebacks in production configuration
- [ ] Audit logging for security-sensitive events (login, failed auth, permission changes)
- [ ] No sensitive data in URL query parameters

## Authentication and Authorization

- [ ] Signing secret loaded from environment and sufficiently long
- [ ] Access tokens short-lived, paired with a refresh flow
- [ ] Refresh mechanism rejects expired or revoked tokens
- [ ] Role and permission checks enforced on every protected endpoint, not just at the route group
- [ ] No privilege escalation path: users cannot modify their own role or permissions
- [ ] Logout and password change invalidate tokens server-side, not only client-side
- [ ] Token claims carry only what is needed; no sensitive personal data in the payload
- [ ] Account lockout or aggressive rate limiting after repeated failed logins

## Configuration and Operations

- [ ] Typed settings object used for all configuration, not direct environment reads
- [ ] Example environment file lists every required variable with a description
- [ ] Health endpoint exists and verifies critical dependencies
- [ ] Structured JSON logging with correlation ids
- [ ] Host and port configurable via environment
- [ ] Version control ignores generated and sensitive files (env files, caches)
- [ ] Golden-signal metrics derivable from the service: latency, traffic, errors, and saturation observable per endpoint (request timing, request and error counters, pool utilization)
- [ ] Correlation id propagated to outbound calls: every downstream request carries the inbound request's id in its headers, not only the local logs
- [ ] Log levels follow the semantic ladder: an expected user error (validation failure, wrong password, not found) is never logged at error level; error level is reserved for faults an operator must act on
- [ ] Dependencies resolve through a committed lockfile: the manifest owns the ranges, the lockfile pins the exact dependency graph, and installs use the lockfile
