# Python + FastAPI QA Checklist

Test taxonomy, fixture set, and traceability rules for backend QA. Coverage is behavioral, not percentage-based: happy path, every error branch, every auth path, one test per business rule.

## Per-Endpoint Test Class Taxonomy

For every endpoint in the API contract, one test class covering the applicable rows:

| Case | Expectation |
|---|---|
| Success | Correct status (200/201/204) and response shape |
| Unauthenticated | 401 when no or invalid token |
| Unauthorized | 403 when authenticated but wrong role or not owner |
| Invalid input | 422 with field-level error details |
| Not found | 404 for missing resource ids |
| Conflict | 409 for duplicates or state conflicts |
| Boundary | Empty string, max length, special characters, zero, negative |

```python
class TestCreateResource:
    """Tests for POST /api/v1/resources"""

    async def test_create_resource_with_valid_input_returns_201(self, authenticated_client): ...
    async def test_create_resource_without_token_returns_401(self, client): ...
    async def test_create_resource_with_wrong_role_returns_403(self, user_client): ...
    async def test_create_resource_with_invalid_payload_returns_422(self, authenticated_client): ...
    async def test_create_resource_with_duplicate_returns_409(self, authenticated_client): ...
    async def test_create_resource_boundary_values(self, authenticated_client): ...

class TestListResources:
    """Tests for GET /api/v1/resources"""

    async def test_list_resources_with_data_returns_paginated(self, authenticated_client): ...
    async def test_list_resources_empty_collection_returns_empty_array(self, authenticated_client): ...
    async def test_list_resources_without_token_returns_401(self, client): ...
```

List endpoints additionally assert pagination metadata and the empty-collection case (empty array, not an error). Delete endpoints assert 204 plus a follow-up 404.

## Conftest Fixture Set

- `client`: unauthenticated HTTP client with the database dependency overridden to an ephemeral test database (pattern in [patterns](patterns.md))
- `authenticated_client`: client with a standard-user token attached
- `admin_client`: client with an admin-role token
- `user_client`: client with a non-admin role, for 403 assertions
- `db_session`: isolated test database, schema created in setup, dropped in teardown
- `user_factory` and per-entity factories: valid objects with overridable fields

## Business Logic Tests

For every service-layer function:

- one test per business rule it implements, tagged `@scenario("BR-###")`
- constraint enforcement raises the expected domain exception
- error paths return meaningful messages; no raw exceptions leak to the caller
- state transitions verified (from A to B, invalid transitions rejected)

Property-based tests are worth adding for computation-heavy logic (invariants like "total is never negative").

## Data Layer Tests

For every repository:

- create persists, read returns, update modifies, delete removes
- unique constraints raise on duplicates
- required fields raise on missing values
- relationship or reference integrity holds across related records
- seed script runs without error and produces the expected records

## Auth Flow Tests

- register creates a user with a hashed (never plaintext) password
- login returns access and refresh tokens
- valid token reaches a protected route
- refresh issues a new access token; reuse of a revoked refresh token fails
- logout invalidates the token server-side
- expired, malformed, missing, and revoked tokens each return 401
- role checks: admin reaches admin routes, regular user gets 403
- ownership: owner can modify own resource, non-owner cannot
- no self-privilege-escalation path exists

## Infrastructure Tests

- CORS headers present; correlation id propagated through middleware
- dependency override works (proves injection is test-swappable)
- settings load from environment; a missing required variable fails with a clear error
- health endpoint returns 200 and verifies critical dependencies
- global handlers: 404 and 500 return the JSON error envelope; validation errors return field details; no stack trace leaks
- every module imports cleanly; no circular imports

## Naming and Traceability

- Names follow `test_<unit>_<scenario>_<expected>`; the name reads as a requirement.
- Every business rule and user story id maps to at least one test via the `@scenario("BR-###")` marker ([patterns](patterns.md)). Generate the traceability matrix (rule id -> tests -> status) from marker metadata; do not maintain it by hand.
- QA verdict: a rule without a tagged test, an endpoint missing a taxonomy row, or an untested error/auth branch is a named FAIL item. There are no percentage gates to satisfy instead.
