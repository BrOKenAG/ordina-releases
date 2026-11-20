import json
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import tempfile
import uuid

sys.dont_write_bytecode = True
ARCHIVE_RUNTIME_VERSION = 5
DEFAULT_SCAN_INTERVAL_SECONDS = 5


def scan_interval_seconds(config):
    value = config.get('scanIntervalSeconds')
    if type(value) is int and 1 <= value <= 86400:
        return value
    return DEFAULT_SCAN_INTERVAL_SECONDS


def set_scan_interval(seconds, path=None):
    text = str(seconds).strip()
    if not text.isdecimal() or len(text) > 5 or not 1 <= int(text) <= 86400:
        raise ValueError('请输入 1–86400 秒的整数。')
    path = Path(path) if path is not None else configuration_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = read_config(path)
    config['scanIntervalSeconds'] = int(text)
    write_json(path, config)
    return config


def set_automatic_archive(enabled, path=None):
    if type(enabled) is not bool:
        raise ValueError('自动归档设置无效。')
    path = Path(path) if path is not None else configuration_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = read_config(path)
    config['automaticArchive'] = enabled
    write_json(path, config)
    return config

def write_json(path, value):
    descriptor, name = tempfile.mkstemp(prefix='.settings-', dir=Path(path).parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def support_directory():
    return Path.home() / 'Library/Application Support/com.example.MagicTools.Youth/Archive'


def configuration_path():
    return support_directory() / 'config.json'


def process_alive(pid):
    try:
        return int(pid) > 1 and os.kill(int(pid), 0) is None
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False


def service_running(path):
    config = read_config(path)
    state = read_config(Path(path).with_name('status.json'))
    if state.get('instance') == config.get('instance') and state.get('running') is False:
        return False
    return process_alive(config.get('pid'))


def pause_service(path):
    config = read_config(path)
    if config:
        config['enabled'] = False
        write_json(path, config)


def wait_stopped(path, timeout=20):
    deadline = time.monotonic() + timeout
    while service_running(path):
        if time.monotonic() >= deadline:
            raise RuntimeError('正在结束当前扫描，请稍后重试。')
        time.sleep(0.1)


def configure_service(resources, automatic=None, roots=None, search_root=None, path=None):
    path = Path(path) if path is not None else configuration_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = read_config(path)
    desired = config.get('automatic', True) if automatic is None else bool(automatic)
    config['scanIntervalSeconds'] = scan_interval_seconds(config)
    config['automatic'] = desired
    if roots is not None:
        config['projectRoots'] = list(dict.fromkeys(list(config.get('projectRoots', [])) + list(roots)))
    if search_root:
        config['searchRoot'] = search_root
    if not desired:
        config['enabled'] = False
        write_json(path, config)
        return config
    names = ['archive_service.py', 'archive_worker.py', 'archive_plan.py', 'media_archive.py', 'resolve_bridge.py']
    resources = Path(resources)
    content = {name: (resources / name).read_bytes() for name in names}
    revision = hashlib.sha256(b''.join(content[name] for name in names)).hexdigest()
    if service_running(path) and config.get('enabled') and config.get('revision') == revision:
        write_json(path, config)
        return config
    config['enabled'] = False
    write_json(path, config)
    wait_stopped(path)
    for name, data in content.items():
        target = path.parent / name
        if not target.exists() or target.read_bytes() != data:
            target.write_bytes(data)
    config.update({'enabled': True, 'instance': uuid.uuid4().hex, 'revision': revision, 'startedAt': time.time()})
    write_json(path, config)
    environment = os.environ.copy()
    environment.update({'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1'})
    log_path = path.with_name('errors.log')
    if log_path.exists() and log_path.stat().st_size > 1024 * 1024:
        log_path.write_text('')
    with log_path.open('ab') as errors:
        process = subprocess.Popen(['/usr/bin/python3', '-B', '-u', str(path.parent / 'archive_service.py'), str(path), config['instance']],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors, env=environment, start_new_session=True)
    config['pid'] = process.pid
    write_json(path, config)
    return config


running = True


def read_config(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def service_enabled(path, instance):
    config = read_config(path)
    return config.get('enabled') is True and config.get('instance') == instance


def write_service_status(path, instance, event):
    config = read_config(path)
    if config.get('instance') != instance:
        return
    status_path = Path(path).with_name('status.json')
    status = read_config(status_path)
    if status.get('instance') != instance:
        status = {}
    status.update(event)
    status.update({'instance': instance, 'updatedAt': time.time()})
    if 'progress' not in event:
        status['progress'] = None
    write_json(status_path, status)


def stop(*arguments):
    global running
    running = False


def supervise(path, instance):
    worker = None
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        try:
            os.setsid()
        except OSError:
            pass
        while running and service_enabled(path, instance):
            write_service_status(path, instance, {'status': '正在连接…', 'running': True})
            worker = subprocess.Popen([sys.executable, '-B', '-u', str(Path(__file__).with_name('archive_worker.py')), '--service', str(path), instance], stdin=subprocess.DEVNULL)
            while worker.poll() is None:
                if not running or not service_enabled(path, instance):
                    worker.terminate()
                    worker.wait()
                    break
                time.sleep(0.25)
            if not running or not service_enabled(path, instance):
                break
            write_service_status(path, instance, {'status': '正在重新连接…', 'running': True})
            for interval in range(20):
                if not running or not service_enabled(path, instance):
                    break
                time.sleep(0.25)
    finally:
        if worker is not None and worker.poll() is None:
            worker.terminate()
            worker.wait()
        write_service_status(path, instance, {'status': '后台扫描已停止', 'running': False})


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == '--control':
        try:
            request = json.load(sys.stdin)
            path = Path(request['configPath'])
            if request['action'] == 'pause':
                pause_service(path)
                wait_stopped(path)
                response = read_config(path)
            elif request['action'] == 'set_interval':
                response = set_scan_interval(request.get('scanIntervalSeconds'), path)
            elif request['action'] == 'set_automatic_archive':
                set_automatic_archive(request.get('automaticArchive'), path)
                response = configure_service(request['resources'], path=path)
            else:
                response = configure_service(request['resources'], request.get('automatic'), request.get('projectRoots'), request.get('searchRoot'), path)
            print(json.dumps({'ok': True, 'data': response}, ensure_ascii=False))
        except Exception as error:
            print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
    else:
        supervise(Path(sys.argv[1]), sys.argv[2])
