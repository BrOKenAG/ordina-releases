#!/usr/bin/env python3
"""Run Python regressions and standalone Swift checks without a Resolve project.

    python3 scripts/verify.py                 # everything, including the lab
    python3 scripts/verify.py --no-acceptance # skip the end-to-end lab run
    python3 scripts/verify.py --no-typecheck  # skip the full-module Swift check

The per-feature Swift harnesses below compile only a subset of the sources.
verify_swift_typecheck.py additionally type-checks EVERY app source under the
real Xcode compile settings (all upcoming features, incl. MemberImportVisibility)
for Debug and Release. It needs Sparkle.framework from a prior build/resolve;
if absent, use --no-typecheck (a full xcodebuild still covers it on a real Mac).
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

arguments = sys.argv[1:]
unknown = [item for item in arguments
           if item not in ('--no-acceptance', '--no-typecheck')]
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
    'VerifyDeveloperTOTP': ['DeveloperTOTP'],
    'VerifyDeveloperAuthState': ['DeveloperAuthState', 'DeveloperAuthKeychain'],
    'VerifyIntegration': ['ResolveService', 'IntegrationInstaller'],
    'VerifyPluginProtocol': ['PluginProtocol'],
    'VerifyPluginInstaller': ['PluginProtocol', 'PluginEnvironment', 'PluginInstaller'],
    'VerifyPluginCatalog': ['PluginProtocol', 'PluginEnvironment', 'PluginUpdates',
                            'PluginInstaller', 'PluginCatalog'],
    'VerifyOptionalPlugins': ['ResolveService', 'PluginProtocol', 'PluginEnvironment',
                              'PluginInstaller', 'OptionalPluginInstaller'],
    'VerifyOnboarding': ['ResolveService', 'DestinationBookmark', 'IntegrationInstaller', 'OnboardingState'],
    'VerifyDeveloperCenter': ['ResolveService', 'PluginProtocol', 'PluginEnvironment',
                              'PluginUpdates', 'PluginInstaller', 'OptionalPluginInstaller',
                              'DeveloperBridge', 'DevPublisherTool', 'DevToolManager',
                              'DeveloperAuthentication', 'DeveloperAuthSession', 'DeveloperAuthState',
                              'DeveloperAuthKeychain', 'DeveloperTOTP', 'PluginPublishing',
                              'DeveloperCenter'],
    'VerifyPluginPublishing': ['ResolveService', 'PluginProtocol', 'PluginEnvironment',
                               'PluginUpdates', 'PluginInstaller', 'OptionalPluginInstaller',
                               'DeveloperBridge', 'DevPublisherTool', 'DevToolManager',
                               'DeveloperAuthentication', 'DeveloperAuthSession', 'DeveloperAuthState',
                               'DeveloperAuthKeychain', 'DeveloperTOTP', 'PluginPublishing'],
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

subprocess.run([sys.executable, '-B', str(root / 'scripts' / 'verify_native_plugin.py')],
               cwd=root, env=environment, check=True)

# Full-module Swift type-check under the real Xcode compile settings. It needs
# Sparkle.framework from a prior build/resolve; skip (with a clear note) when
# the workspace has not been built yet, so this file still runs on a clean tree.
if '--no-typecheck' not in arguments:
    products = root / '.build' / 'Build' / 'Products'
    has_sparkle = any((products / cfg / 'Sparkle.framework').is_dir()
                      for cfg in ('Debug', 'Release'))
    if has_sparkle:
        subprocess.run([sys.executable, '-B', str(root / 'scripts' /
                                                  'verify_swift_typecheck.py')],
                       cwd=root, env=environment, check=True)
    else:
        print('跳过全模块 Swift typecheck：缺少 Sparkle.framework'
              '（先完成一次 Xcode 构建或包解析后自动启用）。')

if '--no-acceptance' not in arguments:
    # The publishing tool, the client transport and the isolated backend are
    # exercised end to end in a throwaway HOME and lab repository.
    subprocess.run([sys.executable, '-B', str(root / 'scripts' /
                                              'verify_developer_center.py')],
                   cwd=root, env=environment, check=True)
print('Python 回归、Swift 验证与开发者中心验收通过。')
