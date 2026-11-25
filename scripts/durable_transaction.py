"""Fail-closed cross-repository journal core, independent of transport.

A backend must implement an exclusive distributed lock, conditional writes,
verified immutable assets, and transaction-marked commit inspection. This
module does not perform network operations or enable production publication.
"""
import base64
import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import tempfile
from pathlib import Path


def _encode(data): return base64.b64encode(data).decode('ascii')
def _decode(data): return base64.b64decode(data, validate=True)


def _digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _save(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.journal-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(document, stream, sort_keys=True, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(name): os.unlink(name)


@contextlib.contextmanager
def _local_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + '.lock', 'a') as handle:
        try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error: raise RuntimeError('本地发布事务正在运行') from error
        yield


def _load(plan, path):
    token = plan.get('id')
    if not isinstance(token, str) or token == 'free' or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', token):
        raise ValueError('事务标识无效')
    path = Path(path)
    if path.exists():
        journal = json.loads(path.read_text(encoding='utf-8'))
        if journal.get('plan_hash') != _digest(plan): raise ValueError('事务计划与记录不一致')
        return journal
    journal = {'format': 1, 'plan': plan, 'plan_hash': _digest(plan), 'token': plan['id'] + '_' + _digest(plan)[:16] + '_' + secrets.token_hex(16),
               'status': 'preparing', 'repositories': {}}
    _save(path, journal)
    return journal


def publish(plan, path, remote):
    with _local_lock(path):
        journal = _load(plan, path)
        if journal['status'] == 'complete':
            remote.acquire(journal['token'])
            for repo, record in journal['repositories'].items():
                head, data = remote.read(repo)
                if head != record['after_head'] or data != _decode(record['after']):
                    raise RuntimeError('已发布的远端 HEAD 已变化')
            remote.release(journal['token'])
            return
        if journal['status'] in ('rolling_back', 'rolled_back'): raise ValueError('事务正在回滚或已回滚，不能继续发布')
        token = journal['token']
        remote.acquire(token)
        # Recheck every time, including after a crash; no feed changes before
        # every referenced release asset is actually available and verified.
        remote.verify_assets(plan)
        # Capture every repository before the first write. Otherwise a foreign
        # commit to a later repository could be mistaken for our baseline.
        if not journal['repositories']:
            snapshot = {}
            for repo, value in sorted(plan['feeds'].items()):
                head, data = remote.read(repo)
                prepared = plan.get('expected_heads', {}).get(repo)
                if prepared is not None and head != prepared:
                    raise RuntimeError('远端 HEAD 已变化，请重新准备事务')
                snapshot[repo] = {'before_head': head, 'before': _encode(data),
                                  'after': _encode(value.encode('utf-8')), 'after_head': None}
            journal['repositories'] = snapshot
            journal['status'] = 'publishing'
            _save(path, journal)
        elif set(journal['repositories']) != set(plan['feeds']):
            raise RuntimeError('事务快照不完整，停止恢复')
        for repo, value in sorted(plan['feeds'].items()):
            expected = value.encode('utf-8')
            record = journal['repositories'][repo]
            head, data = remote.read(repo)
            if _decode(record['after']) != expected: raise ValueError('事务目标变化')
            if record['after_head'] is not None:
                if head != record['after_head'] or data != expected: raise RuntimeError('远端已变化，停止恢复')
                continue
            if head == record['before_head'] and data == _decode(record['before']):
                head = remote.write(repo, head, expected, token)
            elif data != expected or not remote.is_transaction_commit(repo, head, record['before_head'], token):
                raise RuntimeError('远端状态无法安全恢复')
            record['after_head'] = head
            _save(path, journal)
        # Completion requires reading the externally served state, not merely
        # accepting a successful API write. A failure remains resumable.
        remote.verify_online(plan)
        journal['status'] = 'complete'
        _save(path, journal)
        remote.release(token)


def resume(path, remote):
    journal = json.loads(Path(path).read_text(encoding='utf-8'))
    plan = journal.get('plan')
    if not isinstance(plan, dict) or journal.get('plan_hash') != _digest(plan):
        raise ValueError('事务记录缺少有效的发布计划')
    publish(plan, path, remote)


def rollback(path, remote):
    with _local_lock(path):
        journal = json.loads(Path(path).read_text(encoding='utf-8'))
        token = journal['token']
        remote.acquire(token)
        # Validate all heads before changing any. A matching transaction-marked
        # rollback commit recovers a lost response without trusting content alone.
        records = journal['repositories']
        for repo, record in records.items():
            head, data = remote.read(repo)
            if record.get('rollback_head') is not None:
                if head != record['rollback_head'] or data != _decode(record['before']):
                    raise RuntimeError('已回滚的远端 HEAD 已变化')
            elif record['after_head'] is None:
                if head == record['before_head'] and data == _decode(record['before']):
                    continue
                if data == _decode(record['after']) and remote.is_transaction_commit(repo, head, record['before_head'], token):
                    record['after_head'] = head
                    _save(path, journal)
                    continue
                raise RuntimeError('发布状态不明，停止回滚')
            elif head == record['after_head'] and data == _decode(record['after']):
                continue
            elif journal['status'] == 'rolling_back' and data == _decode(record['before']) and remote.is_transaction_commit(repo, head, record['after_head'], token + ':rollback'):
                record['rollback_head'] = head
                _save(path, journal)
            else:
                raise RuntimeError('远端 HEAD 已变化，拒绝回滚')
        journal['status'] = 'rolling_back'
        _save(path, journal)
        for repo, record in sorted(records.items(), reverse=True):
            if record['after_head'] is not None and record.get('rollback_head') is None:
                record['rollback_head'] = remote.write(repo, record['after_head'], _decode(record['before']), token + ':rollback')
                _save(path, journal)
        journal['status'] = 'rolled_back'
        _save(path, journal)
        remote.release(token)
