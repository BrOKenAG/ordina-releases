import json
import math
import os
from pathlib import Path
import tempfile
import plistlib
import importlib.util
import sys
import subprocess
import time
import uuid
from datetime import datetime


TEMPLATE_CONFIG_VERSION = 1
ROOT_TARGET_LABEL = "媒体池根目录"
DEFAULT_TIMELINE_TARGET = "03_Timeline/01_合板及素材筛选"
DEFAULT_FOLDER_PATHS = [
    "01_Aroll",
    "02_Broll",
    "03_Timeline",
    "03_Timeline/01_合板及素材筛选",
    "03_Timeline/02_正片时间线",
    "04_BGM",
    "05_VFX",
    "06_SFX",
    "07_Subtitles",
    "08_network",
    "09_Image",
]


def template_config_path(path=None):
    if path is not None:
        return Path(path)
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "13rOKen4nGeL"
        / "DaVinciScripts"
        / "start_work_template.json"
    )


def default_template_config():
    return {
        "version": TEMPLATE_CONFIG_VERSION,
        "folders": list(DEFAULT_FOLDER_PATHS),
        "timeline_target": DEFAULT_TIMELINE_TARGET,
    }


def parse_folder_paths(text):
    paths = []
    seen = set()
    for line_number, raw_line in enumerate(str(text).splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        if any(ord(char) < 32 or ord(char) == 127 for char in line):
            raise ValueError(f"第 {line_number} 行包含无效字符")

        parts = [part.strip() for part in line.split("/")]
        if any(not part or part in (".", "..") for part in parts):
            raise ValueError(
                f"第 {line_number} 行路径无效：不能包含空目录、. 或 .."
            )

        path = "/".join(parts)
        if path not in seen:
            paths.append(path)
            seen.add(path)

    if not paths:
        raise ValueError("模板至少需要一个有效文件夹路径")
    return paths


def folder_paths_to_text(paths):
    return "\n".join(paths)


def expanded_folder_paths(paths):
    result = []
    seen = set()
    for path in paths:
        parts = path.split("/")
        for length in range(1, len(parts) + 1):
            parent = "/".join(parts[:length])
            if parent not in seen:
                result.append(parent)
                seen.add(parent)
    return result


def normalize_template_config(data):
    if not isinstance(data, dict):
        raise ValueError("模板配置必须是 JSON 对象")
    if data.get("version") != TEMPLATE_CONFIG_VERSION:
        raise ValueError("模板配置版本不受支持")

    folders = data.get("folders")
    if not isinstance(folders, list) or not all(
        isinstance(path, str) for path in folders
    ):
        raise ValueError("模板 folders 必须是字符串数组")
    if any("\n" in path or "\r" in path for path in folders):
        raise ValueError("每个文件夹路径只能占一行")
    normalized_folders = parse_folder_paths(folder_paths_to_text(folders))

    timeline_target = data.get("timeline_target", "")
    if not isinstance(timeline_target, str):
        raise ValueError("模板 timeline_target 必须是字符串")
    timeline_target = timeline_target.strip()
    if timeline_target and timeline_target not in expanded_folder_paths(normalized_folders):
        raise ValueError("锁帧位置不在文件夹模板中，请重新选择")

    return {
        "version": TEMPLATE_CONFIG_VERSION,
        "folders": normalized_folders,
        "timeline_target": timeline_target,
    }


def evaluate_template_draft(text, selected_target, saved_config):
    try:
        paths = parse_folder_paths(text)
        target = selected_target if selected_target in expanded_folder_paths(paths) else ""
        draft = normalize_template_config({
            "version": TEMPLATE_CONFIG_VERSION,
            "folders": paths,
            "timeline_target": target,
        })
        saved = normalize_template_config(saved_config)
    except (ValueError, TypeError) as exc:
        return {
            "config": None,
            "error": str(exc),
            "changed": False,
            "dirty": True,
            "is_default": False,
        }

    dirty = draft != saved
    return {
        "config": draft,
        "error": "",
        "changed": dirty,
        "dirty": dirty,
        "is_default": draft == default_template_config(),
    }


def load_template_config(path=None):
    config_path = template_config_path(path)
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return normalize_template_config(data), ""
    except FileNotFoundError:
        return default_template_config(), ""
    except (OSError, ValueError, TypeError) as exc:
        return default_template_config(), f"模板配置无法读取，已使用默认模板：{exc}"


def save_template_config(config, path=None):
    normalized = normalize_template_config(config)
    config_path = template_config_path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(config_path.parent),
            prefix=config_path.name + ".", suffix=".tmp", delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temp_path.replace(config_path)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
    return normalized


def ensure_folder_paths(media_pool, root_folder, paths):
    folders_by_path = {}
    for path in paths:
        parent = root_folder
        parts = []
        for name in path.split("/"):
            parts.append(name)
            current_path = "/".join(parts)
            if current_path in folders_by_path:
                parent = folders_by_path[current_path]
                continue

            child = None
            for existing in parent.GetSubFolderList() or []:
                if existing.GetName() == name:
                    child = existing
                    break
            if not child:
                child = media_pool.AddSubFolder(parent, name)
            if not child:
                raise RuntimeError(f"无法创建媒体池文件夹：{current_path}")

            folders_by_path[current_path] = child
            parent = child
    return folders_by_path


def choose_timeline_folder(root_folder, folders_by_path, target_path):
    if not target_path:
        return root_folder
    if target_path not in folders_by_path:
        raise RuntimeError("找不到锁帧位置，请检查文件夹模板")
    return folders_by_path[target_path]


PLUGIN_VERSION = "v2.3"
ARCHIVE_CLASSIFICATIONS = {
    'archived': '已归档', 'raw': '原始素材', 'shared': '共享素材',
    'trusted': '可信外部素材', 'pending': '待归档', 'offline': '媒体离线',
    'unknown': '外部引用',
}


def archive_scan_summary(scan, project_id, database):
    if not (isinstance(scan, dict) and project_id and scan.get('projectID') == project_id
            and isinstance(database, dict) and database.get('DbName')
            and isinstance(scan.get('database'), dict)
            and all(database.get(key, '') == scan['database'].get(key, '')
                    for key in ('DbType', 'DbName', 'IpAddress'))
            and isinstance(scan.get('items'), list)):
        return None
    items = [item for item in scan['items'] if isinstance(item, dict)]
    counts = {key: 0 for key in ARCHIVE_CLASSIFICATIONS}
    for item in items:
        kind = item.get('classification', 'unknown')
        counts[kind if isinstance(kind, str) and kind in counts else 'unknown'] += 1
    return {'items': items, 'counts': counts}


def read_archive_snapshot(path=None, project_id=None, database=None, project_name='', alive=None):
    """Read saved state only; never start a worker or inspect media files."""
    path = Path(path) if path is not None else (
        Path.home() / 'Library/Application Support/com.example.MagicTools.Youth/Archive/config.json')

    def read_object(file):
        try:
            value = json.loads(file.read_text(encoding='utf-8'))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def process_alive(pid):
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except OSError:
            return False

    config = read_object(path)
    state = read_object(path.with_name('status.json'))
    same_instance = bool(config.get('instance')) and config['instance'] == state.get('instance')
    if not same_instance:
        state = {}
    result = {'project': project_name or '未打开项目', 'status': '暂无后台扫描数据',
              'updated': '', 'counts': None, 'items': [], 'resultLabel': '上次扫描'}
    if not project_id:
        result['status'] = '未打开项目'
        return result
    scan = state.get('scan')
    summary = archive_scan_summary(scan, project_id, database)
    if summary is not None:
        result.update(summary, scan=scan)
    timestamp = state.get('updatedAt')
    result['timestamp'] = timestamp if isinstance(timestamp, (int, float)) else 0
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool) and math.isfinite(timestamp) and timestamp > 0:
        try:
            result['updated'] = '状态更新 ' + datetime.fromtimestamp(timestamp).strftime('%m-%d %H:%M:%S')
        except (ValueError, OverflowError, OSError):
            pass
    running = (same_instance and state.get('running') is True
               and (alive or process_alive)(config.get('pid')))
    status = state.get('status') if isinstance(state.get('status'), str) else ''
    if config.get('automatic') is False or config.get('enabled') is False:
        result['status'] = '后台归档已关闭'
    elif not config:
        pass
    elif status and status != '监测中':
        result['status'] = status
    elif not running:
        result['status'] = '后台归档未运行'
    elif summary is None:
        result['status'] = '暂无当前工程扫描记录'
    else:
        result.update(status='扫描完成', resultLabel='扫描结果')
    return result


