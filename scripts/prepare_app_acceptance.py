#!/usr/bin/env python3
"""Prepare a fully isolated real-App acceptance environment.

Nothing here touches production: everything lives under one generated
directory with its own HOME, its own loopback Registry, its own isolated
publishing backend and a throwaway signing key. The real App is launched with
that HOME, so its plugin installation state, credentials, journals and tool
versions cannot reach the developer's real ~/Library data.

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
import shutil
import subprocess
import sys
import time
import zipfile
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

    out = args.output.resolve()
    if out.exists():
        shutil.rmtree(out)
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
    (repo / 'plugins' / 'registry.source.json').write_text(
        json.dumps({'format': 2, 'plugins': {}}, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8')

    # --- isolated trust chain -------------------------------------------
    seed = hashlib.sha256(b'ordina real-app acceptance / 2026-11-25').digest()
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
        f'APP="${{1:-{args.app}}}"',
        'if [[ ! -d "$APP" ]]; then',
        '  echo "缺少 Debug App：先运行 bash scripts/local_xcode_build.sh --debug" >&2',
        '  echo "隔离验收不能用 Release 构建（lab 开关被编译剔除，会回落到生产资源）" >&2',
        '  exit 1',
        'fi',
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

所有数据都在本目录的 `home/` 里，App 用这个 HOME 启动，**不会碰你真实的插件、凭据或钥匙串**。Registry、发布后端、签名密钥全部是一次性隔离环境，线上资源只读、不写入。

## 准备（终端，4 条命令）

```bash
bash scripts/local_xcode_build.sh --debug            # 完整 Xcode Debug 构建
python3 scripts/prepare_app_acceptance.py            # 生成 dist/app-acceptance
bash dist/app-acceptance/start-registry.sh &         # 隔离 Registry，保持运行
bash dist/app-acceptance/launch.sh                   # 启动真实 Debug App
```

**必须用 `--debug`，不要用 `--release`。** 隔离 Registry、隔离公钥与 lab 后端全部依赖
`#if DEBUG || ORDINA_LAB` 编译条件；Release 构建会把这些分支编译剔除，App 会静默回落到
正式 Pages / Registry / 生产公钥，隔离验收就不再是隔离的。
`launch.sh` 默认指向 `.build/Build/Products/Debug/ordina.app`，也可把 App 路径作为第一个参数传入。

（`local_xcode_build.sh` 必须在 macOS 原生「终端」App 里跑；agent 会话的 `sandbox-exec` 限制无法完成 Xcode 构建。
正式 1.10.0 出包另用 `bash scripts/local_xcode_build.sh --all`，与本次隔离验收分开。）

## App 内只做这些操作

1. 设置页 → 连点版本号 **10 次** → 「测试选项」→ 打开**开发者中心**并启用。
2. 「高级」→ 选择发布仓库 → 选 `{repo}`（只需一次）。
3. 「工具」→ 检查更新 → 安装 **1.0.0** → 更新到 **1.1.0** → 回滚到 **1.0.0**（记录/事务应保留）。
4. 「发布」→ 拖入 `packages/existing-plugin-update.zip` → 应识别为**更新 1.0.0→1.1.0** → 本地测试 → 填更新说明 → 预览 → 发布 → **发布完成后应自动回到「拖入插件 ZIP」页**，底部保留「已发布 …」提示。
5. 拖入 `packages/brand-new-plugin.zip` → 应识别为**新增** → 本地测试 → 预览 → 正式发布 → 回到插件页刷新，**新插件应出现且可安装**；再发一版更新并做插件回滚。
6. 事务进行中退出 App 再启动 → 「管理」→ 找到未完成事务 → 继续 → 完成；再做一次发布回滚。
7. 依次拖入 `packages/malicious.zip`、`packages/corrupt.zip`、`packages/zip-bomb.zip` → **全部被拒绝**，且没有任何文件被写出。
8. 插件页刷新：普通用户视角应能看到已有插件的更新与新插件。

## 清理

```bash
pkill -f app_acceptance_server.py
# dist/app-acceptance 可以直接删除；真实 ~/Library 未被改动
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
    print('  下一步：bash dist/app-acceptance/start-registry.sh & 然后 bash dist/app-acceptance/launch.sh')


if __name__ == '__main__':
    main()
