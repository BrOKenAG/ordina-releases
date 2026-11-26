#!/usr/bin/env python3
"""Prepare a fully isolated real-App acceptance environment.

The generated environment uses an isolated HOME, loopback Registry, lab
backend, credential file and throwaway signing key. HOME alone does not isolate
the macOS Keychain; only the explicitly configured lab credential and signer
paths avoid the normal Keychain services.

    python3 scripts/prepare_app_acceptance.py
    bash dist/app-acceptance/start-registry.sh &   # keep it running
    bash dist/app-acceptance/launch.sh             # real App, isolated HOME

The script also publishes one "existing" plugin through the real six-stage
transaction so the App starts with a plugin it can update, and generates every
test ZIP plus two independently versioned developer tools.
"""
import argparse
import base64
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
import zipfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_dev_tool                                        # noqa: E402
import lab_signing                                           # noqa: E402
from app_acceptance_server import envelope                   # noqa: E402
from verify_developer_center import (credential_document, write_plugin,  # noqa: E402
                                     zip_plugin)

CLIENT_VERSION = '1.10.0'
PORT = 18765
EXISTING_ID = 'minutes-lab'          # already published inside this lab
NEW_ID = 'acceptance-lab-notes'      # never seen by the client's source
MAIN = 'BrOKenAG/ordina-releases'
TOOL_VERSIONS = ('1.0.0', '1.1.0')
# The isolated acceptance only works with a Debug build: every lab switch
# (DevBridge.isLab, PluginEnvironment.current, DevBridge.toolManifestPublicKey)
# is behind `#if DEBUG || ORDINA_LAB`. A Release build compiles those branches
# out and would silently fall back to the production Pages/Registry/key, which
# is exactly what this environment must never touch.
DEBUG_APP = ROOT / '.build' / 'Build' / 'Products' / 'Debug' / 'ordina.app'


def is_debug_app(path):
    return Path(path).resolve() == DEBUG_APP.resolve()


def tool_env(root, home, repo, labroot, sign_key, credential):
    return {
        'HOME': str(home),
        'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
        'NO_PROXY': 'localhost,127.0.0.1',
        'PYTHONUNBUFFERED': '1',
        'ORDINA_CLIENT_VERSION': CLIENT_VERSION,
        'ORDINA_DEV_LAB': '1',
        'ORDINA_DEV_BACKEND': 'lab',
        'ORDINA_DEV_LAB_ROOT': str(labroot),
        'ORDINA_DEV_REPO': str(repo),
        'ORDINA_DEV_SIGN_KEY': str(sign_key),
        'ORDINA_DEV_CREDENTIALS': str(credential),
        'ORDINA_DEV_LAB_WRITE_SOURCE': '1',
        'ORDINA_DEV_JOURNAL_DIR': str(
            home / 'Library/Application Support/ordina/Developer/journals'),
    }


def run_tool(archive, arguments, environment, timeout=180):
    result = subprocess.run(['/usr/bin/python3', str(archive), *arguments],
                            env=environment, capture_output=True, text=True,
                            timeout=timeout)
    lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
    try:
        payload = json.loads(lines[-1]) if lines else {}
    except ValueError:
        payload = {'raw': result.stdout[-500:]}
    if result.returncode != 0 or not payload.get('ok'):
        raise SystemExit(f'工具命令失败：{arguments}\n{payload}\n{result.stderr[-400:]}')
    return payload['data']


