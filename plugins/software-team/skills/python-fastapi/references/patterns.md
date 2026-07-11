# FastAPI Production Patterns

Project layout, layering, test fixtures, and the scenario-tag convention. Load when implementing or testing a Python backend.

## Project Layout

```
app/
  api/
    v1/
      endpoints/          # one module per resource (users.py, auth.py, items.py)
      router.py           # aggregates endpoint routers under /api/v1
    dependencies.py       # shared Depends providers (get_db, get_current_user, ...)
  core/
    config.py             # typed settings object, loaded from environment
    security.py           # hashing, token creation and verification
    database.py           # engine/client, session factory, get_db dependency
  models/                 # persistence models
  schemas/                # request/response validation models
  services/               # business logic (user_service.py, auth_service.py)
  repositories/           # data access only (user_repository.py, ...)
  main.py                 # app factory, lifespan, middleware, router include
tests/
  conftest.py             # fixtures below
  test_<resource>.py      # one module per endpoint group
```

Rules the layout encodes:

- Endpoints modules contain route definitions and parameter extraction only.
- Services never import from `api/`; repositories never import from `services/`.
- `schemas/` is the shared vocabulary every layer may import.
- `main.py` wires everything: settings, lifespan, middleware order (CORS -> auth -> logging -> error handling), routers.

## Layer Responsibilities

| Layer | Owns | Forbidden |
|---|---|---|
| Router | HTTP concerns: paths, status codes, params, response models | Business rules, queries |
| Service | Business rules, orchestration, domain exceptions | HTTP objects, raw queries |
| Repository | Queries, persistence mapping | Business rules, HTTP |

Domain exceptions raised by services are translated to HTTP responses by a global exception handler, keeping services HTTP-free.

## Test Fixtures: Client + Dependency Override + Ephemeral Database

The load-bearing pattern: the app never knows it is under test. Tests swap the DB dependency via `dependency_overrides` and run against an in-memory or ephemeral test database created per fixture.

```python
# tests/conftest.py
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.core.database import get_db

@pytest.fixture
async def db_session():
    """Ephemeral test database: create schema, yield a session, drop on teardown."""
    engine = create_test_engine()          # in-memory or throwaway instance
    await create_all(engine)               # build schema/indexes fresh per test
    async with session_factory(engine)() as session:
        yield session
    await drop_all(engine)

@pytest.fixture
async def client(db_session):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()

@pytest.fixture
async def authenticated_client(client, user_factory):
    user = await user_factory()
    token = create_access_token(user.id)
    client.headers["Authorization"] = f"Bearer {token}"
    yield client
```

```python
# tests/test_users.py
async def test_create_user_valid_payload_returns_201(client):
    response = await client.post(
        "/api/v1/users/",
        json={"email": "jane@example.com", "password": "testpass!", "name": "Jane Doe"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "jane@example.com"
    assert "id" in data
```

Points that make this pattern hold:

- The override targets the same `get_db` object the app imports; import it from one place.
- Schema creation lives inside the fixture, so every test starts from a known-empty database.
- `dependency_overrides.clear()` on teardown prevents leakage between test modules.
- Factories (`user_factory`) build valid objects with overridable fields; tests state only what they care about.

## Naming Convention

```
test_<unit>_<scenario>_<expected>
```

Examples: `test_login_with_invalid_password_returns_401`, `test_create_user_with_duplicate_email_returns_409`, `test_calculate_total_with_negative_discount_raises_value_error`. The name alone should read as a requirement.

## Scenario Tags: Business-Rule Traceability

Stamp each test with the business rule it proves using a thin pytest marker, then generate the traceability matrix from marker metadata instead of maintaining it by hand.

```python
# tests/conftest.py (or a small plugin module)
import pytest

def scenario(rule_id: str):
    """Tag a test with the business-rule or story id it verifies (BR-### / AC-###)."""
    return pytest.mark.scenario(rule_id)

@pytest.fixture(autouse=True)
def _scenario_to_junit(request, record_property):
    """Render every @scenario marker into the JUnit XML as a testcase
    property. Without this hook the tag exists only in source and the
    coverage audit maps nothing."""
    for marker in request.node.iter_markers("scenario"):
        for rule_id in marker.args:
            record_property("scenario", rule_id)
```

```python
# pyproject.toml / pytest config
# markers = ["scenario(rule_id): links a test to a business rule or acceptance criterion"]
# junit_family = "xunit1"   (per-testcase properties render warning-free)
```

```python
@scenario("BR-001")
async def test_create_user_with_duplicate_email_returns_409(authenticated_client):
    ...

@scenario("US-003")
async def test_login_with_valid_credentials_returns_tokens(client):
    ...
```