def archive_app_candidates():
    locations = [Path('/Applications'), Path.home() / 'Applications', Path.home() / 'Desktop']
    # Legacy application names remain discoverable for installed users.
    candidates = [location / name for name in ('ordina.app', 'Ordo.app', '项目起点.app') for location in locations]
    def build_version(candidate):
        try:
            info = plistlib.loads((candidate / 'Contents/Info.plist').read_bytes())
            if info.get('CFBundleIdentifier') != 'com.example.MagicTools.Youth':
                return -1
            return int(info.get('CFBundleVersion', 0))
        except (OSError, ValueError, TypeError):
            return -1
    return sorted(candidates, key=build_version, reverse=True)


def archive_runtime():
    local = Path.home() / 'Library/Application Support/com.example.MagicTools.Youth/Archive'
    fallback = None
    if (local / 'archive_service.py').is_file():
        module = load_archive_runtime(local)
        if getattr(module, 'ARCHIVE_RUNTIME_VERSION', 0) >= 5:
            return module, local
        if getattr(module, 'ARCHIVE_RUNTIME_VERSION', 0) >= 2:
            fallback = (module, local)
    candidates = archive_app_candidates()
    resources = None
    for candidate in candidates:
        info = candidate / 'Contents/Info.plist'
        if info.is_file() and plistlib.loads(info.read_bytes()).get('CFBundleIdentifier') == 'com.example.MagicTools.Youth':
            folder = candidate / 'Contents/Resources'
            if (folder / 'archive_service.py').is_file():
                module = load_archive_runtime(folder)
                if getattr(module, 'ARCHIVE_RUNTIME_VERSION', 0) >= 5:
                    return module, folder
                if fallback is None:
                    fallback = (module, folder)
    if fallback is not None:
        return fallback
    if resources is None:
        resources = local
    return load_archive_runtime(resources), resources


def load_archive_runtime(resources):
    script = resources / 'archive_service.py'
    if not script.is_file():
        raise RuntimeError('请先安装新版归档组件。')
    spec = importlib.util.spec_from_file_location('_project_setup_archive_service', script)
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


class ArchiveScanJob:
    """A cancellable scan subprocess. Polling never calls into Resolve."""
    def __init__(self, resources, request, python='/usr/bin/python3', action='archive_scan'):
        self.temporary = tempfile.TemporaryDirectory(prefix='ordo-scan-')
        self.directory = Path(self.temporary.name)
        self.process = None
        self.result = None
        self.started = time.monotonic()
        self.action = action
        try:
            input_path = self.directory / 'request.json'
            input_path.write_text(json.dumps(dict(request, action=action)), encoding='utf-8')
            environment = os.environ.copy()
            environment.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
            with input_path.open('rb') as source, (self.directory / 'result.txt').open('wb') as output:
                self.process = subprocess.Popen(
                    [python, '-B', str(Path(resources) / 'archive_worker.py'),
                     '--once' if action == 'archive_scan' else '--archive-action'],
                    stdin=source, stdout=output, stderr=subprocess.DEVNULL, env=environment)
        except Exception:
            self.cancel()
            raise

    def poll(self):
        if self.result is not None:
            return self.result
        if self.process.poll() is None:
            if self.action == 'archive_execute' or time.monotonic() - self.started < 60:
                return None
            self.cancel()
            self.result = {'ok': False, 'error': '扫描超时，请检查存储连接后重试。'}
            return self.result
        try:
            prefix = 'MAGIC_ARCHIVE_SCAN:'
            lines = (self.directory / 'result.txt').read_text(encoding='utf-8').splitlines()
            line = next(line for line in reversed(lines) if line.startswith(prefix))
            result = json.loads(line[len(prefix):])
            if not isinstance(result, dict) or not isinstance(result.get('ok'), bool):
                raise ValueError('Invalid scan response')
            if result['ok'] and not isinstance(result.get('data'), dict):
                raise ValueError('Invalid scan result')
            self.result = result
        except (OSError, ValueError, StopIteration):
            self.result = {'ok': False, 'error': '扫描未完成，请检查 Resolve 和存储连接后重试。'}
        finally:
            self.temporary.cleanup()
        return self.result

    def cancel(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1)
        self.temporary.cleanup()
        if self.result is None:
            self.result = {'ok': False, 'error': '扫描已取消'}
CUSTOM_LABEL = "自定义"
RES_OPTIONS = {
    "HD": ["1920", "1080"],
    "UHD": ["3840", "2160"],
    "竖屏 HD": ["1080", "1920"],
    "竖屏 4K": ["2160", "3840"],
    CUSTOM_LABEL: ["", ""],
}
FPS_OPTIONS = ["23.976", "24", "25", "29.97", "30", "48", "50", "59.94", "60", "100", "119.88", "120", CUSTOM_LABEL]
SETTING_LABELS = {
    "timelineResolutionWidth": "宽度",
    "timelineResolutionHeight": "高度",
    "timelineFrameRate": "帧率",
}


def format_fps(value):
    try:
        number = float(value)
        if math.isfinite(number):
            return str(int(number)) if number.is_integer() else str(number)
    except (ValueError, TypeError, OverflowError):
        pass
    return str(value)


def fps_preset_index(value):
    normalized = format_fps(value)
    return FPS_OPTIONS.index(normalized) if normalized in FPS_OPTIONS else len(FPS_OPTIONS) - 1


def validate_inputs(width, height, fps):
    errors = []
    for label, value in (("宽度", width), ("高度", height)):
        try:
            number = int(str(value).strip())
            if not 1 <= number <= 16384:
                errors.append(label + "需在 1–16384 之间")
        except (ValueError, TypeError):
            errors.append(label + "必须是整数")
    try:
        number = float(fps)
        if not math.isfinite(number) or not 1 <= number <= 240:
            errors.append("帧率需在 1–240 之间")
    except (ValueError, TypeError, OverflowError):
        errors.append("帧率必须是数字")
    return not errors, "\n".join(errors)


def read_project_settings(project):
    # Resolve 21 的批量接口；保留旧版本脚本兼容性。
    getter = getattr(project, "GetSettings", None)
    settings = getter() if callable(getter) else None
    if settings is None:
        settings = {key: project.GetSetting(key) for key in SETTING_LABELS}
    if not isinstance(settings, dict) or any(settings.get(key) in (None, "") for key in SETTING_LABELS):
        raise RuntimeError("无法读取项目设置，请重新打开项目后刷新")
    return {key: str(settings[key]) for key in SETTING_LABELS}


def write_project_setting(project, key, value):
    setter = getattr(project, "SetSettings", None)
    if callable(setter):
        result = setter({key: str(value)})
        if result is not None:
            return result
    return project.SetSetting(key, str(value))


def setting_matches(key, actual, expected):
    try:
        if key == "timelineFrameRate":
            return math.isclose(float(actual), float(expected), rel_tol=0, abs_tol=0.0001)
        return int(actual) == int(expected)
    except (ValueError, TypeError, OverflowError):
        return False


