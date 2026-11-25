#!/usr/bin/env python3
"""Prepare and rehearse the independent developer-tool release channel.

This is the single entry point for the tool channel. It does NOT publish
anything by default: the default run builds the archive in memory, derives the
signed-manifest document, rehearses the SAME six-stage durable transaction used
by stage A, and prints the commands a release operator would run after
authorisation. No network call, no git push and no write to any online feed.

    python3 scripts/publish_dev_tool.py                  # rehearse (default)
    python3 scripts/publish_dev_tool.py --commands       # command list only
    python3 scripts/publish_dev_tool.py --prepare        # local artifacts + signed manifest
    python3 scripts/publish_dev_tool.py --rollback-plan  # rollback commands

The only path that can touch a remote is ``--publish-manifest --execute``; it
goes through ``durable_release.execute`` (persistent journal, conditional
writes, asset gate, online re-read), requires a valid isolated-lab report and
an already-uploaded, verified Release asset. There is deliberately no direct
``git push``, no ``gh api`` feed write and no curl to Pages.

Release upload is a separate, explicit operator command printed below; the
transaction's GATE stage refuses to proceed until that asset is downloadable.
"""
import argparse
import base64
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_dev_tool                                        # noqa: E402
import durable_release                                       # noqa: E402
from feed_signing import canonical                           # noqa: E402
from release_transaction import (Asset, DryRunGateway, FeedTarget,  # noqa: E402
                                 LocalGateway, Plan, ROOT as TX_ROOT,
                                 ReleaseError)

MAIN = 'BrOKenAG/ordina-releases'
MANIFEST_PATH = ROOT / 'dev-tool' / 'manifest.json'
SOURCE_PATH = ROOT / 'dev-tool' / 'manifest.source.json'
DIST = ROOT / 'dist' / 'dev-tool'


def tool_document(version, data, notes):
    entry = build_dev_tool.entry(version, data, notes)
    versions = [item for item in json.loads(SOURCE_PATH.read_text())['versions']
                if item['version'] != version]
    versions.append(entry)
    return build_dev_tool.document(versions)


def asset_url(version):
    return (f'https://github.com/{MAIN}/releases/download/'
            f'dev-tool-v{version}/ordina-publisher-{version}.pyz')


def build_plan(version, data, manifest_bytes, notes):
    return Plan(
        label=f'ordina publisher {version}',
        repository=MAIN,
        tag=f'dev-tool-v{version}',
        title=f'ordina publisher {version}',
        notes=notes,
        asset=Asset(name=f'ordina-publisher-{version}.pyz', data=data,
                    url=asset_url(version)),
        feeds=[FeedTarget('dev-tool-manifest', manifest_bytes, path=MANIFEST_PATH)],
        commit_paths=['dev-tool/manifest.json'],
        commit_message=f'Publish ordina publisher {version}',
    )


def rehearsed_stages(plan, data, timeout):
    """Run the real stage-A transaction with a zero-side-effect backend."""
    assert TX_ROOT == ROOT, 'release_transaction.ROOT 与仓库根不一致'
    with tempfile.TemporaryDirectory(prefix='ordina-tool-plan-') as temporary:
        tmp = Path(temporary)
        staged = durable_release.prepare(plan, f'dev-tool-v{plan.tag.split("-v")[-1]}',
                                         journal_dir=tmp / 'journals')
        backend = durable_release.DryRunBackend(staged.plan['feeds'])
        gateway = DryRunGateway(data)
        result = durable_release.execute(plan, gateway, backend, staged, tmp,
                                         timeout=timeout)
        return result['stages'], staged.plan['feeds']


