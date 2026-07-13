---
name: python-fastapi
description: Python and FastAPI backend expertise loaded by software-engineering-team agents for server-side work. Use when implementing REST endpoints from an API contract, structuring a service into router, service, and repository layers, adding request validation and consistent error responses, wiring authentication and authorization flows, configuring the app from environment settings, or writing pytest suites with fixtures and business-rule traceability.
user-invocable: false
---

# Python + FastAPI Backend

**Given:** an API contract, data model, and auth specification to implement server-side.
**Produces:** a layered FastAPI application with validated boundaries, consistent errors, and a behavior-complete pytest suite.

## When to Use

- Implementing or extending REST endpoints in a Python backend
- Structuring a new service (project layout, layering, dependency injection)
- Adding validation, error handling, or auth to existing endpoints
- Writing or reviewing the backend test suite
- Reviewing server-side code (load the review checklist below)
- Planning QA for a backend deliverable (load the qa checklist below)

## Core Concepts

1. **Layering:** router -> service -> repository, one-way dependencies. Routers extract parameters and call services; services hold business logic; repositories hold data access. Never cross or reverse the arrows.
2. **Dependency injection:** shared resources (DB session, current user, settings) enter via `Depends`, never via module globals. This is also what makes tests overridable.
3. **Async-first for I/O:** async handlers and async drivers for every network or disk call; no sync I/O inside async functions.
4. **Boundary validation:** validation models on every request, response, and persistence boundary. Never trust client data, never pass raw dicts inward.
5. **Environment config:** all settings from environment via a typed settings object. No hardcoded secrets, hosts, or ports.

## DO

- Type-annotate every function signature, including `-> None`
- Return the status codes the contract declares and render the error envelope it defines; field-level detail on validation failures
- Log structured JSON with a correlation id per request; attach it to error responses
- Pool connections, close them on every path, retry transient failures
- Design writes to be idempotent and safely retryable
- Use the lifespan handler for startup and shutdown (open pools, create indexes, drain in-flight work on termination)
- Implement the pagination scheme the contract declares on list endpoints; push heavy aggregation into the database
- Prefer three similar lines over one confusing utility (abstract on the third repeat)

## DON'T

- Put business logic in routers or raw queries in services
- Expose stack traces or internal identifiers in responses
- Use bare `except:` or swallow exceptions without logging
- Read `os.environ` directly from application code
- Block the event loop with sync I/O or CPU-heavy work in handlers
- Pin dependency or runtime versions inside this skill's guidance; the project's own manifest owns versions

## Quick Start

```python
# router: extract and delegate only
@router.post("/items", status_code=201, response_model=ItemOut)
async def create_item(payload: ItemIn, svc: ItemService = Depends(get_item_service)) -> ItemOut:
    return await svc.create(payload)

# service: business rules, no HTTP and no raw queries
class ItemService:
    def __init__(self, repo: ItemRepository) -> None:
        self.repo = repo

    async def create(self, payload: ItemIn) -> ItemOut:
        if await self.repo.exists_by_name(payload.name):
            raise DuplicateItemError(payload.name)
        return await self.repo.insert(payload)
```

- [patterns](references/patterns.md): project layout, layer responsibilities, test fixtures, the scenario-tag convention, plus a conditional section on message-driven work and caching. Read when implementing or testing a service, or when the contract declares async or cached behavior.

## Checklists

- [review-checklist](references/review-checklist.md): checkbox assertions for code review (architecture, types, errors, performance, security, auth, operations). Read when reviewing server-side code.
- [qa-checklist](references/qa-checklist.md): per-endpoint test taxonomy, fixtures, and requirement traceability. Read when planning QA for a backend deliverable.

## Troubleshooting

**Tests hit the real database or fail with connection errors.**
The app is building its DB dependency at import time. Route all access through an injected `get_db` dependency and override it in tests with `app.dependency_overrides[get_db]`; see the fixture pattern in the patterns reference.

**Endpoints hang or throughput collapses under light load.**
A sync call (driver, file I/O, CPU-bound work) is blocking the event loop inside an async handler. Switch to an async driver, or move the work to a background task or threadpool.

**Validation errors return inconsistent shapes across endpoints.**
Handlers are raising ad hoc exceptions. Register a global exception handler that maps domain exceptions to the error envelope the contract defines; return 422 with field-level details for validation failures.

**Circular imports between router, service, and repository modules.**
A lower layer is importing upward. Move shared types into a schemas or models module both can import, keeping the dependency arrow pointing router -> service -> repository.

**A protected endpoint is reachable without the right role.**
Auth was checked only at login or router include, not per endpoint. Enforce the role or permission check in a dependency on every protected route; add the 403 test from the qa checklist.

## Related Skills

Pairs with the sql-database-design and nosql-database-design skills for schema decisions, and with react-typescript on the consuming frontend.
