# Cluster Patterns (Kubernetes, Helm, kind)

The rules for an environment that runs on a local cluster, such as kind with
Helm charts, instead of or beside compose. The environment command, verbs,
seed scenarios and log audit of the skill apply unchanged. Every unchecked box
is a finding.

## Rollout

- [ ] A file mounted with `subPath` never refreshes: a hash of the config and secrets it carries sits in a pod-template label, so a change rolls the pod.
- [ ] A single-writer volume uses the `Recreate` strategy; switching to it drops the defaulted `rollingUpdate` block.

## State

- [ ] Stateful services (identity provider, object store, secret store, database) run on kept volumes that survive `down` and `up`; the identity provider stores its users in the database.
- [ ] Database roles for dependents are created by the database's own idempotent setup on every bring-up, never by a dependent's init container.
- [ ] A dependent that creates its schema only at startup is restarted when its store is replaced.
- [ ] Generated credentials survive `down` and `up`; they rotate only on a deliberate reset.
- [ ] Secrets reach a container as mounted files, never as environment variables or arguments.

## Images

- [ ] Minimal images lack common tools and flags: start scripts are run on the real image before they ship, not on a developer shell.
- [ ] With a read-only root filesystem, scratch space is `/dev/shm` or an `emptyDir`.

## Dev Environment

- [ ] One long-lived dev environment converges in place on `up`; it is not rebuilt from nothing on every run.
- [ ] Images come from a local registry, pinned by digest, built offline from lock files.
- [ ] The warm bring-up has a measured time budget; a run over it is reported.
- [ ] A readiness wait fails fast on a repeating error and dumps the redacted state of every not-ready workload (events, last log lines, never a secret).
