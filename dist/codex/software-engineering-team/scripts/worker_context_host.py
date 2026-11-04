"""Native instruction surfaces and hook rewrite contracts."""


def settings(host):
    return {
        'claude': {'companion': 'CLAUDE.user.md', 'instructions': ['CLAUDE.md', 'AGENTS.md'], 'allow_rewrite': False},
        'codex': {'companion': 'AGENTS.user.md', 'instructions': ['AGENTS.md', 'AGENTS.override.md'], 'allow_rewrite': True},
    }[host]
