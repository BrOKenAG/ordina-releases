#!/usr/bin/env python3
"""Inspect, resume or roll back a persisted release transaction.

    python3 scripts/durable_journal.py --status .transactions/<id>.json
    python3 scripts/durable_journal.py --resume .transactions/<id>.json --postflight
    python3 scripts/durable_journal.py --rollback .transactions/<id>.json

Resume reads the exact plan from the journal; no release arguments are needed.
Rollback restores every repository to its recorded pre-publish bytes and
refuses to touch a repository whose HEAD has changed since the transaction.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import durable_release
import durable_transaction as tx
from release_transaction import ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--status', action='store_true', help='只读取事务状态，不联网')
    action.add_argument('--resume', action='store_true', help='从中断处继续发布')
    action.add_argument('--rollback', action='store_true', help='回滚到事务前状态')
    parser.add_argument('journal', type=Path)
    parser.add_argument('--postflight', action='store_true',
                        help='恢复后运行 check_feeds.py --remote 体检')
    args = parser.parse_args()

    try:
        if args.status:
            document = json.loads(args.journal.read_text(encoding='utf-8'))
            summary = {
                'status': document.get('status'),
                'transaction': document.get('plan', {}).get('id'),
                'repositories': {
                    repo: {'before_head': record.get('before_head'),
                           'after_head': record.get('after_head'),
                           'rollback_head': record.get('rollback_head')}
                    for repo, record in document.get('repositories', {}).items()
                },
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return

        backend, durable, _ = durable_release.backend_from_journal(args.journal)
        if args.rollback:
            tx.rollback(args.journal, backend)
            print('已回滚：' + ', '.join(sorted(durable['feeds'])))
        else:
            durable_release.resume_journal(args.journal, backend)
            print('事务已完成：' + ', '.join(sorted(durable['feeds'])))
        if args.postflight and not args.rollback:
            if subprocess.run(['python3', 'scripts/check_feeds.py', '--remote'],
                              cwd=ROOT).returncode:
                sys.exit('发布后体检未通过；事务已完成，请人工核对线上清单。')
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        sys.exit('事务操作失败：' + str(error))


if __name__ == '__main__':
    main()
