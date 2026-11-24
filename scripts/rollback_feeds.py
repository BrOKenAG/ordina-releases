#!/usr/bin/env python3
"""Roll signed feeds back to a known-good commit.

This restores the feeds so they reference an asset that is known to exist. It
NEVER deletes a Release: deleting the asset a previous feed pointed at would
create a dead link for any client that cached that feed. Roll back the feed,
then clean up old Releases only after no client can still reference them.

    python3 scripts/rollback_feeds.py --to HEAD~1 --dry-run
    python3 scripts/rollback_feeds.py --to HEAD~1 --push
"""
import argparse
import subprocess
import sys
from pathlib import Path

from release_transaction import ROOT

FILES = ('appcast.xml', 'plugins/manifest.json', 'registry.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--to', default='HEAD~1', help='已知良好的 git 引用')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--push', action='store_true', help='提交并推送回滚')
    args = parser.parse_args()

    if args.push:
        sys.exit("跨仓库回滚推送暂不可用；请先审查线上状态并完成恢复流程验证。")

    restored = []
    for relative in FILES:
        result = subprocess.run(['git', 'show', f'{args.to}:{relative}'],
                                cwd=ROOT, capture_output=True)
        if result.returncode != 0:
            print(f'SKIP  {relative}: {args.to} 中不存在（新增文件，保持现状）')
            continue
        path = ROOT / relative
        current = path.read_bytes() if path.is_file() else b''
        if current == result.stdout:
            print(f'OK    {relative}: 已与目标一致')
            continue
        print(f'RESTORE {relative}: {len(result.stdout):,} 字节')
        if not args.dry_run:
            path.write_bytes(result.stdout)
        restored.append(relative)

    if args.dry_run:
        print('\n预演完成，未修改文件。')
        return
    if not restored:
        print('\n无需回滚。')
        return
    if not args.push:
        print('\n已恢复到工作区，未提交。确认后加 --push 推送。')
        return
    subprocess.run(['git', 'add', '--', *restored], cwd=ROOT, check=True)
    subprocess.run(['git', 'commit', '-m',
                    f'Roll back signed feeds to {args.to}'], cwd=ROOT, check=True)
    subprocess.run(['git', 'push', 'origin', 'main'], cwd=ROOT, check=True)
    print('\n回滚已推送。注意：')
    print('  - 不要删除任何 Release；等旧清单不再被任何客户端引用后再清理。')
    print('  - 各插件仓库的 manifest.json 需单独用 gh api PUT 恢复，或用')
    print('    build_feeds.py --push 从 registry 源重新生成。')
    print('  - 运行 python3 scripts/check_feeds.py --remote 复核。')


if __name__ == '__main__':
    main()
