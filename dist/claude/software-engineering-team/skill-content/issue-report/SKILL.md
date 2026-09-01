---
name: issue-report
description: Prepare a stateless GitHub issue in chat and file the approved payload only to the Agent Marketplace repository.
exposure: entry
project_scope: external
---

# Issue Report

Issue reporting is an external, stateless support workflow. It never creates or
updates project files, workspace documents, runtime state, caches, build
artifacts, Git state or local issue records. Before approval, the current chat
is the only draft state. After filing, the GitHub issue and returned URL are the
only durable record.

## When to Use

- A defect or improvement in Agent Marketplace needs to be reported upstream.
- The user wants to review the exact GitHub payload before it is filed.

## Procedure

1. Prepare the issue from the current conversation. If evidence is missing and
   the user has placed a project in scope, inspect files, logs and Git state
   read-only. Do not run tests, builds, setup or any command that may write a
   cache or artifact. Do not invent missing facts.
2. Remove secrets, tokens, absolute local paths and unrelated project details.
   Present the exact payload in chat with this shape:

   - Target: `agentrof/agent-marketplace`
   - Title
   - Summary
   - Reproduction or Motivation
   - Expected Behavior
   - Actual Behavior
   - Impact
   - Evidence and Context

   Use `Unknown` or `Not observed` where the available evidence is incomplete.
3. Immediately after the complete preview, present one declared choice gate:
   `Open issue`, `Revise` or `Cancel`. Never treat an earlier request to report
   the problem as approval of an unseen payload.
   - `Open issue` approves only the exact displayed title and body.
   - `Revise` changes the payload in chat, displays it again and requires a new
     choice gate.
   - `Cancel` ends without external or local mutation.
4. After `Open issue`, invoke the packaged `scripts/file_issue.py` exactly once
   with `--title`. Pass the approved Markdown body through standard input. Do
   not create a body file, temporary file, report file or local receipt.
5. Report success only when the filer exits successfully with a canonical
   `https://github.com/agentrof/agent-marketplace/issues/<number>` URL. Say
   `Opened #<number>: <url>`. For exit 2 say `Not opened` with the reason. For
   exit 3 say `Outcome unknown, do not retry automatically` and preserve the
   diagnostic in chat. Never retry a filing attempt automatically.

This entry does not require project setup, a Git repository or a project
workspace. It remains separate from Requirement and Delivery flows.
