"""Download the pinned official Sparkle tools and verify their archive."""
from pathlib import Path
import hashlib
import subprocess
import tempfile

VERSION = '2.10.1'
ARCHIVE_SHA256 = '1a856d2a9edb8e628de88121d7be04cf0e2167f8641650b78fca00b14244cf27'
KEYCHAIN_ACCOUNT = 'ordina-sparkle'
ROOT = Path(__file__).resolve().parents[1]


def ensure_tools():
    destination = ROOT / '.build' / ('sparkle-' + VERSION)
    if (destination / 'bin/sign_update').is_file():
        return destination / 'bin'
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ordina-sparkle-') as temporary:
        archive = Path(temporary) / 'Sparkle.tar.xz'
        url = f'https://github.com/sparkle-project/Sparkle/releases/download/{VERSION}/Sparkle-{VERSION}.tar.xz'
        subprocess.run(['/usr/bin/curl', '--fail', '--location', '--silent', '--show-error',
                        '--max-time', '120', url, '--output', str(archive)], check=True)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != ARCHIVE_SHA256:
            raise ValueError('Sparkle 下载校验失败。')
        destination.mkdir(exist_ok=True)
        subprocess.run(['/usr/bin/tar', '-xJf', str(archive), '-C', str(destination)], check=True)
    return destination / 'bin'
