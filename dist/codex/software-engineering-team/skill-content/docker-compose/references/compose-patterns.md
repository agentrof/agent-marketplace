# Compose Patterns

How the environment definition (the compose file) and its entry point are written. Both live under the environment prefix (workspace/environment/) and are owned by the devops engineer.

## Service Anatomy

Every service entry carries the same six concerns. Nothing else is optional glue; a missing concern is a review finding.

```yaml
services:
  api:
    build:
      context: ..                      # build context is workspace/
      dockerfile: environment/docker/api.Dockerfile
    environment:
      # config enters here, nowhere else; the fallback is the committed
      # secretless default, complete enough for a clean-machine bring-up
      DATABASE_URL: ${DATABASE_URL:-postgresql://app:app@db:5432/app}
    healthcheck:
      test: ["CMD", "app-health"]      # probes real capability
      interval: 5s
      timeout: 3s
      retries: 10
      start_period: 15s
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "8000"                         # container port only: host port ephemeral
  db:
    image: postgres:<exact-tag>        # exact tag, never a floating one
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app"]
      interval: 5s
      timeout: 3s
      retries: 10
    volumes:
      - db-data:/var/lib/postgresql/data

volumes:
  db-data:                             # named, project-scoped; never external
```

Application services set `init: true` so a real init process reaps zombies and forwards stop signals; raise `stop_grace_period` where draining needs longer than the default. Leave `restart` unset: a crashing service must surface as a failed `up` or a dirty log audit, and a restart loop masks exactly the defect the from-scratch cycle exists to catch.

## Healthcheck Design

- Probe capability, not existence: a store answers a real query, an application answers its readiness endpoint (which verifies its own dependencies), a queue accepts a ping. A running process with a dead dependency must report unhealthy.
- A `CMD` probe executes inside the container, so the final image must actually contain the probe binary: minimal bases usually ship no HTTP client, so bundle a tiny health command with the application (as the anatomy example does) or probe through the runtime itself.
- `start_period` covers the honest warm-up: probe failures inside it do not count toward `retries`, and one success ends the grace early; `start_period + retries * interval` bounds how long `up` waits before declaring failure.
- Dependents always use `depends_on` with `condition: service_healthy`. Start order without health conditions is a race, not an ordering.

## The Entry Point

One executable (script or make target) implements the verb contract from the skill. Its first act derives the project name, which is what isolates parallel instances:

```sh
root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
export COMPOSE_PROJECT_NAME="$(basename "$root" \
  | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' \
  | sed 's/^-*//;s/-*$//')"
```

The trailing sed strips the hyphens the sanitizer mints at the edges (the newline becomes one); a compose project name must start with a lowercase letter or digit. The definition must never carry a top-level `name:` key, and the entry point aborts if the sanitized name comes out empty: the exported variable wins when set, but a committed name or an empty derivation silently funnels every instance into one shared namespace, defeating the isolation the naming exists for.

- Compose namespaces containers, networks and volumes by project name. Sibling worktrees have distinct basenames, so their environments are disjoint without any caller flag.
- `up` = `docker compose up -d --build --wait`, then seed the default scenario; any unhealthy service fails the verb.
- `down` = `docker compose down -v --remove-orphans` (data volumes included; from-scratch means from scratch).
- `url <service>` = `docker compose port <service> <container-port>` rendered as a base URL.
- `logs` = `docker compose logs --no-color` for the audit.

## Port Strategy

- Never bind fixed host ports and never set `container_name`: both are machine-global names and collide across parallel instances.
- Publish container ports only (`- "8000"`); the kernel assigns a free host port; consumers resolve it through the `url` verb. Inside the network, services address each other by service name and container port; no published port is needed for service-to-service traffic.
- A store the QA protocol must inspect (data effects confirmed at the store) publishes its port like any service and is reached through the `url` verb with the committed secretless default credentials.

## Profiles and Optional Services

Tooling that is not part of the running system (an admin UI, a mail catcher) sits behind a compose profile so `up` stays exactly the system the user gets. Enabling a profile is a local convenience, never a dependency.

## Env Files

- Committed defaults live as interpolation fallbacks in the definition itself, secretless and complete enough for a from-scratch bring-up on a clean machine; a missing override never breaks `up`.
- The untracked local override is the literal `.env` beside the compose file: the only name and place compose auto-loads it from (the project directory is the definition's folder, not the caller's). The ignore pattern for env files already covers it.
