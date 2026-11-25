#!/usr/bin/env python3
"""Run Python regressions and standalone Swift checks without a Resolve project.

    python3 scripts/verify.py                 # everything, including the lab
    python3 scripts/verify.py --no-acceptance # skip the end-to-end lab run
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

arguments = sys.argv[1:]
unknown = [item for item in arguments if item != '--no-acceptance']
if unknown:
    sys.exit('未知参数：' + ', '.join(unknown))

root = Path(__file__).resolve().parents[1]
environment = os.environ.copy()
environment.setdefault('DEVELOPER_DIR', '/Applications/Xcode.app/Contents/Developer')
subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'Tests', '-p', 'test_*.py'],
               cwd=root, env=environment, check=True)
checks = {
    'VerifyNaming': ['ResolveService'],
    'VerifyDestination': ['ResolveService', 'DestinationBookmark'],
    'VerifyIntegration': ['ResolveService', 'IntegrationInstaller'],
    'VerifyPluginProtocol': ['PluginProtocol'],
    'VerifyPluginInstaller': ['PluginProtocol', 'PluginEnvironment', 'PluginInstaller'],
    'VerifyPluginCatalog': ['PluginProtocol', 'PluginEnvironment', 'PluginUpdates',
                            'PluginInstaller', 'PluginCatalog'],
    'VerifyOptionalPlugins': ['ResolveService', 'PluginProtocol', 'PluginEnvironment',
                              'PluginInstaller', 'OptionalPluginInstaller'],
    'VerifyOnboarding': ['ResolveService', 'DestinationBookmark', 'IntegrationInstaller', 'OnboardingState'],
    'VerifyDeveloperCenter': ['PluginProtocol', 'PluginEnvironment', 'PluginUpdates',
                              'PluginInstaller', 'DeveloperBridge', 'DevPublisherTool',
                              'DevToolManager', 'DeveloperCenter'],
}
with tempfile.TemporaryDirectory(prefix='ordina-verification-') as temporary:
    for name, sources in checks.items():
        executable = Path(temporary) / name
        command = ['/usr/bin/xcrun', 'swiftc']
        # The developer centre's lab switches are compiled in only for its own
        # verification; every other check keeps the production assumptions.
        if name == 'VerifyDeveloperCenter':
            command += ['-D', 'ORDINA_LAB']
        command += [str(root / 'Ordina' / (source + '.swift')) for source in sources]
        command += [str(root / 'Tests' / (name + '.swift')), '-o', str(executable)]
        subprocess.run(command, cwd=root, env=environment, check=True)
        subprocess.run([str(executable)], cwd=temporary, env=environment, check=True)
if '--no-acceptance' not in arguments:
    # The publishing tool, the client transport and the isolated backend are
    # exercised end to end in a throwaway HOME and lab repository.
    subprocess.run([sys.executable, '-B', str(root / 'scripts' /
                                              'verify_developer_center.py')],
                   cwd=root, env=environment, check=True)
print('Python 回归、Swift 验证与开发者中心验收通过。')