def deploy_project(project, config, width, height, fps):
    """校验后初始化；所有 API 失败均中止，并尝试恢复本次修改的设置。"""
    if project is None:
        raise RuntimeError("请先打开项目")
    if project.GetTimelineCount() > 0:
        raise RuntimeError("项目已有时间线，无法初始化")
    ok, error = validate_inputs(width, height, fps)
    if not ok:
        raise ValueError(error)
    config = normalize_template_config(config)
    desired = {
        "timelineResolutionWidth": str(int(str(width).strip())),
        "timelineResolutionHeight": str(int(str(height).strip())),
        "timelineFrameRate": format_fps(fps),
    }
    previous = read_project_settings(project)
    name = project.GetName()
    pool = project.GetMediaPool()
    if pool is None:
        raise RuntimeError("无法读取媒体池，请重新打开项目")
    root = pool.GetRootFolder()
    previous_folder = pool.GetCurrentFolder()
    if root is None or previous_folder is None:
        raise RuntimeError("无法读取媒体池文件夹，请重新打开项目")
    attempted = []
    switched = False
    try:
        for key, value in desired.items():
            if setting_matches(key, previous[key], value):
                continue
            attempted.append(key)
            if not write_project_setting(project, key, value):
                raise RuntimeError("无法设置" + SETTING_LABELS[key] + "，请检查项目支持的格式")
        actual = read_project_settings(project)
        for key, value in desired.items():
            if not setting_matches(key, actual[key], value):
                raise RuntimeError(SETTING_LABELS[key] + "未生效，请检查项目支持的格式")
        folders = ensure_folder_paths(pool, root, config["folders"])
        target = choose_timeline_folder(root, folders, config["timeline_target"])
        switched = True  # 即使 API 报错，也尝试恢复当前目录。
        if not pool.SetCurrentFolder(target):
            raise RuntimeError("无法切换到锁帧位置，请检查媒体池权限")
        timeline = pool.CreateEmptyTimeline("锁帧")
        if not timeline:
            raise RuntimeError("无法创建锁帧时间线，请检查项目后重试")
    except Exception as exc:
        recovery_errors = []
        if switched:
            try:
                if not pool.SetCurrentFolder(previous_folder):
                    recovery_errors.append("媒体池位置")
            except Exception:
                recovery_errors.append("媒体池位置")
        for key in reversed(attempted):
            try:
                current = read_project_settings(project)
                if not setting_matches(key, current[key], previous[key]):
                    write_project_setting(project, key, previous[key])
                if not setting_matches(key, read_project_settings(project)[key], previous[key]):
                    recovery_errors.append(SETTING_LABELS[key])
            except Exception:
                recovery_errors.append(SETTING_LABELS[key])
        message = str(exc) or "初始化失败，请检查项目后重试"
        if recovery_errors:
            message += "\n请手动检查：" + "、".join(recovery_errors)
        raise RuntimeError(message) from exc
    return {"project": name, "width": desired["timelineResolutionWidth"],
            "height": desired["timelineResolutionHeight"], "fps": format_fps(actual["timelineFrameRate"])}


