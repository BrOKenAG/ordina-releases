#!/usr/bin/env python3
"""Install the Minutes Roughcut bundle from a verified source directory.

The caller (ordina) supplies MINUTES_INSTALL_SOURCE. It may be the app's
bundled copy or an extracted, signature-verified plugin bundle. The version
marker lets the app report updates without shipping a new app build.
"""
import json, os, shutil, tempfile, time
from pathlib import Path

source = Path(os.environ['MINUTES_INSTALL_SOURCE']).resolve()
home = Path(os.environ.get('HOME', str(Path.home())))
support = home / 'Library/Application Support/Minutes Roughcut'
script = home / 'Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/Minutes Roughcut.py'
manifest = home / 'Library/Application Support/Google/Chrome/NativeMessagingHosts/com.minutesroughcut.bridge.json'
backup = support / 'backups' / (time.strftime('%Y%m%d-%H%M%S') + '-' + next(tempfile._get_candidate_names()))
ext_id = 'kfoaocnjgbklmpekohmmloidfaeiggld'

REQUIRED = ['Minutes Roughcut.py', 'host.py', 'host.command', 'plugin.json',
            'extension/manifest.json']


def check(path):
    for part in (path, *path.parents):
        if part == home.parent:
            break
        if part.is_symlink():
            raise RuntimeError('安装路径包含符号链接：' + str(part))


def save(path, name):
    check(path)
    if path.exists():
        backup.mkdir(parents=True, exist_ok=True)
        if path.is_dir():
            shutil.copytree(path, backup / name)
        else:
            shutil.copy2(path, backup / name)


def put(src, dest):
    check(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=dest.parent, prefix='.minutes-')
    try:
        with os.fdopen(fd, 'wb') as out, open(src, 'rb') as inp:
            shutil.copyfileobj(inp, out)
        os.replace(temp, dest)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_json(payload, dest):
    check(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=dest.parent, prefix='.minutes-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(temp, dest)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


for item in REQUIRED:
    if not (source / item).is_file():
        raise RuntimeError('安装文件缺失：' + item)
try:
    meta = json.loads((source / 'plugin.json').read_text(encoding='utf-8'))
    version = meta['version']
    if not isinstance(version, str) or not version:
        raise ValueError('version')
except Exception as exc:
    raise RuntimeError('插件版本信息无效：' + str(exc))

for path, name in [(script, 'Resolve脚本.py'), (support / 'host.py', 'host.py'),
                   (support / 'host.command', 'host.command'),
                   (support / 'extension', 'extension'),
                   (manifest, 'Chrome通信配置.json')]:
    save(path, name)
put(source / 'Minutes Roughcut.py', script)
put(source / 'host.py', support / 'host.py')
put(source / 'host.command', support / 'host.command')
os.chmod(support / 'host.command', 0o700)
extension = support / 'extension'
check(extension)
staged = Path(tempfile.mkdtemp(prefix='.extension-', dir=support))
try:
    shutil.copytree(source / 'extension', staged / 'extension')
    if extension.exists():
        shutil.rmtree(extension)
    os.replace(staged / 'extension', extension)
finally:
    shutil.rmtree(staged, ignore_errors=True)
manifest.parent.mkdir(parents=True, exist_ok=True)
payload = {'name': 'com.minutesroughcut.bridge', 'description': 'Minutes Roughcut',
           'path': str(support / 'host.command'), 'type': 'stdio',
           'allowed_origins': ['chrome-extension://' + ext_id + '/']}
write_json(payload, manifest)
write_json({'id': 'minutes', 'version': version, 'plugin': 'Minutes Roughcut'},
           support / 'installed.json')
print('已安装 Resolve 脚本、本地通信和浏览器扩展文件，版本 ' + version + '。')
print('浏览器扩展文件夹：' + str(extension))
if backup.exists():
    print('旧文件备份：' + str(backup))
