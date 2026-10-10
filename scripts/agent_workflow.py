"""Local reviewer/implementer handoffs. Standard library only; no app imports."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

DEFAULT_ROOT = Path(__file__).resolve().parents[1] / '.agent-workflow'
STATES = {'awaiting_implementation', 'implementing', 'awaiting_review', 'reviewing', 'complete', 'blocked'}
APPLICATION_DIRS = ('app/', 'ui/src/', 'config/')


class WorkflowError(ValueError):
    pass


def utc():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n').encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def atomic(path, data):
    """Publish only complete files; a crash can leave an ignored, inert temp."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('xb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def immutable(path, data):
    """Called only under the global exclusive lock. Never overwrite history."""
    path = Path(path)
    if path.exists():
        if path.read_bytes() != data:
            raise WorkflowError(f'Immutable artifact differs: {path}')
    else:
        atomic(path, data)


def git(worktree, *args):
    result = subprocess.run(['git', '-C', str(worktree), *args], capture_output=True, check=False)
    if result.returncode:
        raise WorkflowError(result.stderr.decode(errors='replace').strip())
    return result.stdout.decode('utf-8', errors='replace').strip()


def dirty(worktree):
    names = set(git(worktree, 'diff', '--name-only', 'HEAD', '-z').split('\0'))
    names.update(git(worktree, 'ls-files', '--others', '--exclude-standard', '-z').split('\0'))
    return {name: digest((Path(worktree) / name).read_bytes()) if (Path(worktree) / name).is_file() else 'missing'
            for name in names if name}


def validate_id(value):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', value):
        raise WorkflowError('IDs must be lowercase letters/digits/hyphens/underscores, at most 80 characters.')


