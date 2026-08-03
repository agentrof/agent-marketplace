# CI Bootstrap

When the repository has no PR-triggered test workflow in its CI directory
(`.github/workflows/` on GitHub), offer to add the file printed by `"$RUN"
path "$TEAM" templates/ci-tests.yml`.

- Use the configured test command for the test placeholder.
- Build one dependency-audit command per configured stack, anchored at its
  lockfile. Python FastAPI uses `pip-audit`; React TypeScript runs `npm audit
  --audit-level=high` in the frontend directory. Chain two stacks with `&&`.
- Use the configured environment command. Include `environment_smoke` only
  when an up-then-down probe passes now. Otherwise omit the job, note that a
  later setup run will append it, and treat its later absence as a gap once
  the probe passes.
- Keep dependency audit. Route advisories through the request entry as a
  fix-atomic lockfile bump.
