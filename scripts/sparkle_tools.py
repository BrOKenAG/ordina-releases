"""Download the pinned official Sparkle tools and verify their archive.

The publishing engine is deployed as a standalone zipapp outside the source
tree. Deriving a writable directory from ``__file__`` would point inside the
(possibly read-only) archive location, and deriving it from a lab-only variable
would make production behaviour depend on test configuration. Neither is
acceptable: use a stable per-user cache by default, with an explicit override
for CI / release machines that keep a pinned copy.
"""
import os
from pathlib import Path
import hashlib
import subprocess
import tempfile

VERSION = '2.10.1'
ARCHIVE_SHA256 = '1a856d2a9edb8e628de88121d7be04cf0e2167f8641650b78fca00b14244cf27'
KEYCHAIN_ACCOUNT = 'ordina-sparkle'
TOOLS_ENV = 'ORDINA_SPARKLE_TOOLS'


def tools_root():
    """Writable location of the pinned Sparkle distribution.

    ``ORDINA_SPARKLE_TOOLS`` is an explicit deployment override (not a lab
    switch and not a path into the source tree). Without it, the tool owns a
    stable directory under the user's Application Support, which exists and is
    writable in a standalone deployment with no repository checkout at all.
    """
    override = os.environ.get(TOOLS_ENV, '').strip()
    if override:
        return Path(override).expanduser().resolve()
    home = Path(os.environ.get('HOME') or Path.home()).expanduser().resolve()
    return (home / 'Library' / 'Application Support' / 'ordina' / 'Publisher'
            / 'tools' / ('sparkle-' + VERSION))


def tools_bin():
    """Directory containing ``sign_update``.

    Accept either a full tools root (``<root>/bin/sign_update``, the layout
    produced by extracting the official archive) or a directory that already
    holds the executables directly.
    """
    root = tools_root()
    if (root / 'sign_update').is_file():
        return root
    return root / 'bin'


def ensure_tools():
    destination = tools_root()
    binaries = tools_bin()
    if (binaries / 'sign_update').is_file():
        return binaries
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ordina-sparkle-') as temporary:
        archive = Path(temporary) / 'Sparkle.tar.xz'
        url = f'https://github.com/sparkle-project/Sparkle/releases/download/{VERSION}/Sparkle-{VERSION}.tar.xz'
        subprocess.run(['/usr/bin/curl', '--fail', '--location', '--silent', '--show-error',
                        '--max-time', '120', url, '--output', str(archive)], check=True)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != ARCHIVE_SHA256:
            raise ValueError('Sparkle 下载校验失败。')
        subprocess.run(['/usr/bin/tar', '-xJf', str(archive), '-C', str(destination)], check=True)
    if not (binaries / 'sign_update').is_file():
        raise ValueError('Sparkle 归档结构不符合预期。')
    return binaries
