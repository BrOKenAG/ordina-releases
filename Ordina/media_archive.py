import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import wave


class ArchiveError(Exception):
    pass


def is_within(path, roots):
    path = Path(path).resolve()
    return any(path == Path(root).resolve() or Path(root).resolve() in path.parents for root in roots)


def parse_mounts(output):
    result = []
    for line in output.splitlines():
        match = re.search(r" on (.+) \(([^,)]+)", line)
        if match:
            result.append((Path(match.group(1)), match.group(2)))
    return result


def parse_network_mounts(output):
    return [root for root, kind in parse_mounts(output) if kind in {"smbfs", "nfs", "afpfs", "webdav"}]


def is_local_source(path, mounts):
    raw_path = Path(path)
    if not raw_path.is_absolute() or raw_path == Path('/Volumes') or Path('/Volumes') in raw_path.parents:
        return False
    raw_matching = [(root, kind) for root, kind in mounts if raw_path == root or root in raw_path.parents]
    if raw_matching:
        raw_root, raw_kind = max(raw_matching, key=lambda item: len(str(item[0])))
        if raw_root not in {Path('/'), Path('/System/Volumes/Data')} or raw_kind not in {'apfs', 'hfs'}:
            return False
    path = Path(path).resolve()
    if not mounts or is_within(path, [Path('/Volumes')]):
        return False
    matching = [(root, kind) for root, kind in mounts if is_within(path, [root])]
    if not matching:
        return False
    root, kind = max(matching, key=lambda item: len(str(item[0])))
    if root not in {Path('/'), Path('/System/Volumes/Data')} or kind not in {"apfs", "hfs"}:
        return False
    try:
        return path.stat().st_dev in startup_devices()
    except FileNotFoundError:
        return True


def startup_devices():
    return {root.stat().st_dev for root in (Path('/'), Path('/System/Volumes/Data')) if root.exists()}


def clip_kind(properties):
    kind = str(properties.get("Type", "")).lower()
    if "video" in kind or "视频" in kind:
        return 'video'
    if kind in {"still", "image", "still image", "静帧", "图片", "静止图像"}:
        return 'image'
    if "audio" in kind or "音频" in kind:
        return 'audio'
    return 'unknown'


def classify(properties, duration):
    kind = clip_kind(properties)
    if kind == 'video':
        return "视频"
    if kind == 'image':
        return "图片"
    if kind == 'audio':
        if duration is None or not math.isfinite(duration) or duration <= 0:
            raise ArchiveError("无法读取音频时长，暂不归档。")
        return "音乐" if duration > 60 else "音效"
    raise ArchiveError("此素材类型需要手动处理。")


def audio_duration(path, properties):
    try:
        with wave.open(str(path), 'rb') as audio:
            return audio.getnframes() / audio.getframerate()
    except (wave.Error, EOFError):
        pass
    try:
        output = subprocess.check_output(['/usr/bin/afinfo', str(path)], stderr=subprocess.DEVNULL, timeout=15, text=True, encoding='utf-8')
        match = re.search(r'estimated duration:\s*([0-9.]+)\s*sec', output)
        if match:
            return float(match.group(1))
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        frames = float(properties.get('Frames', ''))
        fps = float(properties.get('FPS', ''))
        if frames > 0 and fps > 0:
            return frames / fps
    except (ValueError, TypeError):
        pass
    return None


def signature(path):
    info = Path(path).stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def digest(path, cancelled=lambda: False):
    checksum = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while True:
            if cancelled():
                raise ArchiveError("已暂停。")
            chunk = stream.read(4 * 1024 * 1024)
            if not chunk:
                break
            checksum.update(chunk)
    return checksum.hexdigest()


def safe_directory(root, relative):
    root = Path(root).resolve(strict=True)
    target = root.joinpath(relative)
    if not is_within(target, [root]):
        raise ArchiveError("归档目录超出项目范围，请检查目录链接。")
    target.mkdir(parents=True, exist_ok=True)
    if not is_within(target, [root]):
        raise ArchiveError("归档目录已变化，请重试。")
    return target