class StartWorkApp:
    def __init__(self, resolve, ui, dispatcher, config_path=None):
        self.resolve = resolve
        self.manager = resolve.GetProjectManager()
        if self.manager is None:
            raise RuntimeError("无法连接项目管理器，请重新启动脚本")
        self.ui = ui
        self.dispatcher = dispatcher
        self.config_path = config_path
        self.config, self.warning = load_template_config(config_path)
        self.project_id = None
        self.editable = False
        self.busy = False
        self.completed = False
        self.updating = False
        self.template_open = False
        self.template_window = None
        self.discard_window = None
        self.archive_window = None
        self.archive_open = False
        self.archive_job = None
        self.archive_scan_timer = None
        self.archive_scan_result = None
        self.archive_scan_error = ''
        self.archive_action_message = ''
        self.archive_review_window = None
        self.archive_locations_window = None
        self.archive_locations_scan = None
        self.archive_context = None
        self.archive_context_checked_at = 0
        self.archive_review_open = False
        self.archive_review_rows = []
        self.archive_cache_stamp = None
        self.template_target = self.config["timeline_target"]
        self.template_updating = False
        self.window = self.build_main_window()
        self.refresh_project()
        self.open_media_archive(automatic=True)

    def label(self, text):
        return self.ui.Label({"Text": text, "Weight": 0,
                              "MinimumSize": [76, 28], "MaximumSize": [76, 32]})

    def button(self, identity, text, primary=False):
        properties = {"ID": identity, "Text": text, "Weight": 0,
                      "MinimumSize": [88, 32]}
        if primary:
            properties["StyleSheet"] = (
                "QPushButton {background-color: #3677a8; color: #ffffff; "
                "border: 1px solid #4885b2; border-radius: 4px; padding: 7px 18px;}"
                "QPushButton:hover {background-color: #4085b8;}"
                "QPushButton:focus {border: 1px solid #b1d5f5;}"
                "QPushButton:disabled {background-color: #33383e; color: #7f858c; border-color: #42474e;}"
            )
        return self.ui.Button(properties)

    def build_main_window(self):
        ui = self.ui
        window = self.dispatcher.AddWindow({
            "ID": "ProjectSetupWindow", "WindowTitle": "Project Setup · " + PLUGIN_VERSION,
            "Geometry": [260, 180, 540, 390], "MinimumSize": [520, 370], "Margin": 20,
        }, ui.VGroup({"Spacing": 10}, [
            ui.HGroup({"Weight": 0, "Spacing": 12}, [
                ui.Label({"ID": "ProjectName", "Text": "", "WordWrap": True,
                          "Font": ui.Font({"PixelSize": 15, "Bold": True})}),
                self.button("BtnRefresh", "刷新"),
            ]),
            ui.Label({"Text": "时间线", "Weight": 0,
                      "Font": ui.Font({"PixelSize": 13, "Bold": True})}),
            ui.HGroup({"Weight": 0, "Spacing": 12}, [
                self.label("分辨率"), ui.ComboBox({"ID": "ComboRes", "Weight": 1.2, "MinimumSize": [110, 30]}),
                ui.LineEdit({"ID": "CustomW", "Text": "1920", "ToolTip": "宽度", "Weight": 1, "MinimumSize": [70, 30]}),
                ui.Label({"Text": "×", "Weight": 0}),
                ui.LineEdit({"ID": "CustomH", "Text": "1080", "ToolTip": "高度", "Weight": 1, "MinimumSize": [70, 30]}),
            ]),
            ui.HGroup({"Weight": 0, "Spacing": 12, "MinimumSize": [0, 30]}, [
                self.label("帧率"), ui.ComboBox({"ID": "ComboFPS", "Editable": True,
                    "ToolTip": "选择帧率或直接输入", "MinimumSize": [0, 30]}),
                ui.Label({"Text": "fps", "Weight": 0, "StyleSheet": "color: #9ca2aa;"}),
            ]),
            ui.VGap(4),
            ui.HGroup({"Weight": 0, "Spacing": 12}, [
                ui.Label({"Text": "文件夹模板", "Weight": 0,
                          "Font": ui.Font({"PixelSize": 13, "Bold": True})}),
                ui.Label({"ID": "TemplateSummary", "Text": "", "StyleSheet": "color: #9ca2aa;"}),
                self.button("BtnEditTemplate", "编辑…"),
            ]),
            ui.HGroup({"Weight": 0, "Spacing": 12}, [
                self.label("锁帧位置"),
                ui.Label({"ID": "TimelineTarget", "Text": "", "WordWrap": True}),
            ]),
            ui.HGroup({"Weight": 0, "Spacing": 12}, [
                ui.CheckBox({"ID": "AutoArchive", "Text": "后台扫描", "Checked": True,
                             "ToolTip": "扫描间隔可在归档状态中设置。"}),
                self.button("BtnArchiveBind", "关联 NAS…"), self.button("BtnMediaArchive", "归档状态…"),
            ]),
            ui.Label({"ID": "ArchiveStatus", "Text": "", "WordWrap": True, "Weight": 0, "Visible": False,
                      "StyleSheet": "color: #e39791;"}),
            ui.Label({"ID": "StatusLabel", "Text": "", "WordWrap": True, "Weight": 0, "Visible": False}),
            ui.VGap(0),
            ui.HGroup({"Weight": 0, "Spacing": 10}, [
                self.button("BtnClose", "关闭"), ui.Label({"Text": "", "Weight": 1}),
                self.button("BtnDeploy", "初始化", True),
            ]),
        ]))
        items = window.GetItems()
        for name in RES_OPTIONS:
            items["ComboRes"].AddItem(name)
        for fps in FPS_OPTIONS[:-1]:
            items["ComboFPS"].AddItem(fps)
        window.On.BtnRefresh.Clicked = self.refresh_project
        window.On.BtnDeploy.Clicked = self.deploy
        window.On.BtnClose.Clicked = self.close
        window.On.BtnMediaArchive.Clicked = self.open_media_archive
        window.On.AutoArchive.Clicked = self.change_auto_archive
        window.On.BtnArchiveBind.Clicked = self.bind_archive
        window.On.ProjectSetupWindow.Close = self.close
        window.On.BtnEditTemplate.Clicked = self.show_template_editor
        window.On.ComboRes.CurrentIndexChanged = self.toggle_custom_inputs
        return window

    def open_media_archive(self, ev=None, automatic=False):
        try:
            if automatic:
                service, resources = archive_runtime()
                service.configure_service(resources)
                self.refresh_archive_setting()
                return
            self.show_archive_status()
        except Exception as exc:
            if automatic:
                self.archive_status(str(exc))
            else:
                self.set_status('无法打开素材归档：' + str(exc), 'error')

    def archive_window_data(self):
        project = self.manager.GetCurrentProject()
        identity = project.GetUniqueId() if project is not None else None
        name = project.GetName() if project is not None else ''
        database = self.manager.GetCurrentDatabase() if project is not None else None
        context = (identity, database)
        if context != getattr(self, 'archive_context', None):
            if getattr(self, 'archive_context', None) is not None:
                self.archive_scan_result = None
                self.archive_scan_error = ''
                self.archive_action_message = ''
            self.archive_context = context
        data = read_archive_snapshot(project_id=identity, project_name=name, database=database)
        manual = getattr(self, 'archive_scan_result', None)
        if manual is not None:
            summary = archive_scan_summary(manual['scan'], identity, database)
            if summary is not None and manual.get('timestamp', float('inf')) >= data.get('timestamp', 0):
                data.update(summary, scan=manual['scan'], status='扫描完成',
                            resultLabel='手动扫描', updated=manual['updated'])
        if getattr(self, 'archive_action_message', ''):
            data['status'] = self.archive_action_message
        if getattr(self, 'archive_scan_error', ''):
            data.update(status=self.archive_scan_error, resultLabel='上次扫描')
        if getattr(self, 'archive_job', None) is not None:
            text = {'archive_execute': '正在归档…', 'storage_update': '正在保存…'}
            data.update(status=text.get(getattr(self.archive_job, 'action', None), '正在扫描…'), resultLabel='上次扫描')
        return data

    def show_archive_status(self, ev=None):
        if self.busy:
            return
        if self.archive_window is None:
            ui = self.ui
            self.archive_window = self.dispatcher.AddWindow({
                'ID': 'ArchiveStatusWindow', 'WindowTitle': '归档状态',
                'Geometry': [310, 220, 740, 495], 'MinimumSize': [660, 455], 'Margin': 20,
            }, ui.VGroup({'Spacing': 12}, [
                ui.Label({'ID': 'ArchiveProject', 'Text': '', 'Weight': 0,
                          'Font': ui.Font({'PixelSize': 16, 'Bold': True})}),
                ui.Label({'ID': 'ArchiveDestination', 'Text': '', 'WordWrap': True, 'Weight': 0}),
                ui.Label({'ID': 'ArchiveState', 'Text': '', 'WordWrap': True, 'Weight': 0}),
                ui.Label({'ID': 'ArchiveUpdated', 'Text': '', 'Weight': 0,
                          'StyleSheet': 'color: #9ca2aa;'}),
                ui.Label({'ID': 'ArchiveCounts', 'Text': '', 'Weight': 0, 'WordWrap': True}),
                ui.HGroup({'Weight': 0, 'Spacing': 12}, [
                    ui.CheckBox({'ID': 'ArchiveAutomatic', 'Text': '自动归档', 'Checked': False,
                                 'ToolTip': '后台扫描开启时，时间线停稳后自动归档临时素材。'}),
                    ui.Label({'Text': '', 'Weight': 1}),
                    ui.CheckBox({'ID': 'ArchiveShowAll', 'Text': '显示全部', 'Checked': False}),
                ]),
                ui.HGroup({'Weight': 0, 'Spacing': 8}, [
                    ui.Label({'Text': '扫描间隔', 'Weight': 0}),
                    ui.LineEdit({'ID': 'ArchiveInterval', 'Text': '5', 'Weight': 0,
                                 'MinimumSize': [56, 28], 'MaximumSize': [72, 32],
                                 'ToolTip': '1–86400 秒'}),
                    ui.Label({'Text': '秒', 'Weight': 1}),
                    self.button('BtnArchiveIntervalSave', '保存'),
                ]),
                ui.Label({'ID': 'ArchiveEmpty', 'Text': '', 'WordWrap': True, 'Weight': 0}),
                ui.Tree({'ID': 'ArchiveMaterials', 'ColumnCount': 3, 'HeaderHidden': False,
                         'UniformRowHeights': True, 'Weight': 1}),
                ui.HGroup({'Weight': 0, 'Spacing': 10}, [
                    self.button('BtnArchiveScan', '立即扫描'),
                    self.button('BtnArchiveRaw', '素材位置…'),
                    self.button('BtnArchiveKeep', '保留引用'),
                    self.button('BtnArchiveReview', '归档…'),
                    ui.Label({'ID': 'ArchiveLimit', 'Text': '', 'Weight': 1,
                              'StyleSheet': 'color: #9ca2aa;'}),
                    self.button('BtnArchiveClose', '关闭'),
                ]),
            ]))
            tree = self.archive_window.GetItems()['ArchiveMaterials']
            tree.SetHeaderLabels(['素材', '状态', '原始路径'])
            tree.ColumnWidth[0] = 160
            tree.ColumnWidth[1] = 110
            tree.ColumnWidth[2] = 350
            self.archive_window.On.BtnArchiveScan.Clicked = self.start_archive_scan
            self.archive_window.On.BtnArchiveRaw.Clicked = self.show_archive_locations
            self.archive_window.On.BtnArchiveKeep.Clicked = self.keep_archive_reference
            self.archive_window.On.ArchiveMaterials.ItemClicked = self.refresh_archive_selection
            self.archive_window.On.BtnArchiveReview.Clicked = self.show_archive_review
            self.archive_window.On.BtnArchiveIntervalSave.Clicked = self.save_archive_interval
            self.archive_window.On.ArchiveAutomatic.Clicked = self.save_automatic_archive
            self.archive_window.On.ArchiveShowAll.Clicked = self.tick_archive_status
            self.archive_window.On.BtnArchiveClose.Clicked = self.hide_archive_status
            self.archive_window.On.ArchiveStatusWindow.Close = self.hide_archive_status
        self.archive_open = True
        self.refresh_archive_setting()
        self.archive_window.GetItems()['BtnArchiveScan'].Enabled = getattr(self, 'archive_job', None) is None
        self.tick_archive_status()
        self.archive_window.Show()
        self.archive_window.Raise()
        self.start_archive_timer()

    def start_archive_timer(self):
        if self.archive_scan_timer is None:
            self.archive_scan_timer = self.ui.Timer({'ID': 'ArchiveScanTimer', 'Interval': 500, 'SingleShot': False})
            self.dispatcher.On.Timeout = self.archive_timer_event
        self.archive_scan_timer.Start()

    def archive_timer_event(self, event=None):
        if event and event.get('who') != 'ArchiveScanTimer':
            return
        if self.archive_job is not None:
            self.poll_archive_scan()
            return
        if not self.archive_open or self.archive_review_open:
            return
        path = Path.home() / 'Library/Application Support/com.example.MagicTools.Youth/Archive/status.json'
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            stamp = None
        changed = stamp != self.archive_cache_stamp
        now = time.monotonic()
        if now - self.archive_context_checked_at >= 2:
            self.archive_context_checked_at = now
            try:
                project = self.manager.GetCurrentProject()
                context = (project.GetUniqueId(), self.manager.GetCurrentDatabase()) if project is not None else (None, None)
                changed = changed or context != self.archive_context
            except Exception:
                changed = True
        if changed:
            self.archive_cache_stamp = stamp
            self.tick_archive_status()

    def render_archive_status(self, data):
        items = self.archive_window.GetItems()
        items['ArchiveProject'].Text = '当前工程：' + data['project'] if data['project'] != '未打开项目' else data['project']
        scan = data.get('scan') or {}
        root = scan.get('projectRoot')
        items['ArchiveDestination'].Text = '归档到：' + root if root else '归档到：未确认'
        items['ArchiveState'].Text = data['status']
        items['ArchiveUpdated'].Text = data['updated']
        self.archive_displayed_scan = data.get('scan')
        idle = self.archive_job is None
        items['BtnArchiveScan'].Enabled = idle
        items['BtnArchiveRaw'].Enabled = idle and bool(self.archive_displayed_scan)
        items['BtnArchiveReview'].Enabled = idle and bool(self.pending_archive_items(self.archive_displayed_scan))
        items['BtnArchiveKeep'].Enabled = False
        items['BtnArchiveKeep'].Text = '保留引用'
        items['BtnArchiveClose'].Enabled = idle or self.archive_job.action != 'archive_execute'
        counts = data['counts']
        show_all = items['ArchiveShowAll'].Checked
        visible = data['items'] if show_all else [entry for entry in data['items']
                                                  if entry.get('classification') in {'pending', 'offline'}]
        labels = ARCHIVE_CLASSIFICATIONS if show_all else {'pending': '待归档', 'offline': '离线'}
        items['ArchiveCounts'].Text = ('   '.join(label + ' ' + str(counts[kind])
                                                    for kind, label in labels.items()) if counts is not None else '')
        items['ArchiveEmpty'].Text = '暂无当前工程扫描记录' if counts is None else (
            ('未发现文件素材' if show_all else '没有待归档或离线素材') if not visible else '')
        items['ArchiveEmpty'].Visible = bool(items['ArchiveEmpty'].Text)
        items['ArchiveLimit'].Text = ('前 200 项 · 共 ' + str(len(visible)) + ' 项'
                                      if len(visible) > 200 else '')
        tree = items['ArchiveMaterials']
        tree.Clear()
        self.archive_material_rows = []
        ordered = sorted(visible, key=lambda entry: entry.get('classification') != 'pending')
        for entry in ordered[:200]:
            row = tree.NewItem()
            row.Text[0] = str(entry.get('name') or '未命名素材')
            row.Text[1] = ARCHIVE_CLASSIFICATIONS.get(entry.get('classification'), '外部引用')
            row.Text[2] = str(entry.get('source') or '—')
            tree.AddTopLevelItem(row)
            self.archive_material_rows.append((entry, row))
        self.archive_window.RecalcLayout()

    def tick_archive_status(self, ev=None):
        if not self.archive_open or self.busy:
            return
        try:
            self.render_archive_status(self.archive_window_data())
        except Exception as exc:
            self.render_archive_status({'project': '归档状态', 'status': '读取失败：' + str(exc),
                                        'updated': '', 'counts': None, 'items': [], 'resultLabel': ''})

    def hide_archive_status(self, ev=None):
        if getattr(getattr(self, 'archive_job', None), 'action', None) == 'archive_execute':
            self.archive_window.Show()
            return
        if getattr(self, 'archive_review_open', False):
            self.hide_archive_review()
        if getattr(self, 'archive_locations_scan', None) is not None:
            self.hide_archive_locations()
        self.archive_open = False
        if getattr(self, 'archive_job', None) is not None:
            self.archive_job.cancel()
            self.archive_job = None
            self.archive_scan_error = '扫描已取消'
        if getattr(self, 'archive_scan_timer', None) is not None:
            self.archive_scan_timer.Stop()
        if self.archive_window is not None:
            self.archive_window.Hide()

    def start_archive_scan(self, ev=None):
        if self.busy or self.archive_job is not None:
            return
        try:
            project = self.manager.GetCurrentProject()
            if project is None:
                raise RuntimeError('请先打开项目。')
            service, resources = archive_runtime()
            if getattr(service, 'ARCHIVE_RUNTIME_VERSION', 0) < 2:
                raise RuntimeError('请更新 Project Setup 归档组件。')
            config_path = service.configuration_path()
            config = service.read_config(config_path)
            request = {'action': 'archive_scan', 'projectID': project.GetUniqueId(),
                       'database': self.manager.GetCurrentDatabase(),
                       'projectRoots': config.get('projectRoots', []), 'searchRoot': config.get('searchRoot'),
                       '_scanStateDirectory': str(config_path.parent)}
            self.archive_scan_error = ''
            self.archive_action_message = ''
            self.archive_job = ArchiveScanJob(resources, request)
            self.start_archive_timer()
        except Exception as exc:
            if self.archive_job is not None:
                self.archive_job.cancel()
                self.archive_job = None
            self.archive_scan_error = str(exc)
        self.archive_window.GetItems()['BtnArchiveScan'].Enabled = self.archive_job is None
        self.tick_archive_status()

    def poll_archive_scan(self, ev=None):
        if self.archive_job is None:
            return
        response = self.archive_job.poll()
        if response is None:
            return
        action = self.archive_job.action
        self.archive_job = None
        if not getattr(self, 'archive_open', False):
            self.archive_scan_timer.Stop()
        if response.get('ok'):
            data = response['data']
            fresh = data.get('freshScan') if action in {'archive_execute', 'storage_update'} else data
            self.archive_scan_result = ({'scan': fresh, 'timestamp': time.time(),
                                         'updated': '状态更新 ' + datetime.now().strftime('%m-%d %H:%M:%S')}
                                        if fresh else None)
            if action == 'archive_execute':
                results = data.get('results', [])
                archived = sum(item.get('actualResult') == 'relinked' for item in results)
                retained = sum(item.get('actualResult') == 'copy_retained' for item in results)
                failed = len(results) - archived - retained
                self.archive_action_message = f'已归档 {archived} 项 · 副本已保留 {retained} 项 · 未复制 {failed} 项'
                warnings = list(dict.fromkeys(item['warning'] for item in results if item.get('warning')))
                if warnings:
                    self.archive_action_message += '\n' + '\n'.join(warnings[:3])
            elif action == 'storage_update':
                self.archive_action_message = '已保存素材设置'
            self.archive_scan_error = ''
        else:
            self.archive_scan_error = response.get('error') or '扫描失败，请重试。'
        self.archive_window.GetItems()['BtnArchiveScan'].Enabled = True
        self.tick_archive_status()

    @staticmethod
    def pending_archive_items(scan):
        return [item for item in (scan or {}).get('items', [])
                if item.get('classification') == 'pending' and item.get('method') == 'copy']

    def show_archive_review(self, ev=None):
        if self.archive_job is not None:
            return
        scan = self.archive_displayed_scan
        pending = self.pending_archive_items(scan)
        unknown = [item for item in self.selected_archive_items() if item.get('classification') == 'unknown'
                   and item.get('method') == 'copy']
        pending += unknown
        if not pending:
            return
        if self.archive_review_window is None:
            ui = self.ui
            self.archive_review_window = self.dispatcher.AddWindow({
                'ID': 'ArchiveReviewWindow', 'WindowTitle': '确认归档',
                'Geometry': [330, 260, 850, 360], 'MinimumSize': [650, 300], 'Margin': 16,
            }, ui.VGroup({'Spacing': 10}, [
                ui.Label({'Text': '复制、校验并更新 Resolve 引用。源文件保留。', 'Weight': 0}),
                ui.Tree({'ID': 'ArchiveReviewTree', 'ColumnCount': 5, 'Weight': 1}),
                ui.HGroup({'Weight': 0, 'Spacing': 8}, [
                    self.button('BtnCancelArchive', '取消'),
                    ui.Label({'ID': 'ArchiveReviewError', 'Text': '', 'Weight': 1}),
                    self.button('BtnConfirmArchive', '确认归档', True),
                ]),
            ]))
            self.archive_review_window.On.BtnCancelArchive.Clicked = self.hide_archive_review
            self.archive_review_window.On.ArchiveReviewWindow.Close = self.hide_archive_review
            self.archive_review_window.On.BtnConfirmArchive.Clicked = self.confirm_archive_review
            tree = self.archive_review_window.GetItems()['ArchiveReviewTree']
            tree.SetHeaderLabels(['素材', '大小', '来源', '目标', '提示'])
            for index, width in enumerate([150, 82, 180, 200, 180]):
                tree.ColumnWidth[index] = width
        tree = self.archive_review_window.GetItems()['ArchiveReviewTree']
        tree.Clear()
        self.archive_review_scan = scan
        self.archive_review_rows = []
        for item in pending:
            row = tree.NewItem()
            row.CheckState[0] = 'Unchecked' if item.get('classification') == 'unknown' else 'Checked'
            values = [item['name'], f"{item.get('bytes', 0) / 1048576:.1f} MB",
                      item['source'], item['destination'], item.get('warning', '')]
            for index, value in enumerate(values):
                row.Text[index] = str(value)
                row.ToolTip[index] = str(value)
            tree.AddTopLevelItem(row)
            self.archive_review_rows.append((item, row))
        self.archive_review_window.GetItems()['ArchiveReviewError'].Text = ''
        self.archive_review_open = True
        self.archive_review_window.Show()
        self.archive_review_window.Raise()

    def hide_archive_review(self, ev=None):
        self.archive_review_open = False
        self.archive_review_window.Hide()
        self.archive_review_rows = []

    def confirm_archive_review(self, ev=None):
        selected = [item for item, row in self.archive_review_rows if row.CheckState[0] == 'Checked']
        if not selected:
            self.archive_review_window.GetItems()['ArchiveReviewError'].Text = '请选择素材'
            return
        scan = self.archive_review_scan
        self.hide_archive_review()
        self.start_archive_action('archive_execute', scan, {
            'preview': scan['snapshot'],
            'selectedIDs': [item['id'] for item in selected],
            'approvedUnknownIDs': [item['id'] for item in selected if item.get('classification') == 'unknown'],
        })

    def selected_archive_items(self):
        return [entry for entry, row in getattr(self, 'archive_material_rows', []) if row.Selected]

    def archive_reference_selection(self):
        return [entry for entry in self.selected_archive_items()
                if entry.get('id') and entry.get('classification') in {'pending', 'unknown'}]

    def refresh_archive_selection(self, ev=None):
        items = self.archive_window.GetItems()
        selected = self.selected_archive_items()
        references = self.archive_reference_selection()
        undo = references and all(entry.get('decision') == 'keep' for entry in references)
        items['BtnArchiveKeep'].Text = '恢复归档' if undo else '保留引用'
        items['BtnArchiveKeep'].Enabled = self.archive_job is None and bool(references)
        items['BtnArchiveReview'].Enabled = self.archive_job is None and (
            bool(self.pending_archive_items(self.archive_displayed_scan)) or
            any(entry.get('classification') == 'unknown' and entry.get('method') == 'copy' for entry in selected))

    def keep_archive_reference(self, ev=None):
        selected = self.archive_reference_selection()
        decision = 'archive' if all(entry.get('decision') == 'keep' for entry in selected) else 'keep'
        decisions = {entry['id']: decision for entry in selected}
        if decisions and self.archive_displayed_scan:
            self.start_archive_action('storage_update', self.archive_displayed_scan, {'decisions': decisions})

    def show_archive_locations(self, ev=None):
        scan = self.archive_displayed_scan
        if not scan or self.archive_job is not None:
            return
        if self.archive_locations_window is None:
            ui = self.ui
            window = self.dispatcher.AddWindow({
                'ID': 'ArchiveLocationsWindow', 'WindowTitle': '素材位置',
                'Geometry': [330, 240, 750, 410], 'MinimumSize': [650, 360], 'Margin': 18,
            }, ui.VGroup({'Spacing': 10}, [
                ui.Label({'ID': 'LocationProject', 'Text': '', 'Weight': 0}),
                ui.Tree({'ID': 'LocationList', 'ColumnCount': 3, 'Weight': 1}),
                ui.HGroup({'Weight': 0, 'Spacing': 8}, [
                    ui.ComboBox({'ID': 'LocationKind', 'Weight': 0}),
                    ui.LineEdit({'ID': 'LocationLogicalID', 'PlaceholderText': '逻辑根名称（可选）', 'Weight': 1,
                                 'ToolTip': '两块盘使用相同名称，登记为同一个逻辑素材根。'}),
                    self.button('BtnLocationAdd', '选择目录…'),
                ]),
                ui.HGroup({'Weight': 0, 'Spacing': 8}, [
                    self.button('BtnLocationRemove', '移除登记'),
                    ui.Label({'Text': '', 'Weight': 1}),
                    self.button('BtnLocationClose', '关闭'),
                ]),
            ]))
            self.archive_locations_window = window
            items = window.GetItems()
            items['LocationList'].SetHeaderLabels(['类型', '逻辑根', '路径'])
            for index, width in enumerate([110, 150, 420]):
                items['LocationList'].ColumnWidth[index] = width
            for title in ('原始素材', '共享素材', '可信目录', '临时目录'):
                items['LocationKind'].AddItem(title)
            window.On.BtnLocationAdd.Clicked = self.add_archive_location
            window.On.BtnLocationRemove.Clicked = self.remove_archive_location
            window.On.BtnLocationClose.Clicked = self.hide_archive_locations
            window.On.ArchiveLocationsWindow.Close = self.hide_archive_locations
        self.archive_locations_scan = scan
        items = self.archive_locations_window.GetItems()
        items['LocationProject'].Text = '当前工程：' + scan['projectName']
        items['LocationKind'].CurrentIndex = 0
        items['LocationLogicalID'].Text = ''
        tree = items['LocationList']
        tree.Clear()
        self.archive_location_rows = []
        labels = {'raw': '原始素材', 'shared': '共享素材', 'trusted': '可信目录', 'temp': '临时目录'}
        for location in scan.get('locations', []):
            row = tree.NewItem()
            row.Text[0] = labels.get(location['kind'], location['kind'])
            row.Text[1] = location['logicalID']
            row.Text[2] = location['path']
            tree.AddTopLevelItem(row)
            self.archive_location_rows.append((location, row))
        self.archive_locations_window.Show()
        self.archive_locations_window.Raise()

    def hide_archive_locations(self, ev=None):
        self.archive_locations_scan = None
        self.archive_locations_window.Hide()

    def add_archive_location(self, ev=None):
        scan = self.archive_locations_scan
        if not scan or self.archive_job is not None:
            return
        items = self.archive_locations_window.GetItems()
        kinds = ('raw', 'shared', 'trusted', 'temp')
        index = items['LocationKind'].CurrentIndex
        if not 0 <= index < len(kinds):
            return
        kind = kinds[index]
        suggestions = scan.get('suggestions', [])
        initial = suggestions[0]['path'] if kind == 'raw' and suggestions else str(Path.home())
        folder = self.resolve.Fusion().RequestDir(initial)
        if folder:
            identity = uuid.uuid4().hex
            logical = items['LocationLogicalID'].Text.strip() or Path(folder).name or identity
            self.hide_archive_locations()
            self.start_archive_action('storage_update', scan, {
                'addLocation': {'id': identity, 'logicalID': logical, 'kind': kind, 'path': folder},
            })

    def remove_archive_location(self, ev=None):
        scan = self.archive_locations_scan
        location = next((location for location, row in self.archive_location_rows if row.Selected), None)
        if scan and location and self.archive_job is None:
            self.hide_archive_locations()
            self.start_archive_action('storage_update', scan, {'removeLocation': location['id']})

    def start_archive_action(self, action, scan, request):
        if self.archive_job is not None:
            return
        try:
            project = self.manager.GetCurrentProject()
            if project is None or archive_scan_summary(scan, project.GetUniqueId(), self.manager.GetCurrentDatabase()) is None:
                raise RuntimeError('工程已切换，请重新扫描。')
            service, resources = archive_runtime()
            if getattr(service, 'ARCHIVE_RUNTIME_VERSION', 0) < 4:
                raise RuntimeError('请更新 ordina 归档组件后重试。')
            request.update(projectID=scan['projectID'], database=scan['database'], projectRoot=scan['projectRoot'],
                           _scanStateDirectory=str(service.configuration_path().parent))
            self.archive_scan_error = self.archive_action_message = ''
            self.archive_job = ArchiveScanJob(resources, request, action=action)
            self.start_archive_timer()
        except Exception as exc:
            if self.archive_job is not None:
                self.archive_job.cancel()
                self.archive_job = None
            self.archive_scan_error = str(exc)
        self.tick_archive_status()

    def archive_status(self, text=''):
        label = self.window.GetItems()['ArchiveStatus']
        label.Text = text
        label.Visible = bool(text)
        self.window.RecalcLayout()

    def refresh_archive_setting(self):
        try:
            service, resources = archive_runtime()
            config = service.read_config(service.configuration_path())
            self.window.GetItems()['AutoArchive'].Checked = config.get('automatic', True)
            interval = (service.scan_interval_seconds(config) if hasattr(service, 'scan_interval_seconds') else 5)
            self.window.GetItems()['AutoArchive'].ToolTip = '每 ' + str(interval) + ' 秒检查素材变化。'
            if self.archive_window is not None:
                items = self.archive_window.GetItems()
                items['ArchiveInterval'].Text = str(interval)
                items['ArchiveAutomatic'].Checked = config.get('automaticArchive', True) is True
                items['ArchiveAutomatic'].Enabled = config.get('automatic', True)
        except Exception:
            pass

    def save_automatic_archive(self, ev=None):
        try:
            service, resources = archive_runtime()
            enabled = bool(self.archive_window.GetItems()['ArchiveAutomatic'].Checked)
            service.set_automatic_archive(enabled)
            service.configure_service(resources)
            self.archive_scan_error = ''
        except Exception as exc:
            self.archive_scan_error = str(exc)
            self.refresh_archive_setting()
        self.tick_archive_status()

    def save_archive_interval(self, ev=None):
        try:
            service, resources = archive_runtime()
            if not hasattr(service, 'set_scan_interval'):
                raise RuntimeError('请更新 ordina 归档组件后重试。')
            value = self.archive_window.GetItems()['ArchiveInterval'].Text
            service.set_scan_interval(value)
            self.archive_scan_error = ''
            self.refresh_archive_setting()
            self.tick_archive_status()
            self.archive_window.GetItems()['ArchiveState'].Text = '扫描间隔已保存'
        except Exception as exc:
            self.archive_scan_error = str(exc)
            self.tick_archive_status()

    def change_auto_archive(self, ev=None):
        try:
            service, resources = archive_runtime()
            desired = bool(self.window.GetItems()['AutoArchive'].Checked)
            service.configure_service(resources, automatic=desired)
            self.archive_status()
            self.refresh_archive_setting()
        except Exception as exc:
            self.archive_status(str(exc))
            self.refresh_archive_setting()

    def bind_archive(self, ev=None):
        service = None
        resources = None
        try:
            service, resources = archive_runtime()
            service.pause_service(service.configuration_path())
            service.wait_stopped(service.configuration_path())
            current = self.manager.GetCurrentProject()
            if current is None or current.GetUniqueId() != self.project_id:
                raise RuntimeError('项目已切换，请刷新后再关联。')
            identity = current.GetUniqueId()
            config = service.read_config(service.configuration_path())
            selected = self.resolve.Fusion().RequestDir(config.get('searchRoot') or str(Path.home()))
            if not selected:
                return
            current = self.manager.GetCurrentProject()
            if current is None or current.GetUniqueId() != identity:
                raise RuntimeError('项目已切换，请重新关联。')
            previous_paths = list(sys.path)
            previous_bytecode = sys.dont_write_bytecode
            try:
                sys.path.insert(0, str(resources))
                sys.dont_write_bytecode = True
                spec = importlib.util.spec_from_file_location('_project_setup_resolve_bridge', resources / 'resolve_bridge.py')
                bridge = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(bridge)
                bridge.run(self.resolve, {'action': 'bind', 'projectRoot': selected})
            finally:
                sys.path[:] = previous_paths
                sys.dont_write_bytecode = previous_bytecode
            service.configure_service(resources, roots=[selected], search_root=config.get('searchRoot') or str(Path(selected).parent))
            self.archive_status()
        except Exception as exc:
            self.archive_status(str(exc))
        finally:
            if service is not None:
                try:
                    service.configure_service(resources)
                except Exception as exc:
                    self.archive_status(str(exc))

    def set_status(self, text="", kind="warning"):
        label = self.window.GetItems()["StatusLabel"]
        colors = {"warning": "#d6b678", "error": "#e39791", "success": "#9bc5a5"}
        label.Text = text
        label.StyleSheet = "color: " + colors.get(kind, colors["warning"]) + ";"
        label.Visible = bool(text)
        self.window.RecalcLayout()

    def update_template_summary(self):
        items = self.window.GetItems()
        items["TemplateSummary"].Text = str(len(expanded_folder_paths(self.config["folders"]))) + " 个文件夹"
        items["TimelineTarget"].Text = self.config["timeline_target"] or ROOT_TARGET_LABEL
        items["TimelineTarget"].ToolTip = items["TimelineTarget"].Text

    def refresh_project(self, ev=None):
        if self.busy or self.template_open:
            return
        items = self.window.GetItems()
        self.updating = True
        self.completed = False
        self.editable = False
        try:
            project = self.manager.GetCurrentProject()
            self.project_id = project.GetUniqueId() if project is not None else None
            items["BtnDeploy"].Text = "初始化"
            if project is None:
                items["ProjectName"].Text = "未打开项目"
                self.set_status("请打开项目后刷新")
            else:
                items["ProjectName"].Text = project.GetName()
                settings = read_project_settings(project)
                width = str(int(settings["timelineResolutionWidth"]))
                height = str(int(settings["timelineResolutionHeight"]))
                fps = format_fps(settings["timelineFrameRate"])
                names = list(RES_OPTIONS)
                selected = next((name for name, size in RES_OPTIONS.items() if size == [width, height]), CUSTOM_LABEL)
                items["ComboRes"].CurrentIndex = names.index(selected)
                items["CustomW"].Text = width
                items["CustomH"].Text = height
                fps_index = fps_preset_index(fps)
                if fps_index < len(FPS_OPTIONS) - 1:
                    items["ComboFPS"].CurrentIndex = fps_index
                else:
                    items["ComboFPS"].CurrentText = fps
                self.editable = project.GetTimelineCount() == 0
                self.set_status(self.warning if self.editable else "项目已有时间线，无法初始化")
        except Exception as exc:
            self.set_status("无法读取项目：" + str(exc), "error")
        finally:
            self.updating = False
            self.update_controls()
            self.update_template_summary()
            self.refresh_archive_setting()

    def update_controls(self):
        items = self.window.GetItems()
        can_edit = self.editable and not self.busy and not self.completed
        items["ComboRes"].Enabled = can_edit
        items["ComboFPS"].Enabled = can_edit
        items["BtnDeploy"].Enabled = (can_edit or self.completed) and not self.busy
        items["BtnRefresh"].Enabled = not self.busy
        items["BtnEditTemplate"].Enabled = not self.busy
        self.toggle_custom_inputs()

    def toggle_custom_inputs(self, ev=None):
        if self.updating:
            return
        items = self.window.GetItems()
        custom_res = items["ComboRes"].CurrentText == CUSTOM_LABEL
        if not custom_res:
            width, height = RES_OPTIONS.get(items["ComboRes"].CurrentText, ("", ""))
            items["CustomW"].Text = width
            items["CustomH"].Text = height
        enabled = self.editable and not self.busy and not self.completed
        items["CustomW"].Enabled = custom_res and enabled
        items["CustomH"].Enabled = custom_res and enabled

    def deploy(self, ev=None):
        if self.busy or self.template_open:
            return
        if self.completed:
            self.close()
            return
        items = self.window.GetItems()
        try:
            project = self.manager.GetCurrentProject()
            if project is None:
                self.refresh_project()
                return
            if project.GetUniqueId() != self.project_id:
                self.refresh_project()
                self.set_status("项目已切换，请确认设置后重试")
                return
            if project.GetTimelineCount() > 0:
                self.refresh_project()
                return
            if items["ComboRes"].CurrentText == CUSTOM_LABEL:
                width, height = items["CustomW"].Text.strip(), items["CustomH"].Text.strip()
            else:
                width, height = RES_OPTIONS[items["ComboRes"].CurrentText]
            fps = items["ComboFPS"].CurrentText.strip()
            self.busy = True
            items["BtnDeploy"].Text = "初始化中…"
            self.update_controls()
            result = deploy_project(project, self.config, width, height, fps)
            self.completed = True
            self.editable = False
            self.set_status("初始化完成 · 锁帧", "success")
            items["BtnDeploy"].Text = "开始剪辑"
            return result
        except Exception as exc:
            self.set_status(str(exc) or "初始化失败，请刷新后重试", "error")
        finally:
            self.busy = False
            if not self.completed:
                items["BtnDeploy"].Text = "初始化"
            self.update_controls()

    def show_template_editor(self, ev=None):
        if self.busy:
            return
        if self.template_open:
            dialog = self.discard_window if self.discard_window is not None and not self.template_window.Enabled else self.template_window
            dialog.Show()
            dialog.Raise()
            return
        if self.template_window is None:
            ui = self.ui
            self.template_window = self.dispatcher.AddWindow({
                "ID": "StartWorkTemplateWindow", "WindowTitle": "文件夹模板",
                "Geometry": [300, 180, 560, 520], "MinimumSize": [500, 440], "Margin": 20,
            }, ui.VGroup({"Spacing": 12}, [
                ui.Label({"Text": "文件夹路径", "Weight": 0,
                          "Font": ui.Font({"PixelSize": 13, "Bold": True})}),
                ui.TextEdit({"ID": "TemplatePaths", "Weight": 1, "AcceptRichText": False,
                             "ToolTip": "每行一个路径，使用 / 表示层级", "MinimumSize": [0, 240]}),
                ui.HGroup({"Weight": 0, "Spacing": 12}, [
                    self.label("锁帧位置"), ui.ComboBox({"ID": "ComboTimelineTarget", "MinimumSize": [0, 30]}),
                ]),
                ui.Label({"ID": "TemplateStatus", "Text": "", "WordWrap": True, "Weight": 0}),
                ui.HGroup({"Weight": 0, "Spacing": 10}, [
                    self.button("BtnTemplateRestore", "恢复默认"), ui.Label({"Text": "", "Weight": 1}),
                    self.button("BtnTemplateCancel", "关闭"), self.button("BtnTemplateSave", "保存", True),
                ]),
            ]))
            window = self.template_window
            window.On.TemplatePaths.TextChanged = self.refresh_template_targets
            window.On.ComboTimelineTarget.CurrentIndexChanged = self.template_target_changed
            window.On.BtnTemplateRestore.Clicked = self.restore_default_template
            window.On.BtnTemplateCancel.Clicked = self.request_close_template_editor
            window.On.BtnTemplateSave.Clicked = self.save_template_editor
            window.On.StartWorkTemplateWindow.Close = self.request_close_template_editor
        self.template_open = True
        self.window.Enabled = False
        self.fill_template(self.config)
        self.template_window.Enabled = True
        self.template_window.Show()
        self.template_window.Raise()

    def fill_template(self, config):
        self.template_updating = True
        try:
            self.template_target = config["timeline_target"]
            self.template_window.GetItems()["TemplatePaths"].PlainText = folder_paths_to_text(config["folders"])
        finally:
            self.template_updating = False
        self.refresh_template_targets()

    def refresh_template_targets(self, ev=None):
        if self.template_updating:
            return
        items = self.template_window.GetItems()
        combo = items["ComboTimelineTarget"]
        self.template_updating = True
        try:
            try:
                paths = expanded_folder_paths(parse_folder_paths(items["TemplatePaths"].PlainText))
            except ValueError:
                # 保留上次选择；路径输入暂时无效时不清空下拉框。
                combo.Enabled = False
            else:
                combo.Enabled = True
                combo.Clear()
                combo.AddItem(ROOT_TARGET_LABEL)
                for path in paths:
                    combo.AddItem(path)
                combo.CurrentIndex = paths.index(self.template_target) + 1 if self.template_target in paths else 0
        finally:
            self.template_updating = False
        self.update_template_editor_state()

    def template_target_changed(self, ev=None):
        if self.template_updating:
            return
        combo = self.template_window.GetItems()["ComboTimelineTarget"]
        self.template_target = combo.CurrentText if combo.CurrentIndex > 0 else ""
        self.update_template_editor_state()

    def update_template_editor_state(self, ev=None):
        if self.template_updating or self.template_window is None:
            return None
        items = self.template_window.GetItems()
        combo = items["ComboTimelineTarget"]
        target = combo.CurrentText if combo.CurrentIndex > 0 else ""
        state = evaluate_template_draft(items["TemplatePaths"].PlainText, target, self.config)
        items["BtnTemplateSave"].Enabled = bool(state["config"] is not None and (state["changed"] or self.warning))
        items["BtnTemplateRestore"].Enabled = not state["is_default"]
        status = items["TemplateStatus"]
        status.Text = state["error"] or ("未保存" if state["dirty"] else self.warning)
        if not state["error"] and state["dirty"] and self.template_target and not target:
            status.Text = "未保存 · 锁帧位置已改为媒体池根目录"
        status.StyleSheet = "color: #e39791;" if state["error"] else "color: #d6b678;"
        status.Visible = bool(status.Text)
        self.template_window.RecalcLayout()
        return state

    def restore_default_template(self, ev=None):
        self.fill_template(default_template_config())

    def save_template_editor(self, ev=None):
        state = self.update_template_editor_state()
        if state is None or state["config"] is None or not (state["changed"] or self.warning):
            return
        previous_warning = self.warning
        try:
            self.config = save_template_config(state["config"], self.config_path)
            self.warning = ""
        except (OSError, ValueError, TypeError) as exc:
            status = self.template_window.GetItems()["TemplateStatus"]
            status.Text = "保存失败：" + str(exc)
            status.StyleSheet = "color: #e39791;"
            status.Visible = True
            self.template_window.RecalcLayout()
            return
        self.update_template_summary()
        if previous_warning and self.window.GetItems()["StatusLabel"].Text == previous_warning:
            self.set_status()
        self.hide_template_editor()

    def request_close_template_editor(self, ev=None):
        state = self.update_template_editor_state()
        if state is not None and state["dirty"]:
            self.show_discard_message()
        else:
            self.hide_template_editor()

    def hide_template_editor(self):
        if self.discard_window is not None:
            self.discard_window.Hide()
        self.template_window.Hide()
        self.template_window.Enabled = True
        self.template_open = False
        self.window.Enabled = True
        self.window.Raise()

    def show_discard_message(self):
        if self.discard_window is None:
            ui = self.ui
            self.discard_window = self.dispatcher.AddWindow({
                "ID": "TemplateDiscardWindow", "WindowTitle": "放弃修改",
                "Geometry": [360, 300, 380, 150], "Margin": 20,
            }, ui.VGroup({"Spacing": 16}, [
                ui.Label({"Text": "放弃未保存的修改？", "WordWrap": True}),
                ui.HGroup({"Weight": 0, "Spacing": 10}, [
                    self.button("BtnContinue", "继续编辑"), self.button("BtnDiscard", "放弃修改"),
                ]),
            ]))
            self.discard_window.On.BtnContinue.Clicked = self.close_discard_message
            self.discard_window.On.BtnDiscard.Clicked = self.discard_template_changes
            self.discard_window.On.TemplateDiscardWindow.Close = self.close_discard_message
        self.template_window.Enabled = False
        self.discard_window.Show()
        self.discard_window.Raise()

    def close_discard_message(self, ev=None):
        self.discard_window.Hide()
        self.template_window.Enabled = True
        self.template_window.Raise()

    def discard_template_changes(self, ev=None):
        self.hide_template_editor()

    def close(self, ev=None):
        if self.busy:
            return
        if getattr(getattr(self, 'archive_job', None), 'action', None) == 'archive_execute':
            return
        if self.template_open:
            self.show_template_editor()
            return
        self.dispatcher.ExitLoop()

    def run(self):
        try:
            self.window.Show()
            self.dispatcher.RunLoop()
        finally:
            self.hide_archive_status()
            for window in (self.discard_window, self.template_window, self.window):
                if window is not None:
                    window.Hide()