def shell(path, text):
    path.write_text(text, encoding='utf-8')
    path.chmod(0o755)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / 'app-acceptance')
    parser.add_argument('--port', type=int, default=PORT)
    parser.add_argument('--app', type=Path, default=DEBUG_APP,
                        help='隔离验收必须指向 Debug 产物（lab 开关只在 DEBUG 构建生效）')
    args = parser.parse_args()

    if not is_debug_app(args.app) or not args.app.is_dir():
        sys.exit('请先构建当前工作区的 Debug App；隔离验收不接受其他 App。')
    out = args.output.resolve()
    if out.exists():
        sys.exit('验收目录已存在，请选择新的 --output，避免覆盖已有数据。')
    home = out / 'home'
    repo = out / 'repo'
    labroot = out / 'labroot'
    packages = out / 'packages'
    tools = out / 'tools'
    registry = out / 'registry'
    credentials = out / 'credentials'
    for directory in (home, repo / 'plugins', labroot, packages, tools,
                      registry, credentials):
        directory.mkdir(parents=True, exist_ok=True)
    # HOME does not isolate macOS preferences. Use a dedicated bundle identity
    # for the Debug copy and disable normal startup services in this lab.
    isolated_app = out / 'ordina-acceptance.app'
    shutil.copytree(args.app, isolated_app, symlinks=True)
    info_path = isolated_app / 'Contents/Info.plist'
    with info_path.open('rb') as stream:
        info = plistlib.load(stream)
    preference_domain = 'com.ordina.acceptance.' + uuid.uuid4().hex
    info['CFBundleIdentifier'] = preference_domain
    with info_path.open('wb') as stream:
        plistlib.dump(info, stream)
    subprocess.run(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', str(isolated_app)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (repo / 'plugins' / 'registry.source.json').write_text(
        json.dumps({'format': 2, 'plugins': {}}, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8')

    # Lab Keychain password is independent of signing keys and never used in production.
    keychain_password = home / 'ordina-totp-keychain-password'
    keychain_password.write_bytes(os.urandom(32))
    keychain_password.chmod(0o600)

    # --- isolated trust chain -------------------------------------------
    seed = os.urandom(32)
    sign_key = credentials / 'lab-signing.key'
    sign_key.write_bytes(base64.b64encode(seed) + b'\n')
    sign_key.chmod(0o600)
    public_key = base64.b64encode(lab_signing.public_key(seed)).decode('ascii')
    credential = credentials / 'credential.json'
    raw = envelope(credential_document([
        'plugin.publish', 'plugin.localTest', 'plugin.rollback',
        'repository.create', 'tool.install']), seed).decode().strip()
    credential.write_text(raw, encoding='utf-8')
    credential.chmod(0o600)

    # --- two independently versioned, lab-capable publishing tools ------
    tool_bytes = {}
    for version in TOOL_VERSIONS:
        data = build_dev_tool.build(version, lab=True)
        tool_bytes[version] = data
        (tools / f'ordina-publisher-{version}.pyz').write_bytes(data)
    require_different = tool_bytes['1.0.0'] != tool_bytes['1.1.0']
    assert require_different, '两个工具版本必须有不同的内容'

    tool_entries = []
    for version in TOOL_VERSIONS:
        data = tool_bytes[version]
        asset = f'ordina-publisher-{version}.pyz'
        tool_entries.append({
            'version': version, 'protocol': 1, 'minClientVersion': CLIENT_VERSION,
            'asset': asset, 'sha256': hashlib.sha256(data).hexdigest(),
            'size': len(data), 'tag': f'dev-tool-v{version}',
            'url': (f'http://127.0.0.1:{args.port}/{MAIN}/releases/download/'
                    f'dev-tool-v{version}/{asset}'),
            'notes': f'{version} 隔离验收版本（不发布）',
        })
    tool_manifest = envelope({
        'format': 1, 'tool': 'ordina-publisher', 'channel': 'stable',
        'versions': tool_entries,
    }, seed)
    (registry / 'tool-manifest.json').write_bytes(tool_manifest)
    initial_registry = envelope({'format': 2, 'plugins': {}}, seed)
    (registry / 'registry.initial.json').write_bytes(initial_registry)

    routes = {
        '/dev-tool/manifest.json': {'file': 'registry/tool-manifest.json'},
        '/registry.json.initial': {'file': 'registry/registry.initial.json'},
    }
    for item in tool_entries:
        routes[item['url'].split(f'127.0.0.1:{args.port}', 1)[1]] = {
            'file': 'tools/' + item['asset']}
    (registry / 'routes.json').write_text(
        json.dumps(routes, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    # --- publish one EXISTING plugin through the real transaction -------
    env = tool_env(out, home, repo, labroot, sign_key, credential)
    slug = f'BrOKenAG/ordina-plugin-{EXISTING_ID}'
    (labroot / 'lab' / 'assets' / slug).mkdir(parents=True, exist_ok=True)
    first_source = out / 'src-existing' / EXISTING_ID
    write_plugin(first_source, EXISTING_ID, '1.0.0', 'initial')
    first_zip = zip_plugin(first_source, out / 'bootstrap.zip')
    preview = run_tool(tools / 'ordina-publisher-1.0.0.pyz',
                       ['preview', '--zip', str(first_zip), '--notes', '隔离初始插件'],
                       env)
    run_tool(tools / 'ordina-publisher-1.0.0.pyz',
             ['publish', '--zip', str(first_zip), '--notes', '隔离初始插件',
              '--expect-fingerprint', preview['fingerprint'], '--execute'], env)
    source_after = json.loads(
        (repo / 'plugins' / 'registry.source.json').read_text(encoding='utf-8'))
    assert EXISTING_ID in source_after['plugins'], '初始插件未写入隔离仓库'

    # --- test ZIP materials ---------------------------------------------
    update_source = packages / 'src-update' / EXISTING_ID
    write_plugin(update_source, EXISTING_ID, '1.1.0', 'update')
    zip_plugin(update_source, packages / 'existing-plugin-update.zip')

    new_source = packages / 'src-new' / NEW_ID
    write_plugin(new_source, NEW_ID, '1.0.0', 'brand new')
    zip_plugin(new_source, packages / 'brand-new-plugin.zip')

    with zipfile.ZipFile(packages / 'malicious.zip', 'w') as archive:
        archive.writestr('plugin.json', '{}')
        archive.writestr('../evil.py', 'print(1)')
    (packages / 'corrupt.zip').write_bytes(b'PK\x03\x04' + b'\x00' * 40)
    with zipfile.ZipFile(packages / 'zip-bomb.zip', 'w',
                         zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('plugin.json', '{}')
        archive.writestr('zeros.py', 'A' * 900_000)

    # --- launch scripts --------------------------------------------------
    lines = [
        '#!/bin/bash',
        '# 隔离验收环境：由 prepare_app_acceptance.py 生成，勿手工编辑',
        f'export HOME="{home}"',
        'export ORDO_UI_TEST=1',
        'export NO_PROXY="localhost,127.0.0.1"',
        f'export ORDINA_CLIENT_VERSION="{CLIENT_VERSION}"',
        'export ORDINA_PLUGIN_LAB=1',
        f'export ORDINA_PLUGIN_REGISTRY="http://127.0.0.1:{args.port}/registry.json"',
        f'export ORDINA_PLUGIN_PUBLIC_KEY="{public_key}"',
        'export ORDINA_DEV_LAB=1',
        f'export ORDINA_DEV_MANIFEST="http://127.0.0.1:{args.port}/dev-tool/manifest.json"',
        f'export ORDINA_DEV_MANIFEST_PUBLIC_KEY="{public_key}"',
        'export ORDINA_DEV_BACKEND=lab',
        f'export ORDINA_DEV_LAB_ROOT="{labroot}"',
        f'export ORDINA_DEV_SIGN_KEY="{sign_key}"',
        f'export ORDINA_DEV_CREDENTIALS="{credential}"',
        f'export ORDINA_DEV_REPO="{repo}"',
        'export ORDINA_DEV_LAB_WRITE_SOURCE=1',
        f'export ORDINA_DEV_JOURNAL_DIR="{home}/Library/Application Support/ordina/Developer/journals"',
        'export ORDINA_DEV_PYTHON=/usr/bin/python3',
    ]
    shell(out / 'env.sh', '\n'.join(lines) + '\n')

    shell(out / 'start-registry.sh', '\n'.join([
        '#!/bin/bash',
        f'exec /usr/bin/python3 "{ROOT / "scripts" / "app_acceptance_server.py"}" '
        f'--root "{out}" --port {args.port}',
    ]) + '\n')

    shell(out / 'launch.sh', '\n'.join([
        '#!/bin/bash',
        'set -e',
        'source "$(dirname "$0")/env.sh"',
        f'APP="{isolated_app}"',
        'if [[ ! -d "$APP" ]]; then',
        '  echo "缺少 Debug App：先运行 bash scripts/local_xcode_build.sh --debug" >&2',
        '  echo "隔离验收不能用 Release 构建（lab 开关被编译剔除，会回落到生产资源）" >&2',
        '  exit 1',
        'fi',
        f'/usr/bin/python3 "{ROOT / "scripts" / "verify_app_acceptance.py"}" --launch-check --root "{out}" --report "{out / "launch-check.json"}"',
        'echo "隔离 HOME：$HOME"',
        'echo "验收 App：$APP（Debug 构建，lab 开关生效）"',
        f'echo "隔离 Registry：http://127.0.0.1:{args.port}/registry.json"',
        'echo "开发者中心 → 高级 → 选择发布仓库：$ORDINA_DEV_REPO"',
        'exec "$APP/Contents/MacOS/ordina"',
    ]) + '\n')

    shell(out / 'verify.sh', '\n'.join([
        '#!/bin/bash',
        'set -e',
        f'cd "{ROOT}"',
        'export NO_PROXY="localhost,127.0.0.1,::1"',
        'python3 -B scripts/verify.py',
        'python3 -B scripts/verify_standalone_tool.py',
        f'python3 -B scripts/check_dev_tool_versions.py',
        f'python3 -B scripts/verify_app_acceptance.py --root "{out}"',
    ]) + '\n')

    readme = f'''# 真实 App 隔离验收（简版）

插件数据和事务使用本目录的隔离 HOME；Registry、发布后端、签名密钥和授权文件使用独立的 lab 配置。HOME 本身不会隔离 macOS 钥匙串；仅在上述 lab 配置完整且使用 Debug App 时进行验收，缺少配置应停止操作。

## 启动（终端）

```bash
bash "{out}/start-registry.sh" &
registry_pid=$!
bash "{out}/launch.sh"
kill "$registry_pid"
```

**必须用 `--debug`，不要用 `--release`。** 隔离 Registry、隔离公钥与 lab 后端全部依赖
`#if DEBUG || ORDINA_LAB` 编译条件；Release 构建会把这些分支编译剔除，App 会静默回落到
正式 Pages / Registry / 生产公钥，隔离验收就不再是隔离的。
`launch.sh` 使用从当前 Debug 构建复制的独立验收 App，不能传入其他 App。
验收副本使用独立偏好设置域 `{preference_domain}` 和本目录内独立测试钥匙串（服务 `{preference_domain}.developer-totp`）；HOME 本身不隔离钥匙串。
TOTP 仅保护本机交互，实际发布仍由发布引擎独立验证权限和签名。

（`local_xcode_build.sh` 必须在 macOS 原生「终端」App 里跑；agent 会话的 `sandbox-exec` 限制无法完成 Xcode 构建。
正式 1.10.0 出包另用 `bash scripts/local_xcode_build.sh --all`，与本次隔离验收分开。）

## App 内只做这些操作

1. 首页插件入口 → 安装 Demo Lab Notes，关闭插件页。
2. 设置 → 连点版本号 **10 次** →「测试选项」→ 开发者中心并启用。
3. 「配置发布工具」→ 选择发布仓库 `{repo}` → 检查更新并安装 1.1.0 → 返回验证。
4. 「绑定验证器」→ 手机扫码 → 输入六位验证码；解锁后可使用开发者中心。
5. 点击「锁定」后，等待新的验证码，再输入解锁；「重新绑定」需要再次验证隔离发布凭据。
6. 「撤销认证」清除本机绑定；不会删除发布事务。
7. 「管理」→ Demo Lab Notes →「下架」→ 确认；应显示“已下架”。
8. 关闭开发者中心，首页插件入口刷新：已安装插件显示“已下架”，仍可卸载；卸载后刷新，不再显示该插件。
9. 返回开发者中心 →「管理」→「重新上架」→ 确认；首页插件入口刷新后可再次安装。
10. 「管理」中的事务记录可用于恢复中断的操作或回滚已完成操作。

## 测试中断恢复

保持隔离服务运行，退出 App（⌘Q），在同一终端执行：

```bash
ORDINA_DEV_LAB_FAIL_AFTER=1 bash "{out}/launch.sh"
```

在「管理」执行一次下架或重新上架，预期出现失败提示及未完成事务。退出 App，再不带变量运行 `bash "{out}/launch.sh"`，在「管理」中选择“继续”或“回滚”；完成后插件状态应与所选操作一致。

普通下架不撤回旧客户端的内置插件、离线缓存或历史下载链接。

## 清理

```bash
defaults delete {preference_domain}
 # 测试钥匙串随验收目录一并清理
# 确认不再需要验收数据后，可删除 dist/app-acceptance
```

生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}
工具版本：1.0.0 / 1.1.0（均为隔离 lab 构建，**不发布**）
隔离端口：{args.port}
'''
    (out / 'README.md').write_text(readme, encoding='utf-8')

    summary = {
        'format': 1, 'generatedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'root': str(out), 'port': args.port, 'clientVersion': CLIENT_VERSION,
        'existingPlugin': EXISTING_ID, 'newPlugin': NEW_ID,
        'toolVersions': list(TOOL_VERSIONS), 'productionResourcesTouched': False,
        'isolatedHome': str(home),
        'isolatedApp': str(isolated_app), 'preferencesDomain': preference_domain,
        # A Release build compiles the lab switches out; only Debug keeps the
        # isolated Registry / public key / backend branches reachable.
        'requiredBuildMode': 'Debug', 'launchApp': str(args.app),
        'buildCommand': 'bash scripts/local_xcode_build.sh --debug',
        'packages': sorted(path.name for path in packages.glob('*.zip')),
    }
    (out / 'environment.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    print(f'隔离验收环境已生成：{out}')
    print(f'  隔离 HOME：{home}')
    print(f'  工具：{", ".join(TOOL_VERSIONS)}（lab 构建，公钥 {public_key[:16]}…）')
    print(f'  已有插件：{EXISTING_ID} 1.0.0（真实六阶段事务发布）')
    print(f'  测试 ZIP：{", ".join(summary["packages"])}')
    print(f'  验收步骤：{out / "README.md"}')


if __name__ == '__main__':
    main()
