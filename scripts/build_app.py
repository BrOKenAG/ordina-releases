#!/usr/bin/env python3
"""构建 ordina，并验证资源与签名。"""
from pathlib import Path
import os, subprocess, shutil, plistlib, argparse
from datetime import datetime
root = Path(__file__).resolve().parents[1]
name = 'ordina.app'
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, default=Path.home()/'Desktop'/name)
args = parser.parse_args()
output = args.output.resolve()
env = os.environ.copy()
env.setdefault('DEVELOPER_DIR', '/Applications/Xcode.app/Contents/Developer')
# Standard Xcode build. Run this from a local, non-sandboxed terminal (or from
# Xcode itself): SwiftPM's "Resolve Package Graph" step invokes sandbox-exec,
# which is denied inside agent/IDE sandboxes. No environment-specific compiler
# workarounds belong in the official build path.
try:
    subprocess.run(['xcodebuild','-quiet','-project',str(root/'Ordina.xcodeproj'),'-scheme','Ordina',
        '-configuration','Release','-destination','platform=macOS,arch=arm64','-derivedDataPath',str(root/'.build'),
        'CODE_SIGN_IDENTITY=-','DEVELOPMENT_TEAM=','ARCHS=arm64','ONLY_ACTIVE_ARCH=NO','build'],env=env,check=True)
except subprocess.CalledProcessError:
    raise SystemExit('xcodebuild 失败。若报错包含 sandbox-exec / sandbox_apply: Operation not permitted，'
                     '请在本地非沙盒终端（「终端」App）或 Xcode 内重新运行本脚本；'
                     '这是运行环境的权限限制，不要改用临时编译方案。')
prepared = root/'.build/Build/Products/Release'/name
shutil.copyfile(root/'Integrations/Project Setup.py', prepared/'Contents/Resources/Project Setup.py')
for plugin in ('Import YueLiu.py', 'Multicam Tools.py'):
    shutil.copyfile(root/'Integrations/OptionalPlugins'/plugin, prepared/'Contents/Resources'/plugin)
shutil.copytree(root/'Integrations/OptionalPlugins/Minutes Roughcut', prepared/'Contents/Resources/Minutes Roughcut', dirs_exist_ok=True)
(prepared/'Contents/Resources/导入阅流.py').unlink(missing_ok=True)
sparkle = prepared/'Contents/Frameworks/Sparkle.framework'
assert sparkle.is_dir(), '缺少 Sparkle 更新组件'
# Sign nested helpers first. --deep is used only for verification.
for relative in ('Versions/B/XPCServices/Installer.xpc', 'Versions/B/XPCServices/Downloader.xpc',
                 'Versions/B/Autoupdate', 'Versions/B/Updater.app'):
    helper = sparkle/relative
    subprocess.run(['codesign','--force','--sign','-','--options','runtime',
                    '--preserve-metadata=entitlements',str(helper)],check=True)
subprocess.run(['codesign','--force','--sign','-','--options','runtime',str(sparkle)],check=True)
# Ad-hoc builds have no Apple Team ID. This app-scoped entitlement permits
# loading Sparkle while retaining the other Hardened Runtime protections.
entitlements = plistlib.loads((root/'scripts/app.entitlements').read_bytes())
entitlements['com.apple.security.cs.disable-library-validation'] = True
signing_entitlements = root/'.build/adhoc-entitlements.plist'
signing_entitlements.write_bytes(plistlib.dumps(entitlements))
subprocess.run(['codesign','--force','--sign','-','--options','runtime','--entitlements',str(signing_entitlements),str(prepared)],check=True)
subprocess.run(['codesign','--verify','--deep','--strict',str(prepared)],check=True)
info = plistlib.loads((prepared/'Contents/Info.plist').read_bytes())
assert info['CFBundleIdentifier']=='com.example.MagicTools.Youth'
assert info['CFBundleDisplayName']=='ordina'
assert not (prepared/'Contents/Helpers').exists()
assert {item.name for item in (prepared/'Contents/Frameworks').iterdir()} == {'Sparkle.framework'}
if output.exists():
    # Back up only this independent edition before replacing it.
    backup=root/'dist/previous'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')/name
    backup.parent.mkdir(parents=True,exist_ok=True)
    shutil.copytree(output,backup,symlinks=True)
    shutil.rmtree(output)
output.parent.mkdir(parents=True, exist_ok=True)
subprocess.run(['ditto',str(prepared),str(output)],check=True)
subprocess.run(['codesign','--verify','--deep','--strict',str(output)],check=True)
print(output)
