"""Test levels.

A unit test proves its rule in process, on synthetic input or files in its
own temporary directory. ``@integration`` marks a test, or every test of a
class, that starts a process (Git, a shipped script, a host CLI) or writes
outside its own temporary directory. The local gate runs unit tests only;
pull request CI runs both, and every worker fails an unmarked test that
crosses that boundary.
"""


def integration(target):
    target._test_level = "integration"
    return target