class Workflow:
    def __init__(self, root=DEFAULT_ROOT):
        self.root = Path(root).resolve()
        self.tasks = self.root / 'tasks'

    @contextmanager
    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        lock = self.root / '.transition.lock'
        deadline = time.monotonic() + 10
        while True:
            try:
                lock.mkdir()
                break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise WorkflowError(f'Exclusive transition lock held: {lock}. Inspect owner.json; never remove a live lock.')
                time.sleep(.05)
        try:
            atomic(lock / 'owner.json', encoded({'pid': os.getpid(), 'created_at': utc(), 'token': uuid.uuid4().hex}))
            yield
        finally:
            # Normal exits release the lock. A terminated process leaves it for owner recovery.
            (lock / 'owner.json').unlink(missing_ok=True)
            lock.rmdir()

    def path(self, task_id):
        validate_id(task_id)
        return self.tasks / task_id

    def load(self, task_id):
        path = self.path(task_id)
        if not (path / 'state.json').exists():
            raise WorkflowError('Task has no committed state; incomplete creation is not actionable.')
        state = read_json(path / 'state.json')
        if state['task_id'] != task_id or state['state'] not in STATES:
            raise WorkflowError('Invalid task identity or state.')
        for name, expected in state['artifacts'].items():
            if digest((path / name).read_bytes()) != expected:
                raise WorkflowError(f'Immutable artifact changed: {name}')
        return state

    def status(self, task_id=None, stale_seconds=3600):
        if task_id:
            state = self.load(task_id)
            claim = state.get('claim')
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(claim['claimed_at'])).total_seconds() if claim else None
            return {**state, 'claim_age_seconds': age, 'stale_claim': age is not None and age >= stale_seconds,
                    'stale_policy': 'Never reclaim automatically; require owner intervention.'}
        result = []
        for path in sorted(self.tasks.glob('*')):
            if path.is_dir():
                result.append(self.status(path.name, stale_seconds) if (path / 'state.json').exists()
                              else {'task_id': path.name, 'state': 'incomplete_creation', 'actionable': False})
        return result

    def commit(self, state):
        atomic(self.path(state['task_id']) / 'state.json', encoded(state))

    def artifact(self, state, name, data):
        immutable(self.path(state['task_id']) / name, data)
        state['artifacts'][name] = digest(data)

    def apply(self, action, task_id, round_no, expected_state, request_id, **payload):
        validate_id(task_id)
        validate_id(request_id)
        if expected_state not in STATES and expected_state != 'absent':
            raise WorkflowError('Invalid expected state.')
        fingerprint = digest(encoded([action, task_id, round_no, expected_state, payload]))
        with self.lock():
            path = self.path(task_id)
            existing = (path / 'state.json').exists()
            state = self.load(task_id) if existing else None
            if state and request_id in state['requests']:
                old = state['requests'][request_id]
                if old['fingerprint'] != fingerprint:
                    raise WorkflowError('Request ID reused with different arguments.')
                return {**old['result'], 'idempotent': True}
            if action == 'create':
                if existing or expected_state != 'absent' or round_no != 1:
                    raise WorkflowError('Create requires an absent task and round 1.')
                if any(item.get('state') != 'complete' for item in self.status() if item['task_id'] != task_id):
                    raise WorkflowError('One active task at a time, including blocked/incomplete tasks.')
                criteria = payload['criteria']
                if not criteria or len({c['id'] for c in criteria}) != len(criteria):
                    raise WorkflowError('Provide unique acceptance criteria.')
                for c in criteria:
                    validate_id(c['id'])
                    if not c.get('description'):
                        raise WorkflowError('Criterion descriptions are required.')
                if not payload['requirement'].strip() or not payload['prompt'].strip():
                    raise WorkflowError('Approved requirement and initial prompt are required.')
                if not payload['required_checks'] or len(set(payload['required_checks'])) != len(payload['required_checks']):
                    raise WorkflowError('At least one uniquely named required verification check is required.')
                worktree = str(Path(payload['worktree']).resolve())
                state = {'task_id': task_id, 'round': 1, 'state': 'awaiting_implementation',
                         'created_at': utc(), 'updated_at': utc(), 'claim': None, 'artifacts': {}, 'requests': {},
                         'criteria': criteria, 'required_checks': payload['required_checks'],
                         'pr_url': payload['pr_url'], 'pr_number': payload['pr_number'], 'worktree': worktree,
                         'preexisting_changes': dirty(worktree), 'max_rounds': 5, 'events': []}
                self.artifact(state, 'requirement.md', payload['requirement'].encode())
                self.artifact(state, 'prompts/001.md', payload['prompt'].encode())
            else:
                if not state or state['round'] != round_no or state['state'] != expected_state:
                    raise WorkflowError('Task ID, round or expected current state does not match.')
                self.transition(state, action, payload)
            state['updated_at'] = utc()
            result = {'task_id': task_id, 'round': state['round'], 'state': state['state']}
            if state.get('claim'):
                result['ownership_token'] = state['claim']['token']
            state['events'].append({'action': action, 'round': round_no, 'request_id': request_id, 'at': state['updated_at'],
                                    'owner_note': payload.get('owner_note'), 'claim': state.get('claim')})
            state['requests'][request_id] = {'fingerprint': fingerprint, 'result': result}
            if action == 'accept':
                self.exact_commit(state, self.submitted(state))
            self.commit(state)
            return result

    def owner(self, state, payload, role):
        claim = state.get('claim')
        if not claim or claim['role'] != role or claim['token'] != payload.get('token'):
            raise WorkflowError('Result requires the current claim ownership token.')

    def submitted(self, state):
        return read_json(self.path(state['task_id']) / f"implementation-reports/{state['round']:03}.json")

    def exact_commit(self, state, report):
        worktree = state['worktree']
        if str(Path(report['worktree_path']).resolve()) != worktree:
            raise WorkflowError('Submitted worktree differs from designated worktree.')
        sha = report['commit_sha']
        if not re.fullmatch(r'[0-9a-f]{40}', sha) or git(worktree, 'rev-parse', 'HEAD') != sha:
            raise WorkflowError('Submitted commit changed or is not an exact 40-character SHA.')
        if git(worktree, 'branch', '--show-current') != report['branch']:
            raise WorkflowError('Submitted branch changed.')
        now = dirty(worktree)
        for name, value in state['preexisting_changes'].items():
            if now.get(name) != value:
                raise WorkflowError(f'Unrelated preexisting workspace change was modified: {name}')
        for name, value in now.items():
            if name.startswith(APPLICATION_DIRS) and state['preexisting_changes'].get(name) != value:
                raise WorkflowError(f'Uncommitted application work cannot be submitted/reviewed: {name}')

    def transition(self, state, action, p):
        current, number = state['state'], state['round']
        if action in ('claim-implementation', 'claim-review'):
            role = 'implementer' if action == 'claim-implementation' else 'reviewer'
            required = 'awaiting_implementation' if role == 'implementer' else 'awaiting_review'
            if current != required or state.get('claim'):
                raise WorkflowError('Duplicate claim or incorrect claim state.')
            if role == 'reviewer':
                self.exact_commit(state, self.submitted(state))
            state['claim'] = {'role': role, 'token': uuid.uuid4().hex, 'claimed_at': utc(), 'owner': p['owner']}
            state['state'] = 'implementing' if role == 'implementer' else 'reviewing'
        elif action == 'publish-implementation':
            if current != 'implementing':
                raise WorkflowError('Implementation publication requires implementing state.')
            self.owner(state, p, 'implementer')
            report = p['report']
            required = {'task_id', 'round', 'pr_url', 'pr_number', 'branch', 'worktree_path', 'commit_sha',
                        'implemented_requirements', 'validation_results', 'unverified_checks', 'blockers'}
            if not required.issubset(report) or report['task_id'] != state['task_id'] or report['round'] != number:
                raise WorkflowError('Implementation report fields/identity/round are invalid.')
            if (report['pr_url'], report['pr_number']) != (state['pr_url'], state['pr_number']):
                raise WorkflowError('Implementation report targets a different PR.')
            if any(not isinstance(report[key], list) for key in ('implemented_requirements', 'validation_results', 'unverified_checks', 'blockers')):
                raise WorkflowError('Implementation report collection fields must be arrays.')
            if not set(report['implemented_requirements']).issubset({c['id'] for c in state['criteria']}):
                raise WorkflowError('Implemented requirements must reference approved criterion IDs.')
            checks = report['validation_results']
            if (any(not isinstance(item, dict) or not item.get('name')
                    or item.get('status') not in {'pass', 'fail', 'unverified'}
                    or not isinstance(item.get('verified'), bool) or not item.get('evidence') for item in checks)
                    or len({item['name'] for item in checks}) != len(checks)):
                raise WorkflowError('Validation results need unique names, status, verification flag and evidence.')
            self.exact_commit(state, report)
            self.artifact(state, f'implementation-reports/{number:03}.json', encoded(report))
            state.update(state='awaiting_review', claim=None)
        elif action in ('request-round', 'accept'):
            if current != 'reviewing':
                raise WorkflowError('Review result requires reviewing state.')
            self.owner(state, p, 'reviewer')
            submitted = self.submitted(state)
            self.exact_commit(state, submitted)
            report = p['report']
            expected_ids = {c['id'] for c in state['criteria']}
            assessments = report.get('criteria', [])
            ids = [item.get('id') for item in assessments]
            if (report.get('task_id') != state['task_id'] or report.get('round') != number
                    or report.get('reviewed_commit') != submitted['commit_sha']
                    or set(ids) != expected_ids or len(ids) != len(expected_ids)
                    or any(item.get('status') not in {'pass', 'fail', 'unverified'} or not item.get('evidence') for item in assessments)
                    or not isinstance(report.get('findings'), list) or not isinstance(report.get('unverified_checks'), list)):
                raise WorkflowError('Review must identify the submitted commit and assess every criterion with evidence.')
            if action == 'accept':
                checks = submitted['validation_results']
                passed = {item.get('name') for item in checks if item.get('status') == 'pass' and item.get('verified') is True and item.get('evidence')}
                if (any(item['status'] != 'pass' or item.get('verified') is not True for item in assessments)
                        or report['findings'] or report['unverified_checks'] or submitted['unverified_checks'] or submitted['blockers']
                        or any(item['status'] != 'pass' or item['verified'] is not True for item in checks)
                        or not set(state['required_checks']).issubset(passed)):
                    raise WorkflowError('Acceptance requires verified criteria, required checks, and no open findings/unverified checks/blockers.')
            else:
                if not p.get('prompt', '').strip():
                    raise WorkflowError('A correction prompt is required.')
                if not set(p.get('criterion_ids', [])).issubset(expected_ids) or not p.get('criterion_ids'):
                    raise WorkflowError('Corrections must reference existing criteria; new requirements are prohibited.')
            self.artifact(state, f'review-reports/{number:03}.json', encoded(report))
            if action == 'request-round':
                self.artifact(state, f'correction-prompts/{number:03}.md', p['prompt'].encode())
            if action == 'accept':
                self.artifact(state, 'SUCCESS.json', encoded({'task_id': state['task_id'], 'round': number,
                    'reviewed_commit': submitted['commit_sha'], 'review_report': f'review-reports/{number:03}.json',
                    'pr_url': state['pr_url'], 'meaning': 'Verified acceptance only; merge/deployment/production writes are not authorized.'}))
                state.update(state='complete', claim=None)
            elif number >= state['max_rounds']:
                self.block(state, 'Maximum five implementation rounds reached; owner intervention required.', 'awaiting_implementation')
            else:
                self.artifact(state, f'prompts/{number + 1:03}.md', p['prompt'].encode())
                state.update(state='awaiting_implementation', round=number + 1, claim=None)
        elif action == 'block':
            if current == 'complete' or current == 'blocked':
                raise WorkflowError('Cannot block completed/already blocked task.')
            if state.get('claim'):
                self.owner(state, p, state['claim']['role'])
            elif not p.get('owner_note'):
                raise WorkflowError('Blocking an unclaimed task requires an explicit owner note.')
            self.block(state, p['reason'], 'awaiting_review' if current in {'reviewing', 'awaiting_review'} else 'awaiting_implementation')
        elif action == 'resume':
            if current != 'blocked' or not p.get('owner_note'):
                raise WorkflowError('Resume requires blocked state and explicit human authorization note.')
            if number >= state['max_rounds'] and state['resume_state'] == 'awaiting_implementation':
                raise WorkflowError('Round limit reached. Owner must decide scope/continuation; do not start another round.')
            state.update(state=state['resume_state'], claim=None)
        else:
            raise WorkflowError('Unknown transition.')

    def block(self, state, reason, resume_state):
        if not reason.strip():
            raise WorkflowError('Blocker reason required.')
        index = 1 + len([x for x in state['artifacts'] if x.startswith('blockers/')])
        blocker = {'task_id': state['task_id'], 'round': state['round'], 'reason': reason, 'requires_owner': True}
        self.artifact(state, f'blockers/{index:03}.json', encoded(blocker))
        # BLOCKED.json is an immutable index, not a replaceable history entry.
        if 'BLOCKED.json' not in state['artifacts']:
            self.artifact(state, 'BLOCKED.json', encoded({'task_id': state['task_id'], 'history': 'blockers/', 'authority': 'state.json'}))
        state.update(state='blocked', claim=None, resume_state=resume_state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    commands = parser.add_subparsers(dest='command', required=True)
    status = commands.add_parser('status')
    status.add_argument('--task-id')
    status.add_argument('--stale-seconds', type=int, default=3600)
    for action in ('create', 'claim-implementation', 'claim-review', 'publish-implementation', 'request-round', 'accept', 'block', 'resume'):
        p = commands.add_parser(action)
        p.add_argument('--task-id', required=True)
        p.add_argument('--round', type=int, required=True)
        p.add_argument('--expected-state', required=True)
        p.add_argument('--request-id', required=True)
        if action == 'create':
            p.add_argument('--requirement', type=Path, required=True)
            p.add_argument('--prompt', type=Path, required=True)
            p.add_argument('--criteria', type=Path, required=True)
            p.add_argument('--required-check', action='append', default=[])
            p.add_argument('--worktree', required=True)
            p.add_argument('--pr-url', required=True)
            p.add_argument('--pr-number', type=int, required=True)
        elif action.startswith('claim-'):
            p.add_argument('--owner', required=True)
        else:
            p.add_argument('--token', default='')
            if action in ('accept', 'request-round', 'publish-implementation'):
                p.add_argument('--report', type=Path, required=True)
            if action == 'request-round':
                p.add_argument('--prompt', type=Path, required=True)
                p.add_argument('--criterion-id', action='append', required=True)
            if action in ('block', 'resume'):
                p.add_argument('--owner-note', default='')
            if action == 'block':
                p.add_argument('--reason', required=True)
    args = vars(parser.parse_args())
    flow = Workflow(args.pop('root'))
    command = args.pop('command')
    try:
        if command == 'status':
            result = flow.status(args['task_id'], args['stale_seconds'])
        else:
            task_id, round_no, expected_state, request_id = (args.pop(k) for k in ('task_id', 'round', 'expected_state', 'request_id'))
            for key in ('requirement', 'prompt'):
                if key in args:
                    args[key] = args[key].read_text(encoding='utf-8')
            for key in ('criteria', 'report'):
                if key in args:
                    args[key] = read_json(args[key])
            if 'required_check' in args:
                args['required_checks'] = args.pop('required_check')
            if 'criterion_id' in args:
                args['criterion_ids'] = args.pop('criterion_id')
            result = flow.apply(command, task_id, round_no, expected_state, request_id, **args)
        print(json.dumps(result, indent=2))
        return 0
    except (WorkflowError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f'Workflow refused: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
