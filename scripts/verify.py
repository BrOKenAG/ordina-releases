#!/usr/bin/env python3
"""Run Python regressions and standalone Swift checks without a Resolve project."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
environment = os.environ.copy()
environment.setdefault('DEVELOPER_DIR', '/Applications/Xcode.app/Contents/Developer')
subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'Tests', '-p', 'test_*.py'],
               cwd=root, env=environment, check=True)
checks = {
    'VerifyNaming': ['ResolveService'],
    'VerifyDestination': ['ResolveService', 'DestinationBookmark'],
    'VerifyIntegration': ['ResolveService', 'IntegrationInstaller'],
    'VerifyOptionalPlugins': ['ResolveService', 'IntegrationInstaller', 'OptionalPluginInstaller'],
    'VerifyPluginUpdates': ['ResolveService', 'IntegrationInstaller', 'OptionalPluginInstaller', 'PluginUpdates'],
    'VerifyOnboarding': ['ResolveService', 'DestinationBookmark', 'IntegrationInstaller', 'OnboardingState'],
}
with tempfile.TemporaryDirectory(prefix='ordina-verification-') as temporary:
    for name, sources in checks.items():
        executable = Path(temporary) / name
        command = ['/usr/bin/xcrun', 'swiftc']
        command += [str(root / 'Ordina' / (source + '.swift')) for source in sources]
        command += [str(root / 'Tests' / (name + '.swift')), '-o', str(executable)]
        subprocess.run(command, cwd=root, env=environment, check=True)
        subprocess.run([str(executable)], cwd=temporary, env=environment, check=True)
print('Python 回归与 Swift 验证通过。')
