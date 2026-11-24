#!/usr/bin/env python3
"""Publish an ordina app release as a verified transaction.

The DMG is uploaded first; the signed appcast.xml is committed and pushed only
after the published DMG has been downloaded and verified (HTTP 200, size,
SHA-256 and the Sparkle Ed25519 signature recorded in the feed).
"""
import argparse
import plistlib
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from build_feeds import generate, load_source
from feed_signing import sign_tool
from sparkle_tools import KEYCHAIN_ACCOUNT
from release_transaction import (ROOT, Asset, DryRunGateway, FeedTarget,
                                 LocalGateway, Plan, ReleaseError,
                                 describe_failure, execute)

SPARKLE = '{http://www.andymatuschak.org/xml-namespaces/sparkle}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True, help='DMG')
    parser.add_argument('--feed', type=Path, required=True, help='已签名的 appcast.xml')
    parser.add_argument('--notes-file', type=Path, required=True)
    parser.add_argument('--repository', default='BrOKenAG/ordina-releases')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--timeout', type=int, default=300)
    parser.add_argument('--publish-appcast', action='store_true', help='真实客户端验证后发布 appcast')
    parser.add_argument('--validation-report', type=Path, help='真实客户端验证记录')
    args = parser.parse_args()

    try:
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
        # transaction: newer clients bootstrap plugin feeds from it, so a
        # client update without registry.json would break plugin checking.
        registry_bytes = generate(load_source())['registry'][1]

        plan = Plan(
            label=f'ordina {version}（{build}）',
            repository=args.repository,
            tag=tag,
            title=f'ordina {version}（{build}）',
            notes=notes,
            asset=Asset(args.archive.name, archive, url),
            ed_signature=signature,
            latest=False,
            publish_appcast=args.publish_appcast,
            validation_report=args.validation_report,
            feeds=[FeedTarget('appcast', feed_bytes, path=ROOT / 'appcast.xml'),
                   FeedTarget('registry', registry_bytes, path=ROOT / 'registry.json')],
            commit_paths=['appcast.xml', 'registry.json'],
            commit_message=f'Publish signed ordina {version} update feed',
        )
        with tempfile.TemporaryDirectory(prefix='ordina-app-release-') as temporary:
            gateway = DryRunGateway(archive) if args.dry_run else LocalGateway()
            result = execute(plan, gateway, Path(temporary), args.timeout)

        if args.dry_run:
            print(f'预演通过：{plan.label} → {plan.repository}')
            print('阶段：' + ' → '.join(result['stages']))
        else:
            print(f'已准备 {plan.label}（{result["asset"]}）')
            if result.get('awaiting_client_validation'):
                print('registry 已上线；appcast 未发布，等待真实客户端验证。')
    except ReleaseError as error:
        sys.exit(describe_failure(error))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f'发布失败：{error}')


if __name__ == '__main__':
    main()