Usage:

- The coverage matrix is machine-generated: run the suite with `--junitxml`, then the verification skill's scenario_report script crosses the brief's ids with the rendered properties. The autouse fixture above is what makes that work; a suite without it audits as all NO-TEST even when every test is tagged.
- A business rule with no tagged test is an untested requirement; the QA pass flags it.
- Combine freely with standard markers (`@pytest.mark.integration`, `@pytest.mark.slow`) for suite slicing.

## Behavioral Coverage Contract

No percentage thresholds. A backend deliverable is covered when, for each endpoint and service function:

- the happy path is tested,
- every error branch is tested (validation failure, not found, conflict, dependency failure),
- every auth path is tested (unauthenticated, wrong role, correct role, ownership),
- every business rule has at least one test tagged with its `@scenario` id.

Anything short of that list is a named gap in the QA report, not a percentage.

## Executable Contract: Schema Export

The interface contract is executable, not prose. The stack generates the
schema for free; export it as a build artifact and let the client check
against it:

```python
# scripts/export_schema.py (or a make target the test command calls)
import json
from pathlib import Path

from app.main import app

Path("openapi.json").write_text(json.dumps(app.openapi(), indent=2))
```

- The export runs inside the configured test command, so a schema that no
  longer matches the code cannot go stale silently.
- The frontend's suite validates its typed client against this file (see
  the client stack's testing reference); a shape drift is a red suite on
  either side, never a runtime surprise.
- The finalize step publishes the exported schema as the package's API
  documentation.

## Performance Assertions: the honest floor

Budgets from the brief get the cheapest real measurement, never a fake
green:

- Perf-smoke: a marked test fires one request against the running app and
  asserts an order-of-magnitude bound; label it plainly as a smoke, not a
  proof of the p95-under-load budget.
- Seeded-volume assertion: where a budget names a data volume, seed it
  (bulk-insert the named row count in the fixture) and assert the query
  path meets its bound on one request; slow but deterministic, run behind
  a marker.
- Budgets only load can prove (concurrency, sustained p95) are reported
  UNVERIFIED with the reason in the QA record's budget table.

## Message-Driven Work and Caching

[conditional] Read when the contract declares asynchronous behavior (accepted-then-processed endpoints, webhooks, scheduled jobs) or cached behavior. When the contract declares neither, introducing a queue or a cache is an architecture change: route it to the architecture owner, do not implement it as a local optimization.

### Queue/Worker Split

- The request path validates, persists a job record, enqueues a message, and returns the accepted-status response the contract declares (job id plus a status location). No heavy work in the handler.
- The worker is a separate process consuming from the queue; it reuses the service and repository layers, never the router layer.
- Job state lives in the database (`queued -> running -> succeeded | failed`), so status reads never touch the broker.

```
POST /reports             -> accepted { "job_id": "...", "status_url": "..." }
worker: consume(message)  -> load job -> run service -> update job state -> ack
GET  /reports/jobs/{id}   -> ok { "state": "running", ... }
```

### At-Least-Once and Idempotent Consumers

- Assume every message can arrive twice; brokers redeliver on timeout and on consumer crash.
- Make the consumer idempotent: key side effects on a unique message or job id, and make the write a no-op or safe upsert when that key was already processed.
- Acknowledge only after the side effect is durably committed. Ack-then-work loses messages; work-then-crash-then-redeliver is exactly what idempotency absorbs.
- Poison messages: cap redeliveries and park repeated failures in a dead-letter destination with the error recorded; an unbounded retry loop is an outage.

### Webhooks (outbound)

- Sign every payload: keyed hash over the raw body plus a timestamp header. Receivers verify both; the timestamp bounds replay.
- Retry failed deliveries with exponential backoff and a cap; record delivery attempts per event so support can answer "did we send it".
- Treat success-range responses as delivered; everything else retries. Never block the originating request on webhook delivery.

### Scheduled Jobs

- Single-flight: a run takes a lock (database row or advisory lock) so overlapping schedules cannot execute the same job twice.
- Job bodies are idempotent and resumable; record the last successful run so a missed window catches up deliberately, not accidentally.
- Schedules are configuration loaded through the settings object, never code constants.

### App-Level Caching (cache-aside)

- Read path: check the cache; on miss, load through the repository, set the entry with a TTL, return.
- Every cached key DECLARES its invalidation: TTL only, explicit delete on the owning write path, or event-driven. An entry without a declared invalidation is the caching version of the undeclared snapshot: it silently serves stale data.
- The cache is never the source of truth: writes hit the store first, and cache failures degrade to store reads, never to errors.
- DON'T cache per-user responses under shared keys; the key must carry every dimension the response varies on.
