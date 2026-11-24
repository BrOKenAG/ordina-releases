#!/usr/bin/env python3
"""验证 ordina 标识、中文配置、资源与签名。"""
from pathlib import Path
import plistlib,subprocess,sys
app=Path(sys.argv[1]) if len(sys.argv)>1 else Path.home()/'Desktop/ordina.app'
d=plistlib.loads((app/'Contents/Info.plist').read_bytes())
assert d['CFBundleIdentifier']=='com.example.MagicTools.Youth'
assert d['CFBundleDevelopmentRegion']=='zh-Hans'
assert d['CFBundleDisplayName']=='ordina'
localized=plistlib.loads(subprocess.check_output(['plutil','-convert','xml1','-o','-',str(app/'Contents/Resources/zh-Hans.lproj/InfoPlist.strings')]))
assert localized['CFBundleDisplayName']=='ordina'
assert localized['CFBundleName']=='ordina'
assert d['CFBundleShortVersionString']=='1.9.6'
assert d['CFBundleVersion']=='34'
assert d['SUFeedURL']=='https://brokenag.github.io/ordina-releases/appcast.xml'
assert d['SUPublicEDKey']=='90vua+GDSP8MSl6owCI/WSIkqBBuXO/AGw0ny4p67Bw='
assert d['SUVerifyUpdateBeforeExtraction'] is True
assert d['SURequireSignedFeed'] is True
assert (app/'Contents/Resources/Project Setup.py').exists()
assert (app/'Contents/Resources/THIRD_PARTY_NOTICES.md').exists()
assert (app/'Contents/Resources/resolve_bridge.py').exists()
for component in ('archive_worker.py', 'archive_service.py', 'media_archive.py', 'archive.request', 'archive.auto', 'Import YueLiu.py', 'Multicam Tools.py'):
    assert (app/'Contents/Resources'/component).exists()
assert (app/'Contents/Resources/archive_plan.py').exists()
root=Path(__file__).resolve().parents[1]
resources=app/'Contents/Resources'
for name in ('archive_worker.py','archive_service.py','archive_plan.py','media_archive.py','resolve_bridge.py'):
    assert (resources/name).read_bytes()==(root/'Ordina'/name).read_bytes(), name+' 不是当前源码'
for name in ('Import YueLiu.py','Multicam Tools.py'):
    assert (resources/name).read_bytes()==(root/'Integrations/OptionalPlugins'/name).read_bytes(), name+' 资源不符'
assert (resources/'Minutes Roughcut/extension/manifest.json').is_file()
assert (resources/'Minutes Roughcut/plugin.json').is_file()
assert (resources/'Minutes Roughcut/install.py').is_file()
assert (resources/'Project Setup.py').read_bytes()==(root/'Integrations/Project Setup.py').read_bytes()
assert not (resources/'导入阅流.py').exists()
for removed in ('Helpers',):
    assert not (app/'Contents'/removed).exists()
frameworks = app/'Contents/Frameworks'
assert {item.name for item in frameworks.iterdir()} == {'Sparkle.framework'}
sparkle = frameworks/'Sparkle.framework'
assert (sparkle/'Versions/Current').is_symlink()
assert (sparkle/'Sparkle').is_symlink()
assert (sparkle/'Versions/B/Autoupdate').is_file()
assert (sparkle/'Versions/B/Updater.app').is_dir()
assert not list(app.rglob('HaoyePet.bundle'))
subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True)
entitlements=plistlib.loads(subprocess.check_output(['codesign','-d','--entitlements',':-',str(app)],stderr=subprocess.DEVNULL))
assert not entitlements.get('com.apple.security.app-sandbox',False)
assert entitlements.get('com.apple.security.network.client') is True
assert entitlements.get('com.apple.security.cs.disable-library-validation') is True
details=subprocess.check_output(['codesign','-dv',str(app)],stderr=subprocess.STDOUT,text=True)
assert 'runtime' in details
arch=subprocess.check_output(['lipo','-archs',str(app/'Contents/MacOS'/d['CFBundleExecutable'])],text=True).strip()
assert not list(app.rglob('*.pyc'))
assert arch=='arm64'
print('通过：独立身份、中文配置、Apple 芯片架构、精简资源及严格签名验证。')
