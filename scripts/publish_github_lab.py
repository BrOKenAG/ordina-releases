#!/usr/bin/env python3
"""Exercise durable publication against the two private lab repositories only.

This is not the ordina production publisher. The test backend rejects every
repository outside the fixed lab prefix and the release asset is deliberately
synthetic. The same arguments and journal are required to resume a failure.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

from durable_transaction import publish, rollback
from github_lab_backend import GitHubLabBackend

MAIN = 'BrOKenAG/ordina-release-lab-20261124-main'
PLUGIN = 'BrOKenAG/ordina-release-lab-20261124-plugin'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asset', type=Path, required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--transaction', required=True)
    parser.add_argument('--journal', type=Path, required=True)
    parser.add_argument('--rollback', action='store_true')
    parser.add_argument('--simulate-lost-response', action='store_true',
                        help='仅用于私有测试：第一个远端提交后模拟断线')
    args = parser.parse_args()
    if not re.fullmatch(r'lab-[A-Za-z0-9._-]{1,60}', args.tag):
        parser.error('测试标签必须以 lab- 开头。')
    if not args.asset.is_file(): parser.error('测试资产不存在。')
    name = args.asset.name
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,100}', name): parser.error('测试资产名称无效。')
    data = args.asset.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    url = f'https://github.com/{PLUGIN}/releases/download/{args.tag}/{quote(name)}'
    entry = {'version': args.tag, 'url': url, 'sha256': digest}
    manifest = json.dumps({'format':1,'plugins':{'lab-test':entry}},sort_keys=True,separators=(',',':'))
    plan = {'id':args.transaction, 'asset':{'size':len(data),'sha256':digest,'source':str(args.asset.resolve())},
            'feeds':{MAIN:manifest,PLUGIN:manifest}}

    class Lab(GitHubLabBackend):
        interrupted = False
        def write(self, *values):
            result = super().write(*values)
            if args.simulate_lost_response and not self.interrupted:
                self.interrupted = True
                raise ConnectionError('模拟：远端已提交但响应丢失')
            return result

    remote = Lab(MAIN,PLUGIN,args.tag,name)
    try:
        if args.rollback: rollback(args.journal,remote)
        else: publish(plan,args.journal,remote)
    except Exception as error:
        sys.exit('测试事务未完成：' + str(error))
    print('私有测试事务已完成；正式仓库未修改。')


if __name__ == '__main__':
    main()
