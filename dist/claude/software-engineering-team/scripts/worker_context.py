"""Build and inject bounded project rules before native worker launch."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys

import atomic_file
import task_lane as lane
import project_config
from worker_context_host import settings

BRIEF = 'SWE_WORKER_BRIEF\n'
PACKET = 'SWE_WORKER_PACKET\n'
MAX_BYTES = 49152
FIELDS = {'task', 'worktree', 'outcome', 'write_scope', 'authority', 'evidence', 'stop', 'sources'}
SPAWN = re.compile(r'(?:^|[._])(?:spawn_agent|Agent|Task)$')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def source(path: Path, root: Path):
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve()) or path.is_symlink() or not resolved.is_file():
        raise ValueError('worker source must be a regular file within its owner root')
    if resolved.stat().st_size > MAX_BYTES:
        raise ValueError('worker source exceeds budget; curate the source without dropping binding rules')
    text = resolved.read_text(encoding='utf-8')
    return {'path': str(resolved), 'sha256': digest(text), 'content': text}


def prepare(parent: Path, brief: dict, host: str):
    if not isinstance(brief, dict) or set(brief) != FIELDS:
        raise ValueError('worker brief requires task, worktree, outcome, write_scope, authority, evidence, stop and sources')
    for name in FIELDS - {'write_scope', 'sources'}:
        if not isinstance(brief[name], str) or not brief[name].strip():
            raise ValueError(f'worker brief {name} must be nonempty text')
    if not Path(brief['worktree']).is_absolute():
        raise ValueError('worker worktree must be an absolute path')
    tree = Path(brief['worktree']).resolve(strict=True)
    common = ('git', 'rev-parse', '--path-format=absolute', '--git-common-dir')
    if lane.run(*common, cwd=parent) != lane.run(*common, cwd=tree):
        raise ValueError('worker worktree must belong to the parent repository')
    for name in ('write_scope', 'sources'):
        if not isinstance(brief[name], list) or any(not isinstance(p, str) or not p.strip() for p in brief[name]):
            raise ValueError(f'worker brief {name} must be a list of relative paths')
        for p in brief[name]:
            if Path(p).is_absolute() or not (tree / p).resolve().is_relative_to(tree):
                raise ValueError('worker paths must remain inside the assigned worktree')
    config = project_config.load(tree / 'workspace/config.json')
    errors = project_config.base_errors(config)
    if errors:
        raise ValueError('worker project configuration requires reconciliation: ' + '; '.join(errors))
    plugin = Path(__file__).resolve().parents[1]
    task = lane.task_path(tree, brief['task'])
    paths = [task, tree / 'workspace/config.json', tree / 'workspace/memory/me.md',
             tree / 'workspace/memory/profile.md', tree / 'workspace/memory/agent-marketplace.md',
             tree / 'workspace/memory/current-plan.md', tree / 'workspace/memory/product-decisions.md']
    companion = settings(host)['companion']
    paths.append(tree / companion)
    # Authored root and nested instructions can carry constraints absent from memory.
    for scope in ['', *brief['write_scope']]:
        directory = (tree / scope).resolve()
        if not directory.is_dir():
            directory = directory.parent
        while directory.is_relative_to(tree):
            for name in settings(host)['instructions']:
                path = directory / name
                if path.is_file():
                    paths.append(path)
            if directory == tree:
                break
            directory = directory.parent
    paths.extend(tree / p for p in brief['sources'])
    sources = [source(plugin / 'constitution.md', plugin),
               source(plugin / 'skill-content/deliver/data/companion-policy.json', plugin)]
    sources.extend(source(path, tree) for path in dict.fromkeys(paths))
    props = lane.task_props(task)
    methods = lane.required_methods(tree, props, set(brief['write_scope']), lane.task_body(task))
    for name in methods:
        sources.append(source(plugin / 'skill-content' / name / 'SKILL.md', plugin))
    value = {'brief': brief, 'host': host, 'sources': sources,
             'limits': 'Inherited authority only. No new delegation or shared configuration changes. '
                       'Apply supplied rules. Read linked procedure depth as needed. '
                       'Report missing capabilities and source conflicts before dependent work. '
                       'A source delivery receipt does not establish semantic compliance.'}
    for row in sources:
        if digest(Path(row['path']).read_text(encoding='utf-8')) != row['sha256']:
            raise ValueError('worker rule source changed during preparation; retry')
    text = PACKET + encoded(value)
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('worker context exceeds budget; narrow the task or curate its sources, never truncate rules')
    return text


def transform(payload: dict, host: str):
    if not SPAWN.search(str(payload.get('tool_name', ''))):
        return {}
    parent = Path(payload.get('cwd') or '.').resolve()
    parent = next((p for p in (parent, *parent.parents) if (p / '.git').exists()), parent)
    if not (parent / 'workspace/config.json').exists():
        return {}
    data = payload.get('tool_input')
    if not isinstance(data, dict):
        raise ValueError('worker launch has no structured tool input')
    key = 'prompt' if 'prompt' in data else 'message'
    message = data.get(key)
    if not isinstance(message, str):
        raise ValueError('worker launch needs a textual brief')
    if message.startswith(BRIEF):
        brief = json.loads(message[len(BRIEF):])
    elif message.startswith(PACKET):
        brief = json.loads(message[len(PACKET):])['brief']
    else:
        raise ValueError('start worker prompt/message with SWE_WORKER_BRIEF newline and the required JSON brief')
    packet = prepare(parent, brief, host)
    receipt = {'schema_version': 1, 'host': host, 'task': brief['task'],
               'session_id': payload.get('session_id'), 'tool_use_id': payload.get('tool_use_id'),
               'packet_sha256': digest(packet), 'packet_bytes': len(packet.encode('utf-8')),
               'source_delivery': 'prepared_for_host', 'at': lane.now()}
    directory = atomic_file.real_directory(parent, Path('.agentrof/agent-marketplace/.runtime/worker-context'))
    atomic_file.replace_text(directory / (digest(encoded(receipt)) + '.json'), encoded(receipt) + '\n')
    output = {'hookEventName': 'PreToolUse', 'updatedInput': {**data, key: packet}}
    if settings(host)['allow_rewrite']:
        output['permissionDecision'] = 'allow'
    return {'hookSpecificOutput': output}


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError('worker hook payload must be an object')
        result = transform(payload, sys.argv[1])
    except Exception as error:
        result = {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                  'permissionDecisionReason': f'Worker context unavailable: {error}'}}
    print(json.dumps(result))


if __name__ == '__main__':
    main()