def archive_destination(root, category, name, expected, cancelled):
    base = safe_directory(root, '3_素材/' + category + '/归档')
    for number in range(1, 10001):
        folder = base if number == 1 else base / ('同名素材 ' + str(number))
        destination = folder / name
        if not is_within(destination, [root]) or destination.is_symlink():
            raise ArchiveError('归档路径包含无效链接，请检查目录。')
        if not destination.exists():
            safe_directory(root, folder.relative_to(root))
            return destination, False
        if destination.is_file() and digest(destination, cancelled) == expected:
            return destination, True
    raise ArchiveError('同名素材过多，请检查归档目录。')


def approved_archive_destination(root, category, name, expected, cancelled, proposed):
    destination = Path(proposed)
    base = root / '3_素材' / category / '归档'
    folder = destination.parent
    numbered = re.fullmatch(r'同名素材 ([0-9]+)', folder.name)
    valid_folder = folder == base or (folder.parent == base and numbered is not None
                                      and 2 <= int(numbered.group(1)) <= 10000
                                      and folder.name == '同名素材 ' + str(int(numbered.group(1))))
    if (not destination.is_absolute() or '..' in destination.parts or destination.name != name
            or not valid_folder or not is_within(destination, [root])
            or any(path.is_symlink() for path in (destination, *destination.parents))):
        raise ArchiveError('批准的归档路径无效，请重新扫描。')
    if destination.exists():
        if not destination.is_file() or digest(destination, cancelled) != expected:
            raise ArchiveError('归档目标已变化，请重新扫描。')
        return destination, True
    safe_directory(root, folder.relative_to(root))
    return destination, False


def verified_copy(source, project_root, category, progress=lambda *arguments: None, cancelled=lambda: False,
                  expected_destination=None, expected_signature=None):
    source = Path(source).resolve(strict=True)
    root = Path(project_root)
    if not root.is_dir():
        raise ArchiveError("项目目录不可用，请检查连接。")
    root = root.resolve(strict=True)
    if category not in {"视频", "图片", "音乐", "音效"}:
        raise ArchiveError("归档分类无效。")
    initial = signature(source)
    if expected_signature is not None and list(initial) != list(expected_signature):
        raise ArchiveError('源文件已变化，请重新扫描。')
    if not source.is_file() or initial[2] <= 0:
        raise ArchiveError("源文件为空或不可用。")
    progress(0, initial[2])
    expected = digest(source, cancelled)
    if signature(source) != initial:
        raise ArchiveError("源文件仍在变化，稍后重试。")
    if expected_destination is None:
        destination, existing = archive_destination(root, category, source.name, expected, cancelled)
    else:
        destination, existing = approved_archive_destination(root, category, source.name, expected, cancelled, expected_destination)
    result = {"source": str(source), "destination": str(destination), "sha256": expected, "bytes": initial[2]}
    if existing:
        if signature(source) != initial:
            raise ArchiveError('源文件已变化，保留原链接。')
        return result
    if shutil.disk_usage(root).free < initial[2] + 8 * 1024 * 1024:
        raise ArchiveError("目标可用空间不足，请清理后重试。")
    created_identity = None
    completed = False
    try:
        checksum = hashlib.sha256()
        copied = 0
        with source.open('rb') as input_stream, destination.open('xb') as output:
            created = os.fstat(output.fileno())
            created_identity = (created.st_dev, created.st_ino)
            while True:
                if cancelled():
                    raise ArchiveError("已暂停。")
                chunk = input_stream.read(4 * 1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                checksum.update(chunk)
                copied += len(chunk)
                progress(copied, initial[2])
            output.flush()
            os.fsync(output.fileno())
        if signature(source) != initial or copied != initial[2]:
            raise ArchiveError("源文件仍在变化，稍后重试。")
        if checksum.hexdigest() != expected or digest(destination, cancelled) != expected:
            raise ArchiveError("副本校验失败，保留原链接。")
        if signature(source) != initial:
            raise ArchiveError("源文件已变化，保留原链接。")
        completed = True
        return result
    finally:
        if not completed and created_identity is not None:
            try:
                current = destination.lstat()
                if (current.st_dev, current.st_ino) == created_identity and not destination.is_symlink():
                    destination.unlink()
            except FileNotFoundError:
                pass


def write_json(path, value):
    descriptor, temporary_name = tempfile.mkstemp(prefix='.magic-json-', dir=Path(path).parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
