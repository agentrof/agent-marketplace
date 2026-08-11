"""Declare tracked contract v5 and machine-local environment projection."""


def migrate(project, context):
    return {
        "contract": "<workspace>/config.json#agent_marketplace",
        "runtime": ".agentrof",
        "portable_gate": ".github/agentrof/vault-gate.pyz",
        "local_projections": "host-native",
    }