def compatible_dispatcher(host_bmd, ui):
    class Dispatcher(host_bmd.UIDispatcher):
        def Dispatch(self, event):
            # Resolve 21 may queue window lifecycle events without a sender,
            # and events for previously closed script windows share the queue.
            # Its stock dispatcher indexes these fields before its error guard.
            if not isinstance(event, dict) or not isinstance(event.get('what'), str):
                return
            if 'window' in event:
                try:
                    handlers = self[event['window']]['On']
                    if not isinstance(event.get('who'), str):
                        if event['what'] not in handlers.get(event['window'], {}):
                            return
                        event = dict(event, who=event['window'])
                    if event['who'] not in handlers:
                        return
                except (KeyError, TypeError):
                    return
            return super().Dispatch(event)
    return Dispatcher(ui)


def main(host_app=None, host_bmd=None, expected_project_id=None):
    if host_bmd is None:
        import DaVinciResolveScript as host_bmd
    resolve = host_app.GetResolve() if host_app is not None else host_bmd.scriptapp("Resolve")
    if resolve is None:
        raise RuntimeError("请在 DaVinci Resolve 中运行此脚本")
    if expected_project_id:
        manager = resolve.GetProjectManager()
        project = manager.GetCurrentProject() if manager is not None else None
        if project is None or project.GetUniqueId() != expected_project_id:
            return
    fusion = resolve.Fusion()
    ui = fusion.UIManager
    if ui is None:
        raise RuntimeError("无法启动界面，请检查 Resolve Studio 的脚本支持")
    controller = StartWorkApp(resolve, ui, compatible_dispatcher(host_bmd, ui))
    controller.run()


if __name__ == "__main__" or globals().get("app") is not None:
    main(globals().get("app"), globals().get("bmd"), globals().get("OrdoExpectedProjectID"))

