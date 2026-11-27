#!/usr/bin/env python3
"""Publish an ordina app release as a verified, resumable transaction.

The DMG is uploaded first; the signed appcast.xml is committed only after the
published DMG has been downloaded and verified (HTTP 200, size, SHA-256 and
the Sparkle Ed25519 signature recorded in the feed).

Real publishing uses the durable cross-repository journal. Preparation writes
the registry and plugin feeds in one transaction; the appcast is a separate,
explicit transaction that requires a real-client validation report. Resume and
rollback are handled by scripts/durable_journal.py.
"""
import argparse
import json
import plistlib
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import durable_release
from build_feeds import generate, load_source
from feed_signing import sign_tool
from sparkle_tools import KEYCHAIN_ACCOUNT
from release_transaction import (ROOT, Asset, DryRunGateway, FeedTarget,
                                 LocalGateway, Plan, ReleaseError)

SPARKLE = '{http://www.andymatuschak.org/xml-namespaces/sparkle}'


def check_client_validation(plan):
    if not plan.validation_report or not plan.validation_report.is_file():
        raise ValueError('缺少真实客户端验证记录；appcast 未发布。')
    report = json.loads(plan.validation_report.read_text(encoding='utf-8'))
    if (report.get('tag') != plan.tag
            or report.get('asset_sha256') != plan.asset.sha256
            or report.get('real_client_verified') is not True):
        raise ValueError('验证记录未确认当前安装包；appcast 未发布。')


def build_plan(args):
    info = plistlib.loads((args.app / 'Contents/Info.plist').read_bytes())
    build = info['CFBundleVersion']
    version = info['CFBundleShortVersionString']
    tag = f'v{version}-build.{build}'
    archive = args.archive.read_bytes()
    feed_bytes = args.feed.read_bytes()
    notes = args.notes_file.read_text(encoding='utf-8')

    items = [item for item in ET.fromstring(feed_bytes).findall('./channel/item')
             if item.findtext(SPARKLE + 'version') == build]
    if len(items) != 1:
        raise ValueError('更新清单必须包含唯一的当前构建号。')
    enclosure = items[0].find('enclosure')
    if enclosure is None:
        raise ValueError('更新清单缺少 enclosure。')
    url = enclosure.get('url', '')
    expected = (f'https://github.com/{args.repository}/releases/download/'
                f'{tag}/{args.archive.name}')
    if url != expected:
        raise ValueError(f'更新清单下载地址不一致：{url}')
    if enclosure.get('length') != str(len(archive)):
        raise ValueError('更新清单记录的大小与 DMG 不符。')
    signature = enclosure.get(SPARKLE + 'edSignature', '')
    if not signature:
        raise ValueError('更新清单缺少 Ed25519 签名。')
    subprocess.run([str(sign_tool()), '--account', KEYCHAIN_ACCOUNT,
                    '--verify', str(args.feed)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # The app release must publish the signed registry in the same
    # transaction: newer clients bootstrap plugin feeds from it.
    source = load_source()
    generated = generate(source)
    registry_bytes = generated['registry'][1]
    prerequisites = [FeedTarget('legacy', generated['legacy'][1], path=ROOT / 'plugins/manifest.json')]
    for name, meta in sorted(source.items()):
        if not meta.get('published'):
            continue
        for label, remote in (('compat-plugin:' + name, 'manifest.json'),
                              ('plugin:' + name, 'manifest-v2.json')):
            if label in generated:
                prerequisites.append(FeedTarget(label, generated[label][1],
                                                repository=meta['repository'],
                                                remote_path=remote))

    plan = Plan(
        label=f'ordina {version}（{build}）',
        repository=args.repository,
        tag=tag,
        title=f'ordina {version}（{build}）',
        notes=notes,
        asset=Asset(args.archive.name, archive, url),
        ed_signature=signature,
        # 软件正式发布是面向全体用户的通道，须自动成为 GitHub Latest；
        # 工具与插件发布保持 Plan.latest=False，不抢占 Latest。
        latest=True,
        publish_appcast=args.publish_appcast,
        validation_report=args.validation_report,
        feeds=[FeedTarget('appcast', feed_bytes, path=ROOT / 'appcast.xml'),
               FeedTarget('registry', registry_bytes, path=ROOT / 'registry.json')] + prerequisites,
        commit_paths=['appcast.xml', 'registry.json'],
        commit_message=f'Publish signed ordina {version} update feed',
    )
    return plan, archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True, help='DMG')
    parser.add_argument('--feed', type=Path, required=True, help='已签名的 appcast.xml')
    parser.add_argument('--notes-file', type=Path, required=True)
    parser.add_argument('--repository', default='BrOKenAG/ordina-releases')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--execute', action='store_true', help='执行真实持久化发布事务')
    parser.add_argument('--timeout', type=int, default=300)
    parser.add_argument('--publish-appcast', action='store_true',
                        help='真实客户端验证后，单独发布 appcast 事务')
    parser.add_argument('--validation-report', type=Path,
                        help='真实客户端验证记录（发布 appcast 时必需）')
    parser.add_argument('--journal-dir', type=Path,
                        default=durable_release.TRANSACTIONS)
    parser.add_argument('--lab-report', type=Path,
                        default=durable_release.LAB_REPORT,
                        help='隔离仓库端到端验证报告')
    args = parser.parse_args()

    try:
        if not args.dry_run and not args.execute:
            raise ValueError('正式发布需显式 --execute；预演请使用 --dry-run。')
        if args.execute and not durable_release.lab_report_valid(args.lab_report):
            raise ValueError('缺少有效的隔离仓库验证报告（--lab-report），已停止发布。')
        plan, archive = build_plan(args)
        if args.publish_appcast and not args.dry_run:
            check_client_validation(plan)

        with tempfile.TemporaryDirectory(prefix='ordina-app-release-') as temporary:
            tmp = Path(temporary)
            scope = f'{plan.tag}-appcast' if args.publish_appcast else f'{plan.tag}-registry'
            journal_dir = tmp / 'journals' if args.dry_run else args.journal_dir
            prepared = durable_release.prepare(
                plan, scope,
                include_appcast=bool(args.publish_appcast),
                only_appcast=bool(args.publish_appcast),
                journal_dir=journal_dir)
            if args.dry_run:
                backend = durable_release.DryRunBackend(prepared.plan['feeds'])
                gateway = DryRunGateway(archive)
            else:
                backend = durable_release.production_backend(prepared, plan)
                gateway = LocalGateway()
            result = durable_release.execute(plan, gateway, backend,
                                             prepared, tmp, args.timeout)
            if args.dry_run:
                print(f'预演通过：{plan.label} → {plan.repository}')
                print('阶段：' + ' → '.join(result['stages']))
                if not args.publish_appcast:
                    print('registry 已就绪；appcast 未发布，等待真实客户端验证。')
                return
            print(f'已发布 {plan.label}（{result["asset"]}）')
            print('事务记录：' + result['journal'])
            if not args.publish_appcast:
                print('registry 已上线；appcast 未发布，等待真实客户端验证。')
    except ReleaseError as error:
        journal = None
        if 'prepared' in locals():
            journal = prepared.journal
        sys.exit(durable_release.describe(error, journal))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f'发布失败：{error}')


if __name__ == '__main__':
    main()
