# Docker Compose Review Checklist

Checkbox assertions for reviewing environment-owned diffs (the workspace/environment/ prefix). Every unchecked box is a finding; report it with file, line, and the failing assertion.

## Definition

- [ ] Every application, service and store the architecture delta declares appears in the compose definition; nothing declared is missing, nothing undeclared was added
- [ ] Every service carries a healthcheck that probes real capability (query answered, readiness endpoint, ping accepted), not process existence
- [ ] Every dependency is `depends_on` with `condition: service_healthy`, never bare start ordering
- [ ] No fixed host-port bindings; container ports only, resolved through the `url` verb
- [ ] No `container_name`, no `external:` volumes, no other machine-global names
- [ ] No top-level `name:` key; the project name comes only from the entry point derivation
- [ ] Data lives in named volumes; teardown with volumes removes all of it
- [ ] Optional tooling sits behind a profile; plain `up` is exactly the system the user gets

## Images

- [ ] Base images pinned to exact tags; zero floating tags anywhere
- [ ] Multi-stage: toolchain and dev dependencies absent from the final stage
- [ ] Final stage runs as a dedicated non-root user
- [ ] Start command in exec form (the shell form never forwards the stop signal)
- [ ] Dependency manifests copied and installed before source (cache ordering)
- [ ] An ignore file keeps version control metadata, dependency dirs, artifacts and local env files out of the build context

## Config and Secrets

- [ ] All runtime configuration enters via environment variables; nothing environment-specific baked into any image
- [ ] No secret in any layer, build argument, committed file or default env value
- [ ] Committed env defaults are secretless and sufficient for a clean-machine bring-up
- [ ] Log output contains no credentials or secrets

## Entry Point

- [ ] All five verbs (`up`, `down`, `seed`, `logs`, `url`) implemented and matching the configured environment command (env_command)
- [ ] Project name derived from the sanitized working-tree basename inside the entry point, never hardcoded and never caller-supplied
- [ ] `up` gates on health and seeds the default scenario; nonzero exit on any unhealthy service
- [ ] `down` removes containers, networks and data volumes

## Seed Data

- [ ] Every scenario in the registry; runner refuses unknown names
- [ ] Scenarios deterministic (fixed ids, fixed timestamps, seeded randomness only) and re-run converges
- [ ] Domain content lives in application loaders, not under the environment prefix
- [ ] Contract document updated: verb usage, scenario catalog, tolerated-warning record

## Traceability

- [ ] A change to the service or store set traces to an approved architecture delta
- [ ] The diff stays inside the environment prefix
