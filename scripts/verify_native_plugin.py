#!/usr/bin/env python3
"""Exercise native installation using an ephemeral ad-hoc lab fixture only."""
import os
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='ordina-native-verification-') as temporary:
    root = Path(temporary).resolve()
    app = root/'Example.app'; contents = app/'Contents'; macos = contents/'MacOS'
    macos.mkdir(parents=True)
    shutil.copyfile('/usr/bin/true', macos/'Example'); (macos/'Example').chmod(0o700)
    resources = contents/'Resources'; resources.mkdir()
    (resources/'fixture.bin').write_bytes(os.urandom(1_100_000))
    (contents/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'test.ordina.fixture','CFBundleExecutable':'Example','CFBundleName':'Example','CFBundlePackageType':'APPL','CFBundleVersion':'1'}))
    subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(app)], check=True, capture_output=True)
    sources = ['PluginProtocol','PluginEnvironment','PluginInstaller']
    exe = root/'verify-native'
    subprocess.run(['/usr/bin/xcrun','swiftc','-D','ORDINA_LAB', *[str(ROOT/'Ordina'/f'{s}.swift') for s in sources], str(ROOT/'Tests/VerifyNativePlugin.swift'),'-o',str(exe)],check=True)
    env = dict(os.environ, ORDINA_PLUGIN_LAB='1', ORDINA_TEST_NATIVE_APP=str(app))
    subprocess.run([str(exe)],env=env,check=True)
    # The same signed App is accepted in production policy without lab flags.
    env.update(ORDINA_PLUGIN_LAB='0')
    subprocess.run([str(exe)],env=env,check=True)
    # Recalculate the inventory from tampered bytes: structural signature
    # validation must still reject the bundle even when file hashes match.
    (resources/'fixture.bin').write_bytes(b'tampered')
    env['ORDINA_TEST_EXPECT_REJECT']='1'
    subprocess.run([str(exe)],env=env,check=True)