def commands(version, notes, rollback_to=None):
    asset = f'ordina-publisher-{version}.pyz'
    tag = f'dev-tool-v{version}'
    return [
        '# 1) 本地构建并签名工具清单（不上传、不发布）',
        'python3 scripts/build_dev_tool.py --version ' + version + ' --sign',
        'python3 scripts/build_dev_tool.py --check',
        'python3 scripts/check_dev_tool_versions.py',
        '',
        '# 2) 上传 Release 资产（发版人手动执行；GATE 会先下载校验）',
        f'gh release create {tag} dist/dev-tool/{asset} \\',
        f'  --repo {MAIN} --title "ordina publisher {version}" '
        f'--notes "{notes or "首个独立发布工具"}"',
        '',
        '# 3) 通过阶段 A 持久事务发布签名清单（禁止直接覆盖 Pages / 裸 push）',
        'python3 scripts/publish_dev_tool.py --publish-manifest --execute \\',
        '  --lab-report docs/validation/lab-release.json',
        '',
        '# 4) 上线后只读体检',
        'python3 scripts/check_feeds.py --remote',
        'python3 scripts/verify_standalone_tool.py',
        '',
        '# 回滚：清单回退 + 客户端本地工具回滚；Release 资产不删除',
        ('python3 scripts/publish_dev_tool.py --rollback-to ' +
         (rollback_to or '<上一个已知良好版本>') + ' --prepare'),
        'python3 scripts/publish_dev_tool.py --publish-manifest --execute \\',
        '  --lab-report docs/validation/lab-release.json',
        '# 客户端：开发者中心 → 工具 → 回滚到 ' + (rollback_to or '<上一个版本>'),
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--version', default=build_dev_tool.read_version())
    parser.add_argument('--notes', default='')
    parser.add_argument('--timeout', type=int, default=300)
    parser.add_argument('--commands', action='store_true', help='只输出命令清单')
    parser.add_argument('--rollback-plan', action='store_true', help='只输出回滚方案')
    parser.add_argument('--rollback-to', help='生成回退到该版本的清单（配合 --prepare）')
    parser.add_argument('--prepare', action='store_true',
                        help='写本地产物与签名清单；不上传、不发布')
    parser.add_argument('--publish-manifest', action='store_true',
                        help='发布清单；必须同时给 --execute')
    parser.add_argument('--execute', action='store_true',
                        help='显式确认执行阶段 A 持久事务')
    parser.add_argument('--journal-dir', type=Path,
                        default=durable_release.TRANSACTIONS)
    parser.add_argument('--lab-report', type=Path, default=durable_release.LAB_REPORT)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    if args.execute and not args.publish_manifest:
        sys.exit('--execute 只允许与 --publish-manifest 一起使用。')
    if args.publish_manifest and not args.execute:
        sys.exit('清单发布需显式 --execute；预演请不带该参数。')
    if args.rollback_to and not args.prepare:
        sys.exit('--rollback-to 需要 --prepare（生成回退后的本地清单）。')

    version = args.version
    notes = args.notes
    plan_data = None

    if args.commands or args.rollback_plan:
        lines = commands(version, notes, args.rollback_to)
        if args.rollback_plan:
            lines = [line for line in lines if line.startswith('# 回滚')
                     or line.startswith(('python3 scripts/publish_dev_tool.py '
                                         '--rollback-to',
                                         'python3 scripts/publish_dev_tool.py '
                                         '--publish-manifest',
                                         '# 客户端'))]
        print('\n'.join(lines))
        return

    data = build_dev_tool.build(version)
    digest = hashlib.sha256(data).hexdigest()

    if args.rollback_to:
        source = json.loads(SOURCE_PATH.read_text(encoding='utf-8'))
        kept = [item for item in source['versions']
                if build_dev_tool._version_key(item['version'])
                <= build_dev_tool._version_key(args.rollback_to)]
        if not kept:
            sys.exit(f'没有可回退到的版本 {args.rollback_to}；首次上线请勿生成空清单。')
        document = build_dev_tool.document(kept)
        from feed_signing import envelope_bytes
        MANIFEST_PATH.write_bytes(envelope_bytes(document))
        SOURCE_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2)
                               + '\n', encoding='utf-8')
        print(f'已生成回退清单：{MANIFEST_PATH.relative_to(ROOT)}（{len(kept)} 个版本）')
        print('下一步仍需走阶段 A 事务发布，且客户端需本地回滚；Release 资产保留。')
        return

    if args.prepare:
        from feed_signing import envelope_bytes
        document = tool_document(version, data, notes)
        DIST.mkdir(parents=True, exist_ok=True)
        (DIST / f'ordina-publisher-{version}.pyz').write_bytes(data)
        MANIFEST_PATH.write_bytes(envelope_bytes(document))
        SOURCE_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2)
                               + '\n', encoding='utf-8')
        print(f'已准备本地产物：{DIST / f"ordina-publisher-{version}.pyz"}')
        print(f'已签名清单：{MANIFEST_PATH.relative_to(ROOT)}（未上传、未发布）')
        return

    # Default: zero-side-effect rehearsal. The manifest document is derived but
    # never signed here, so no release key is touched during a rehearsal.
    document = tool_document(version, data, notes)
    manifest_bytes = (json.dumps(
        {'payload': base64.b64encode(canonical(document)).decode('ascii'),
         'signature': '<REHEARSAL-ONLY>'},
        ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
    plan = build_plan(version, data, manifest_bytes, notes)
    stages, feeds = rehearsed_stages(plan, data, args.timeout)
    require_stages = ['preflight', 'asset', 'gate', 'feeds', 'push', 'postflight']
    if stages != require_stages:
        sys.exit(f'预演阶段顺序与阶段 A 不一致：{stages}')

    if args.publish_manifest and args.execute:
        if not durable_release.lab_report_valid(args.lab_report):
            sys.exit('缺少有效的隔离仓库验证报告（--lab-report），已停止发布。')
        signed = MANIFEST_PATH.read_bytes()
        envelope = json.loads(signed)
        if envelope.get('signature', '').startswith('<REHEARSAL'):
            sys.exit('清单尚未签名，请先 --prepare。')
        payload = json.loads(base64.b64decode(envelope['payload'], validate=True))
        if payload != document:
            sys.exit('签名清单与当前构建不一致，请重新 --prepare。')
        live = build_plan(version, data, signed, notes)
        with tempfile.TemporaryDirectory(prefix='ordina-tool-release-') as temporary:
            tmp = Path(temporary)
            staged = durable_release.prepare(live, f'dev-tool-v{version}',
                                             journal_dir=args.journal_dir)
            backend = durable_release.production_backend(staged, live)
            try:
                result = durable_release.execute(live, LocalGateway(), backend,
                                                 staged, tmp, args.timeout)
            except ReleaseError as error:
                sys.exit(durable_release.describe(error, staged.journal))
        print(f'已发布 {live.label}（{result["asset"]}）')
        print('事务记录：' + result['journal'])
        return

    plan_data = {
        'format': 1, 'mode': 'rehearsal', 'version': version,
        'sha256': digest, 'size': len(data), 'tag': f'dev-tool-v{version}',
        'manifestPath': 'dev-tool/manifest.json',
        'feedTargets': [feed.label for feed in plan.feeds],
        'feeds': {repo: sorted(json.loads(raw)) for repo, raw in feeds.items()},
        'stages': stages,
        'published': False, 'signedDuringRehearsal': False,
        'commands': commands(version, notes),
    }
    if args.json:
        print(json.dumps(plan_data, ensure_ascii=False, indent=2))
    else:
        print(f'预演通过：ordina publisher {version}（{len(data):,} 字节，'
              f'sha256 {digest[:16]}…）')
        print('阶段：' + ' → '.join(stages))
        print('清单目标：' + ', '.join(plan_data['feeds'].get(MAIN, [])))
        print('未发布、未上传、未签名、未写入任何线上资源。')
        print()
        print('\n'.join(commands(version, notes)))


if __name__ == '__main__':
    main()
