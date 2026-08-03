# Image Build Recipes

How application images are built. Recipes live at workspace/environment/docker/<app>.Dockerfile with build context workspace/, so a recipe can copy from its application directory while staying inside the environment prefix the devops engineer owns.

## Multi-Stage Shape

```dockerfile
FROM <base:exact-tag> AS build
WORKDIR /src
# dependency manifests first: source edits must not bust this layer
COPY apps/api/pyproject.toml apps/api/requirements.txt ./
RUN <install-dependencies>
COPY apps/api/ .
RUN <build-or-compile>

FROM <minimal-base:exact-tag>
RUN addgroup --system app && adduser --system --ingroup app app
WORKDIR /app
COPY --from=build --chown=app:app /src/<artifact> .
USER app
EXPOSE 8000
ENTRYPOINT ["<start-command>"]
```

Rules the shape encodes:

- **Two stages minimum:** toolchains, compilers and dev dependencies never reach the final image. The final stage carries the artifact and its runtime, nothing else.
- **Cache ordering:** copy dependency manifests and install them before copying source. A source-only change then rebuilds in seconds; inverting the order rebuilds dependencies on every edit.
- **Non-root:** create a system user in the final stage and switch to it. A recipe whose final stage runs as root is a review finding, not a preference.
- **Exec-form start command:** the bracketed form is mandatory; the shell form wraps the process in a shell that never forwards the stop signal, so graceful shutdown silently never runs.
- **Exact tags everywhere:** base images pinned to an exact tag (digest pinning is stronger where reproducibility matters most). A floating tag makes yesterday's green build unreproducible today. Where a digest is pinned, pin the multi-arch index digest, never a platform one: the laptop and the runner resolve the same tag to different architectures, and a platform digest breaks whichever side it was not captured on. Leave `platform:` unset so each host runs native.

## Configuration and Secrets

- The final image is environment-free: no mode flags, hostnames or credentials baked via build arguments into runtime behavior. Everything enters as environment variables at start.
- Secrets never enter any layer, including intermediate ones: no secret build arguments, no credential files copied then deleted (deletion does not remove the layer). A secret needed at build time is a design smell to escalate.

## Ignore File

A `.dockerignore` at the build context root (plus a per-recipe `<name>.dockerignore` beside a recipe that needs its own exclusions) keeps the context small and the cache honest: version control metadata, dependency directories, test artifacts, local env files and editor noise never enter the build. A bloated context slows every build and can leak files into layers.

## Frontend Applications

The same shape applies: build stage compiles the client bundle, final stage is a minimal static server (or the bundle is copied into the backend image where the architecture says so). The client receives its runtime configuration (such as the API base URL) through the environment at container start, not baked at build time, or the image stops being build-once-run-anywhere. A static bundle cannot read variables at runtime, so the container start step materializes them: the image's entry script writes the values into a small config file the bundle fetches on load.
