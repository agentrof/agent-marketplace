#!/usr/bin/env python3
"""The oldest Python this plugin runs on, and the one message below it.

hook_launcher.py checks this floor before it runs any hook, so this file
parses under Python 3.9 grammar and imports only sys: any python3 can read
it. MINIMUM is the one version CI tests, tools/data/ci-test-policy.json's
`python`, which a test pins to it.
"""

import sys

MINIMUM = (3, 14)


def found():
    """The running Python's version as major.minor.micro."""
    return "%d.%d.%d" % tuple(sys.version_info[:3])


def supported():
    """Whether the running Python is at or above the floor."""
    return tuple(sys.version_info[:2]) >= MINIMUM


def message():
    """What a session below the floor tells the user, fix and restart included."""
    return (
        "This Agent Marketplace plugin needs Python %d.%d or newer, but `python3`"
        " is Python %s at %s. Install Python %d.%d or newer (macOS:"
        " `brew install python3`) so that `python3` on PATH runs it, then restart"
        " the session." % (MINIMUM + (found(), sys.executable or "an unknown path") + MINIMUM)
    )
