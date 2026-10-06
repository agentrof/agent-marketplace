"""Test levels.

A unit test proves its rule in process, on synthetic input or a temporary
tree. ``@integration`` marks a test, or every test of a class, that starts a
process (Git, a shipped script, a host CLI), builds a distribution, or writes
outside its temporary directory. The local gate runs unit tests only; pull
request CI runs both, and its workers fail an unmarked test that crosses
that boundary.
"""


def integration(target):
    target._test_level = "integration"
    return target
