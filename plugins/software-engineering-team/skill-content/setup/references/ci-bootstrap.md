# CI Bootstrap

Project setup does not materialize CI. The fast lane merges a pull request
only on green provider checks, so a project without a pull request workflow
adds one before its first merge.

- Copy the packaged `templates/ci-tests.yml` to `.github/workflows/tests.yml`.
- Replace `{{test_command}}` with the project's test command.
- Delete the `{{dependency_audit_job}}` and `{{environment_smoke_job}}`
  lines, or replace each with a job the project runs.
- Commit it with the change that needs it; no double-braced token may reach
  the repository.

A workflow counts when it is a `.yml` or `.yaml` file directly in
`.github/workflows/`, its top-level `on` names `pull_request` or
`pull_request_target`, and that event lists no `types` or includes
`synchronize`.

Project setup substitutes `{{project_local_ignores}}` in the packaged
gitignore template from the product's declared project-local roots. Route
dependency advisories through the `deliver` entry as a fix-atomic lockfile
bump.
