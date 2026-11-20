from pathlib import Path
import argparse
import subprocess
import sys
import plistlib
import re

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, default=root / 'dist')
parser.add_argument('--release-label', default='')
parser.add_argument('--app', type=Path, help='复用已构建的 App，仅重新打包 DMG')
args = parser.parse_args()
if args.release_label and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', args.release_label):
    parser.error('发布标签只能包含英文字母、数字、点和连字符。')
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=True)
app = args.app.resolve() if args.app else output / 'ordina.app'
if not args.app:
    subprocess.run([sys.executable, str(root / 'scripts/build_app.py'), '--output', str(app)], check=True)
subprocess.run([sys.executable, str(root / 'Tests/verify_app.py'), str(app)], check=True)
version = plistlib.loads((app / 'Contents/Info.plist').read_bytes())['CFBundleShortVersionString']
artifact_version = version + ('-' + args.release_label if args.release_label else '')
subprocess.run([sys.executable, str(root / 'scripts/build_dmg.py'), '--app', str(app),
                '--output', str(output / f'ordina-{artifact_version}.dmg')], check=True)
print(output)
