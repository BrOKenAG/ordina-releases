#!/usr/bin/env python3
"""Build the two-icon ordina DMG with a portable Finder layout.
Setup: python3 -m pip install --target .build/dmg-libs ds_store==1.3.3 mac_alias==2.2.3
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse, subprocess, sys, shutil, struct
root = Path(__file__).resolve().parents[1]
# Keep installer dependencies isolated from the app's bundled runtimes.
sys.path.insert(0, str(root/'.build/dmg-libs'))
from ds_store import DSStore
from mac_alias import Alias, VolumeInfo, TargetInfo
from mac_alias.alias import ALIAS_NO_CNID
parser = argparse.ArgumentParser()
parser.add_argument('--app', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
app, output = args.app.resolve(), args.output.resolve()
output.parent.mkdir(parents=True, exist_ok=True)
subprocess.run([sys.executable, str(root/'scripts/render_dmg_background.py')], check=True)
subprocess.run([sys.executable, str(root/'Tests/verify_app.py'), str(app)], check=True)
stage = root/'.build/dmg-stage/ordina'
if stage.exists(): shutil.rmtree(stage)
stage.mkdir(parents=True)
subprocess.run(['ditto', str(app), str(stage/app.name)], check=True)
(stage/'Applications').symlink_to('/Applications')
subprocess.run(['tiffutil', '-cathidpicheck', str(root/'installer/drop.png'), str(root/'installer/drop@2x.png'), '-out', str(stage/'.background.tiff')], check=True)
shutil.copyfile(app/'Contents/Resources/AppIcon.icns', stage/'.VolumeIcon.icns')
subprocess.run(['SetFile', '-a', 'C', str(stage)], check=True)
subprocess.run(['SetFile', '-a', 'E', str(stage/app.name)], check=True)
now = datetime.fromtimestamp(int(datetime.now(timezone.utc).timestamp()), timezone.utc)
alias = Alias(volume=VolumeInfo('ordina', now, b'H+', 5, 0, b'\0\0', posix_path='/Volumes/ordina'),
              target=TargetInfo(0, '.background.tiff', 2, ALIAS_NO_CNID, now, b'\0'*4, b'\0'*4,
                                folder_name='ordina', carbon_path='ordina:.background.tiff'.encode('utf-8'), posix_path='/.background.tiff'))
with DSStore.open(str(stage/'.DS_Store'), 'w+') as ds:
    ds['.']['vSrn'] = ('long', 1)
    ds['.']['bwsp'] = dict(ShowStatusBar=False, WindowBounds='{{180, 140}, {720, 508}}', ShowToolbar=False,
                           ShowTabView=False, ShowPathbar=False, ShowSidebar=False, ContainerShowSidebar=False, PreviewPaneVisibility=False)
    ds['.']['icvp'] = dict(viewOptionsVersion=1, backgroundType=2, backgroundImageAlias=alias.to_bytes(),
                           backgroundColorRed=1., backgroundColorGreen=1., backgroundColorBlue=1.,
                           gridOffsetX=0., gridOffsetY=0., gridSpacing=100., arrangeBy='none', showIconPreview=False,
                           showItemInfo=False, labelOnBottom=True, textSize=14., iconSize=112., scrollPositionX=0., scrollPositionY=0.)
    ds['.']['icvl'] = ('type', b'icnv')
    ds['.']['vstl'] = ('type', b'icnv')
    ds[app.name]['Iloc'] = (190,230)
    ds['Applications']['Iloc'] = (530,230)
raw = root/'.build/installer-hfs.dmg'
subprocess.run(['hdiutil','makehybrid','-ov','-hfs','-hfs-volume-name','ordina','-hfs-openfolder',str(stage),'-o',str(raw),str(stage)], check=True)
# Match the image's volume date to the portable background alias. Both primary
# and backup HFS+ volume headers must agree. No device or mounted volume writes.
with raw.open('r+b') as f:
    f.seek(512); entry=f.read(512); count=struct.unpack_from('>I',entry,4)[0]
    for i in range(1,count+1):
        f.seek(i*512); entry=f.read(512)
        if entry[48:80].rstrip(b'\0') == b'Apple_HFS':
            start,blocks=struct.unpack_from('>II',entry,8);break
    else: raise RuntimeError('Missing HFS+ partition')
    for offset in (start*512+1024,(start+blocks)*512-1024):
        f.seek(offset);assert f.read(2)==b'H+'
        f.seek(offset+16);f.write(struct.pack('>I',int(now.timestamp())+2082844800))
# makehybrid initializes file/folder Finder positions to (-1, -1), which
# creates a nonempty FinderInfo xattr and invalidates strict Mach-O validation.
# Clear those 32 metadata bytes in HFS+ catalog records, preserving symlinks
# and volume-root metadata (custom icon). File data is untouched.
with raw.open('r+b') as f:
    volume_start = start * 512
    f.seek(volume_start + 1024)
    header = f.read(512)
    block_size = struct.unpack_from('>I', header, 40)[0]
    logical_size = struct.unpack_from('>Q', header, 272)[0]
    extents = [struct.unpack_from('>II', header, 288 + i*8) for i in range(8)]
    catalog = bytearray()
    for block, length in extents:
        if length:
            f.seek(volume_start + block * block_size)
            catalog.extend(f.read(length * block_size))
    assert len(catalog) >= logical_size, 'Catalog overflow extents need support'
    node_size = struct.unpack_from('>H', catalog, 32)[0]
    cleared = 0
    for node in range(0, logical_size, node_size):
        if catalog[node+8] != 255: continue
        records = struct.unpack_from('>H', catalog, node+10)[0]
        for i in range(records):
            offset = struct.unpack_from('>H', catalog, node+node_size-2*(i+1))[0]
            record = node + offset
            key_length = struct.unpack_from('>H', catalog, record)[0]
            data = record + key_length + 2
            kind = struct.unpack_from('>H', catalog, data)[0]
            if kind not in (1, 2): continue
            if kind == 1 and struct.unpack_from('>I', catalog, data+8)[0] == 2: continue
            mode = struct.unpack_from('>H', catalog, data+42)[0]
            if mode & 0xF000 == 0xA000: continue
            catalog[data+48:data+80] = bytes(32)
            cleared += 1
    cursor = 0
    for block, length in extents:
        if length:
            amount = length * block_size
            f.seek(volume_start + block * block_size)
            f.write(catalog[cursor:cursor+amount]); cursor += amount
    print('Sanitized Finder metadata on', cleared, 'file/folder records')
compressed = root/'.build/ordina.dmg'
subprocess.run(['hdiutil','convert',str(raw),'-format','UDZO','-imagekey','zlib-level=9','-ov','-o',str(compressed)],check=True)
subprocess.run(['hdiutil','verify',str(compressed)],check=True)
shutil.copyfile(compressed, output)
print(output)
