"""Native worker launch transfers rules and rejects incomplete context."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tools.tests.git_fixture import init_repository
from tools.tests.levels import integration

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'plugins/software-engineering-team/scripts'))
sys.path.insert(0, str(ROOT / 'platforms/shared/software-engineering-team/overlay/scripts'))
import worker_context as context


@integration
class WorkerContextTests(unittest.TestCase):
    def test_both_hosts_inject_rules_and_reject_incomplete_or_stale_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            init_repository(root)
            for name in ('me', 'profile', 'agent-marketplace', 'current-plan', 'product-decisions'):
                path = root / f'workspace/memory/{name}.md'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f'# {name}\nPreserve data.\n')
            (root / 'workspace/config.json').write_text(json.dumps({'schema_version': 2, 'team_id': 'software-engineering-team', 'output_language': 'English', 'terminology_language': 'English'}))
            for host in ('AGENTS', 'CLAUDE'):
                (root / f'{host}.user.md').write_text('No external publication.\n')
            task = root / 'workspace/docs/backlog/tasks/tk-001-fixture.md'
            task.parent.mkdir(parents=True)
            task.write_text('---\ntype: task\nid: TK-001\ntitle: Fixture\n---\n# Fixture\nPreserve data.\n')
            brief = {'task': 'TK-001', 'worktree': str(root), 'outcome': 'Inspect fixture',
                     'write_scope': [], 'authority': 'Read only', 'evidence': 'Current source lines',
                     'stop': 'Return findings', 'sources': []}
            for host, tool, key in [('claude', 'Agent', 'prompt'), ('codex', 'spawn_agent', 'message')]:
                payload = {'cwd': str(root), 'tool_name': tool, 'session_id': 'fixture',
                           'tool_use_id': 'launch', 'tool_input': {key: context.BRIEF + json.dumps(brief), 'description': 'fixture'}}
                result = context.transform(payload, host)['hookSpecificOutput']
                updated = result['updatedInput']
                self.assertEqual(updated['description'], 'fixture')
                packet = json.loads(updated[key][len(context.PACKET):])
                self.assertIn('No external publication.', updated[key])
                self.assertTrue(any(row['path'].endswith('constitution.md') for row in packet['sources']))
                self.assertTrue(any(Path(row['path']).name == 'profile.md' for row in packet['sources']))
                self.assertEqual(context.transform({**payload, 'tool_input': updated}, host), {'hookSpecificOutput': result})
                (root / 'workspace/memory/me.md').write_text('New binding rule.\n')
                refreshed = context.transform({**payload, 'tool_input': updated}, host)
                self.assertIn('New binding rule.', refreshed['hookSpecificOutput']['updatedInput'][key])
                for bad in ({**brief, 'authority': ''}, {**brief, 'sources': ['../outside']}, {**brief, 'write_scope': ['/tmp']}):
                    with self.assertRaises(ValueError):
                        context.transform({**payload, 'tool_input': {key: context.BRIEF + json.dumps(bad)}}, host)
                with mock.patch.object(context, 'MAX_BYTES', 20), self.assertRaises(ValueError):
                    context.transform(payload, host)
                run = subprocess.run([sys.executable, str(ROOT / f'dist/{host}/software-engineering-team/scripts/worker_context.py'), host], input=json.dumps({**payload, 'tool_input': {key: 'missing brief'}}), text=True, capture_output=True, check=True)
                self.assertEqual(json.loads(run.stdout)['hookSpecificOutput']['permissionDecision'], 'deny')
            (root / 'workspace/memory/profile.md').unlink()
            with self.assertRaises(FileNotFoundError):
                context.transform(payload, 'codex')
            receipts = list((root / '.agentrof/agent-marketplace/.runtime/worker-context').glob('*.json'))
            self.assertTrue(receipts)
            self.assertNotIn('Preserve data.', receipts[0].read_text())

    def test_auto_mode_launch_keeps_its_input_and_carries_rules_in_a_named_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            init_repository(root)
            for name in ('me', 'profile', 'agent-marketplace', 'current-plan', 'product-decisions'):
                path = root / f'workspace/memory/{name}.md'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f'# {name}\n')
            (root / 'workspace/config.json').write_text(json.dumps({'schema_version': 2, 'team_id': 'software-engineering-team', 'output_language': 'English', 'terminology_language': 'English'}))
            (root / 'CLAUDE.user.md').write_text('No external publication.\n')
            task = root / 'workspace/docs/backlog/tasks/tk-001-fixture.md'
            task.parent.mkdir(parents=True)
            task.write_text('---\ntype: task\nid: TK-001\ntitle: Fixture\n---\n# Fixture\n')
            brief = {'task': 'TK-001', 'worktree': str(root), 'outcome': 'Inspect fixture',
                     'write_scope': [], 'authority': 'Read only', 'evidence': 'Current source lines',
                     'stop': 'Return findings', 'sources': []}
            payload = {'cwd': str(root), 'tool_name': 'Agent', 'permission_mode': 'auto', 'session_id': 'fixture',
                       'tool_use_id': 'launch', 'tool_input': {'prompt': context.BRIEF + json.dumps(brief)}}
            first = context.transform(payload, 'claude')['hookSpecificOutput']
            self.assertEqual(first['permissionDecision'], 'deny')
            self.assertNotIn('updatedInput', first)
            prompt = first['permissionDecisionReason'].split('exact prompt:\n', 1)[1]
            packet = Path(json.loads(prompt[len(context.PACKET_FILE):].split('\n', 1)[0])['path'])
            self.assertIn('No external publication.', packet.read_text())
            relaunch = {**payload, 'tool_input': {'prompt': prompt}}
            self.assertEqual(context.transform(relaunch, 'claude'), {})
            (root / 'workspace/memory/me.md').write_text('New binding rule.\n')
            stale = context.transform(relaunch, 'claude')['hookSpecificOutput']
            self.assertEqual(stale['permissionDecision'], 'deny')
            self.assertIn('New binding rule.', Path(json.loads(stale['permissionDecisionReason'].split('exact prompt:\n', 1)[1][len(context.PACKET_FILE):].split('\n', 1)[0])['path']).read_text())
