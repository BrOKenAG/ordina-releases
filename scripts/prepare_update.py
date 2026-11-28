"""Prepare a signed Sparkle feed and DMG for a GitHub release."""
import argparse
import base64
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

SPARKLE = '{http://www.andymatuschak.org/xml-namespaces/sparkle}'


def validate_appcast(feed, build, version, archive_name, download_prefix, size):
    items = [item for item in ET.parse(feed).findall('./channel/item')
             if item.findtext(SPARKLE + 'version') == build]
    if len(items) != 1:
        raise ValueError('更新清单必须包含唯一的当前构建号。')
    item = items[0]
    if item.findtext(SPARKLE + 'shortVersionString') != version:
        raise ValueError('更新清单版本与 App 不一致。')
    if item.findtext(SPARKLE + 'minimumSystemVersion') not in ('26.0', '26.0.0'):
        raise ValueError('更新清单缺少正确的最低系统版本。')
    enclosure = item.find('enclosure')
    if enclosure is None or not download_prefix.startswith('https://'):
        raise ValueError('更新文件需要 HTTPS 下载地址。')
    if enclosure.get('url') != download_prefix + archive_name:
        raise ValueError('更新文件下载地址不一致。')
    signature = enclosure.get(SPARKLE + 'edSignature', '')
    if len(base64.b64decode(signature, validate=True)) != 64:
        raise ValueError('更新文件缺少有效的 Ed25519 签名。')
    if enclosure.get('length') != str(size):
        raise ValueError('更新文件大小不一致。')


def main():
    from sparkle_tools import ensure_tools, KEYCHAIN_ACCOUNT

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repository', default='BrOKenAG/ordina-releases')
    parser.add_argument('--previous-feed', type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repository):
        parser.error('发布仓库名称无效。')
    info = plistlib.loads((args.app / 'Contents/Info.plist').read_bytes())
    build = info['CFBundleVersion']
    version = info['CFBundleShortVersionString']
    if not re.fullmatch(r'[0-9]+', build) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)+', version):
        parser.error('版本号或构建号格式无效。')
    tag = f'v{version}-build.{build}'
    prefix = f'https://github.com/{args.repository}/releases/download/{tag}/'
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f'ordina-{version}-build.{build}.dmg'
    if archive != args.archive.resolve():
        shutil.copy2(args.archive, archive)
    feed = output / 'appcast.xml'
    tools = ensure_tools()
    # Sparkle rewrites links for every archive in its input directory. Isolate
    # this release so earlier entries retain their original versioned URLs.
    with tempfile.TemporaryDirectory(prefix='.prepare-update-', dir=output) as temporary:
        staging = Path(temporary)
        staged_feed = staging / 'appcast.xml'
        shutil.copy2(archive, staging / archive.name)
        previous_feed = args.previous_feed or feed
        if previous_feed.exists():
            shutil.copy2(previous_feed, staged_feed)
        subprocess.run([str(tools / 'generate_appcast'), '--account', KEYCHAIN_ACCOUNT,
                        '--download-url-prefix', prefix, '--maximum-deltas', '0', '--maximum-versions', '0',
                        '--link', f'https://github.com/{args.repository}/releases', str(staging)], check=True)
        validate_appcast(staged_feed, build, version, archive.name, prefix, archive.stat().st_size)
        subprocess.run([str(tools / 'sign_update'), '--account', KEYCHAIN_ACCOUNT,
                        '--verify', str(staged_feed)], check=True)
        current_item = next(item for item in ET.parse(staged_feed).findall('./channel/item')
                            if item.findtext(SPARKLE + 'version') == build)
        signature = current_item.find('enclosure').get(SPARKLE + 'edSignature')
        subprocess.run([str(tools / 'sign_update'), '--account', KEYCHAIN_ACCOUNT,
                        '--verify', str(archive), signature], check=True)
        staged_feed.replace(feed)
    (output / '.nojekyll').touch()
    print(f'已生成签名更新清单与安装包，发布标签 {tag}')


if __name__ == '__main__':
    main()
