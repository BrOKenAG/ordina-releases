# -*- coding: utf-8 -*-
"""Resolve 角度命名与多机位合板。导入模块不会连接或修改工程。"""

import importlib.util
from pathlib import Path
import sys


PLUGIN_VERSION = "v2.0"
SYNC_MODES = ("波形", "时间码")
DEFAULT_TARGET_PATH = "03_Timeline/01_合板及素材筛选"


def values(items):
    if items is None:
        return []
    if isinstance(items, dict):
        return list(items.values())
    return list(items)


def object_uid(obj):
    for method in ("GetUniqueId", "GetMediaId"):
        getter = getattr(obj, method, None)
        if callable(getter):
            uid = getter()
            if uid:
                return str(uid)
    return "object_" + str(id(obj))


def clip_uid(clip):
    return object_uid(clip)


def scan_folders(root):
    records, seen = [], set()
    stack = [(root, "媒体池", None)]
    while stack:
        folder, path, parent = stack.pop()
        uid = object_uid(folder)
        if uid in seen:
            continue
        seen.add(uid)
        records.append({"uid": uid, "folder": folder, "path": path, "parent": parent,
                        "count": len(values(folder.GetClipList()))})
        children = values(folder.GetSubFolderList())
        for child in reversed(children):
            stack.append((child, path + "/" + child.GetName(), uid))
    return records


def collect_clips(folder, recursive):
    clips, visited, stack = [], set(), [folder]
    while stack:
        current = stack.pop()
        uid = object_uid(current)
        if uid in visited:
            continue
        visited.add(uid)
        clips.extend(values(current.GetClipList()))
        if recursive:
            stack.extend(reversed(values(current.GetSubFolderList())))
    return clips


def source_properties(clip):
    getter = getattr(clip, "GetTimeline", None)
    if callable(getter) and getter() is not None:
        return None
    properties = clip.GetClipProperty()
    if not isinstance(properties, dict):
        raise RuntimeError("无法读取片段属性：" + clip.GetName())
    return properties if properties.get("File Path") else None


def gather_sources(folders, recursive, video_only=False):
    clips, seen, skipped = [], set(), 0
    for folder in folders:
        for clip in collect_clips(folder, recursive):
            uid = clip_uid(clip)
            if uid in seen:
                continue
            seen.add(uid)
            properties = source_properties(clip)
            if properties is None or (video_only and not properties.get("Video Codec")):
                skipped += 1
                continue
            if video_only and str(properties.get("Type", "")).lower() in ("still", "image", "静帧", "图像"):
                skipped += 1
                continue
            clips.append(clip)
    return clips, skipped


def read_angle(clip):
    metadata = clip.GetMetadata("Angle")
    if isinstance(metadata, dict):
        metadata = metadata.get("Angle", "")
    if metadata is None:
        raise RuntimeError("无法读取角度元数据")
    return str(metadata)


def set_angle(clip, value):
    try:
        accepted = clip.SetMetadata({"Angle": value})
    except TypeError:
        accepted = clip.SetMetadata("Angle", value)
    return bool(accepted) and read_angle(clip) == value


class AngleJob:
    """每次 step 处理少量片段，供 UI 定时器调用。"""
    def __init__(self, clips, value):
        self.operations = [{"clip": clip, "value": value} for clip in clips]
        self.index = 0
        self.success = self.unchanged = self.failed = 0
        self.errors = []
        self.changes = []
        self.cancelled = False
        self.done = not self.operations
        self.is_undo = False

    @classmethod
    def for_undo(cls, changes):
        job = cls([], "")
        job.operations = [{"clip": change["clip"], "value": change["before"], "expected": change["after"]}
                          for change in reversed(changes)]
        job.done = not job.operations
        job.is_undo = True
        return job

    def cancel(self):
        self.cancelled = self.done = True

    def step(self, limit=8):
        if self.done:
            return
        end = min(self.index + limit, len(self.operations))
        while self.index < end:
            operation = self.operations[self.index]
            clip, value = operation["clip"], operation["value"]
            before = None
            try:
                before = read_angle(clip)
                if before == value:
                    self.unchanged += 1
                elif "expected" in operation and before != operation["expected"]:
                    raise RuntimeError("角度已被再次修改，未覆盖")
                elif set_angle(clip, value):
                    self.success += 1
                else:
                    raise RuntimeError("写入被拒绝或读回不一致")
            except Exception as exc:
                self.failed += 1
                try:
                    name = clip.GetName()
                except Exception:
                    name = clip_uid(clip)
                self.errors.append(str(name) + "：" + str(exc))
            finally:
                if before is not None and before != value:
                    try:
                        if read_angle(clip) == value:
                            self.changes.append({"clip": clip, "before": before, "after": value})
                    except Exception:
                        # 写入后读回中断时仍保留快照；撤销会再次核对当前值。
                        self.changes.append({"clip": clip, "before": before, "after": value})
                self.index += 1
        self.done = self.index >= len(self.operations)


def supports_multicam(resolve):
    return tuple(resolve.GetVersion()[:2]) >= (21, 1)


def prepare_multicam(resolve, project, clips, name, sync_mode):
    if project is None:
        raise RuntimeError("请先打开项目")
    if not supports_multicam(resolve):
        raise RuntimeError("创建多机位需要 Resolve 21.1 或更高版本")
    name = str(name).strip()
    if not name or any(ord(char) < 32 for char in name):
        raise ValueError("请输入有效的多机位名称")
    if sync_mode not in SYNC_MODES:
        raise ValueError("请选择波形或时间码同步")
    unique = {}
    for clip in clips:
        unique.setdefault(clip_uid(clip), clip)
    clips = list(unique.values())
    if len(clips) < 2:
        raise RuntimeError("至少选择两个视频片段")
    angles = {}
    for clip in clips:
        properties = source_properties(clip)
        if properties is None or not properties.get("Video Codec"):
            raise RuntimeError("合板来源必须是原始视频片段")
        angle = read_angle(clip).strip()
        if not angle:
            raise RuntimeError("片段缺少角度，请先完成角度命名：" + clip.GetName())
        angles[angle] = angles.get(angle, 0) + 1
        if sync_mode == "波形":
            try:
                channels = int(properties.get("Audio Ch", 0))
            except (TypeError, ValueError):
                channels = 0
            if channels < 1:
                raise RuntimeError("波形同步需要每个视频都包含音频：" + clip.GetName())
    if len(angles) < 2:
        raise RuntimeError("至少需要两个不同角度，请先区分各机位的角度名称")
    options = {
        "name": name,
        "angleSyncMode": resolve.MULTICAM_ANGLE_SYNC_AUDIO if sync_mode == "波形" else resolve.MULTICAM_ANGLE_SYNC_TIMECODE,
        "angleNameMode": resolve.MULTICAM_ANGLE_NAME_ANGLE,
        "detectSameCameraClipsMode": resolve.MULTICAM_DETECT_BY_ANGLE,
        "multicamAudioMode": resolve.MULTICAM_AUDIO_SOURCE,
        "useFullClipExtents": True,
        "createBinForSourceClips": False,
        "splitAtGaps": False,
    }
    if sync_mode == "波形":
        options["channelConfig"] = resolve.AUDIO_SYNC_CHANNEL_AUTOMATIC
    return {"clips": clips, "options": options, "angles": angles, "project_id": project.GetUniqueId()}


def create_multicam(resolve, project, clips, name, sync_mode, target):
    plan = prepare_multicam(resolve, project, clips, name, sync_mode)
    pool = project.GetMediaPool()
    if pool is None or target is None:
        raise RuntimeError("无法读取保存位置，请刷新后重试")
    if any(clip.GetName() == plan["options"]["name"] for clip in values(target.GetClipList())):
        raise RuntimeError("保存位置已有同名片段，请更换名称")
    previous_folder = pool.GetCurrentFolder()
    if previous_folder is None:
        raise RuntimeError("无法读取媒体池当前位置")
    warning, created, failure = "", [], None
    try:
        if not pool.SetCurrentFolder(target):
            raise RuntimeError("无法切换到保存位置")
        created = values(pool.CreateMulticamClip(plan["clips"], plan["options"]))
        if not created:
            raise RuntimeError("创建多机位失败，请检查保存位置后重试")
    except Exception as exc:
        failure = exc
    finally:
        try:
            if not pool.SetCurrentFolder(previous_folder):
                warning = "媒体池位置未恢复，请手动切换"
        except Exception:
            warning = "媒体池位置未恢复，请手动切换"
    if failure is not None:
        message = str(failure) or "创建多机位失败，请检查保存位置后重试"
        if warning:
            message += "\n" + warning
        raise RuntimeError(message) from failure
    return {"created": created, "warning": warning}


class AngleApp:
    def __init__(self, resolve, ui, dispatcher):
        self.resolve, self.ui, self.dispatcher = resolve, ui, dispatcher
        self.manager = resolve.GetProjectManager()
        if self.manager is None:
            raise RuntimeError("无法连接项目管理器")
        self.project = self.project_id = None
        self.records, self.nodes = {}, {}
        self.node_ids = {}
        self.sources = []
        self.selection_key = None
        self.building = False
        self.job = None
        self.job_project_id = None
        self.undo_changes, self.undo_project_id = [], None
        self.report = ""
        self.details_window = self.multicam_window = None
        self.multicam_open = self.multicam_busy = self.multicam_done = False
        self.multicam_folder_ids = []
        self.multicam_signature = None
        self.window = self.build_window()
        self.timer = ui.Timer({"ID": "AngleBatchTimer", "Interval": 20, "SingleShot": False})
        self.dispatcher.On.Timeout = self.tick
        self.refresh()

    def button(self, identity, text, primary=False):
        props = {"ID": identity, "Text": text, "Weight": 0, "MinimumSize": [86, 32]}
        if primary:
            props["StyleSheet"] = (
                "QPushButton {background: #3677a8; color: #fff; border: 1px solid #4885b2;"
                "border-radius: 4px; padding: 7px 16px;}"
                "QPushButton:hover {background: #4085b8;}"
                "QPushButton:focus {border: 1px solid #b1d5f5;}"
                "QPushButton:disabled {background: #33383e; color: #7f858c; border-color: #42474e;}"
            )
        return self.ui.Button(props)

    def build_window(self):
        ui = self.ui
        window = self.dispatcher.AddWindow({
            "ID": "AngleWin", "WindowTitle": "Multicam Tools · " + PLUGIN_VERSION,
            "Geometry": [220, 120, 610, 600], "MinimumSize": [560, 540], "Margin": 18,
        }, ui.VGroup({"Spacing": 10}, [
            ui.HGroup({"Weight": 0, "Spacing": 10}, [
                ui.Label({"ID": "ProjectName", "Text": "", "WordWrap": True,
                          "Font": ui.Font({"PixelSize": 15, "Bold": True})}),
                self.button("RefreshBtn", "刷新"),
            ]),
            ui.Tree({"ID": "FolderTree", "ColumnCount": 2, "HeaderHidden": False,
                     "SelectionMode": "ExtendedSelection", "AlternatingRowColors": True,
                     "UniformRowHeights": True, "Weight": 1, "MinimumSize": [0, 240],
                     "ToolTip": "Cmd / Ctrl 多选，Shift 连选"}),
            ui.Label({"ID": "SelLabel", "Text": "未选择文件夹", "Weight": 0,
                      "WordWrap": False, "StyleSheet": "color: #9ca2aa;"}),
            ui.HGroup({"Weight": 0, "Spacing": 12}, [
                ui.Label({"Text": "角度", "Weight": 0, "MinimumSize": [56, 30]}),
                ui.LineEdit({"ID": "AngleEdit", "MinimumSize": [0, 30]}),
            ]),
            ui.CheckBox({"ID": "RecursiveCheck", "Text": "包含子文件夹", "Checked": True, "Weight": 0}),
            ui.HGroup({"Weight": 0, "Spacing": 8}, [
                ui.Label({"ID": "StatusLabel", "Text": "", "WordWrap": True, "Weight": 1}),
                self.button("DetailsBtn", "详情"),
            ]),
            ui.HGroup({"Weight": 0, "Spacing": 8}, [
                self.button("CloseBtn", "关闭"), self.button("UndoBtn", "撤销"),
                ui.Label({"Text": "", "Weight": 1}), self.button("ApplyBtn", "写入角度", True),
            ]),
            self.button("MulticamBtn", "创建多机位…"),
        ]))
        window.On.FolderTree.ItemSelectionChanged = self.on_selection
        window.On.FolderTree.ItemClicked = self.on_selection
        window.On.FolderTree.CurrentChanged = self.on_selection
        window.On.AngleEdit.TextChanged = self.on_input_changed
        window.On.RecursiveCheck.Toggled = self.on_scope_changed
        window.On.RefreshBtn.Clicked = self.refresh
        window.On.ApplyBtn.Clicked = self.apply
        window.On.UndoBtn.Clicked = self.undo
        window.On.DetailsBtn.Clicked = self.show_details
        window.On.MulticamBtn.Clicked = self.show_multicam
        window.On.CloseBtn.Clicked = self.close
        window.On.AngleWin.Close = self.close
        window.GetItems()["UndoBtn"].ToolTip = "撤销上次写入"
        return window

    def status(self, text="", kind="normal"):
        label = self.window.GetItems()["StatusLabel"]
        colors = {"normal": "#9ca2aa", "error": "#e39791", "success": "#9bc5a5", "warning": "#d6b678"}
        label.Text = text
        label.ToolTip = text
        label.StyleSheet = "color: " + colors.get(kind, colors["normal"]) + ";"
        self.window.RecalcLayout()

    def selected_records(self):
        tree = self.window.GetItems()["FolderTree"]
        try:
            selected = values(tree.SelectedItems())
        except Exception:
            selected = [node for node in self.nodes.values() if node.Selected]
        result, seen = [], set()
        for node in selected:
            uid = self.node_ids.get(str(node))
            if uid in self.records and uid not in seen:
                result.append(self.records[uid])
                seen.add(uid)
        return result

    def refresh(self, ev=None):
        if self.job is not None or self.multicam_open:
            return
        previous_ids = {record["uid"] for record in self.selected_records()}
        previous_project = self.project_id
        items, self.building = self.window.GetItems(), True
        tree = items["FolderTree"]
        try:
            self.project = self.manager.GetCurrentProject()
            self.project_id = self.project.GetUniqueId() if self.project is not None else None
            if self.project_id != previous_project:
                previous_ids.clear()
            tree.Clear()
            tree.SetHeaderLabels(["文件夹", "片段"])
            tree.ColumnWidth[0], tree.ColumnWidth[1] = 430, 80
            self.records, self.nodes = {}, {}
            self.node_ids = {}
            if self.project is None:
                items["ProjectName"].Text = "未打开项目"
                self.status("请打开项目后刷新", "warning")
            else:
                items["ProjectName"].Text = self.project.GetName()
                pool = self.project.GetMediaPool()
                if pool is None or pool.GetRootFolder() is None:
                    raise RuntimeError("无法读取媒体池")
                for record in scan_folders(pool.GetRootFolder()):
                    uid = record["uid"]
                    node = tree.NewItem()
                    node.Text[0] = "媒体池" if record["parent"] is None else record["folder"].GetName()
                    node.Text[1] = str(record["count"])
                    node.ToolTip[0] = record["path"]
                    self.records[uid], self.nodes[uid] = record, node
                    # Fusion 的自定义 Data 在刷新/显示后可能丢失；代理字符串包含稳定的原生节点地址。
                    self.node_ids[str(node)] = uid
                    if record["parent"] is None:
                        tree.AddTopLevelItem(node)
                    else:
                        self.nodes[record["parent"]].AddChild(node)
                    node.Selected = uid in previous_ids
                for uid, node in self.nodes.items():
                    node.Expanded = self.records[uid]["path"].count("/") < 2
                self.status()
        except Exception as exc:
            tree.Clear()
            self.records, self.nodes = {}, {}
            self.node_ids = {}
            self.status("刷新失败：" + str(exc), "error")
        finally:
            self.building = False
            self.selection_key = None
            self.on_selection()

    def on_selection(self, ev=None):
        if self.building or self.job is not None:
            return
        items = self.window.GetItems()
        records = self.selected_records()
        recursive = bool(items["RecursiveCheck"].Checked)
        key = (tuple(record["uid"] for record in records), recursive)
        if key == self.selection_key:
            return
        self.selection_key = key
        self.sources = []
        try:
            self.sources, skipped = gather_sources([record["folder"] for record in records], recursive)
            items["SelLabel"].Text = ("%d 个文件夹 · %d 个片段" % (len(records), len(self.sources))) if records else "未选择文件夹"
            paths = [record["path"] for record in records]
            items["SelLabel"].ToolTip = "\n".join(paths) + ("\n已跳过 %d 个时间线或生成片段" % skipped if skipped else "")
        except Exception as exc:
            self.status("读取失败：" + str(exc), "error")
            items["SelLabel"].Text = "无法读取所选片段"
        self.update_actions()

    def on_scope_changed(self, ev=None):
        self.selection_key = None
        self.on_selection()

    def on_input_changed(self, ev=None):
        self.update_actions()

    def update_actions(self):
        items = self.window.GetItems()
        busy = self.job is not None
        for name in ("FolderTree", "AngleEdit", "RecursiveCheck", "RefreshBtn"):
            items[name].Enabled = not busy
        items["ApplyBtn"].Text = "停止" if busy else "写入角度"
        items["ApplyBtn"].Enabled = busy or bool(self.project and self.sources and items["AngleEdit"].Text.strip())
        items["UndoBtn"].Enabled = not busy and bool(self.undo_changes) and self.undo_project_id == self.project_id
        items["DetailsBtn"].Enabled = bool(self.report)
        supported = supports_multicam(self.resolve)
        items["MulticamBtn"].Enabled = not busy and supported and len(self.sources) >= 2
        items["MulticamBtn"].ToolTip = "" if supported else "需要 Resolve 21.1 或更高版本"

    def check_project(self):
        current = self.manager.GetCurrentProject()
        if current is None or current.GetUniqueId() != self.project_id:
            self.refresh()
            self.status("项目已切换，请重新选择文件夹", "warning")
            return False
        return True

    def apply(self, ev=None):
        if self.job is not None:
            self.job.cancel()
            return
        if self.multicam_open:
            return
        try:
            if not self.check_project():
                return
            items = self.window.GetItems()
            value = items["AngleEdit"].Text.strip()
            if not value or any(ord(char) < 32 for char in value):
                raise ValueError("请输入有效的角度名称")
            folders = [record["folder"] for record in self.selected_records()]
            if not folders:
                raise ValueError("请选择文件夹")
            clips, skipped = gather_sources(folders, bool(items["RecursiveCheck"].Checked))
            if not clips:
                raise ValueError("所选范围没有可写入的片段")
            self.start_job(AngleJob(clips, value))
        except Exception as exc:
            self.status(str(exc), "error")

    def start_job(self, job):
        self.job, self.job_project_id = job, self.project_id
        self.report = ""
        self.status("准备撤销…" if job.is_undo else "准备写入…")
        self.update_actions()
        self.timer.Start()

    def undo(self, ev=None):
        if self.job is not None or self.multicam_open:
            return
        try:
            if not self.check_project():
                return
            if not self.undo_changes or self.undo_project_id != self.project_id:
                return
            self.start_job(AngleJob.for_undo(self.undo_changes))
        except Exception as exc:
            self.status(str(exc), "error")

    def tick(self, ev=None):
        if self.job is None:
            return
        changed_project = False
        try:
            current = self.manager.GetCurrentProject()
            changed_project = current is None or current.GetUniqueId() != self.job_project_id
            if changed_project:
                self.job.cancel()
            self.job.step(8)
            if not self.job.done:
                self.status(("撤销" if self.job.is_undo else "写入") + " %d / %d" % (self.job.index, len(self.job.operations)))
                return
        except Exception as exc:
            self.job.cancel()
            self.job.errors.append(str(exc))
            self.job.failed += 1
        self.finish_job()
        if changed_project:
            self.refresh()
            self.status("项目已切换，操作已停止", "warning")

    def finish_job(self):
        job = self.job
        self.timer.Stop()
        if job is None:
            return
        if job.is_undo:
            remaining = []
            for change in self.undo_changes:
                try:
                    if read_angle(change["clip"]) != change["before"]:
                        remaining.append(change)
                except Exception:
                    remaining.append(change)
            self.undo_changes = remaining
        elif job.changes:
            self.undo_changes, self.undo_project_id = job.changes, self.job_project_id
        action = "已撤销" if job.is_undo else "已写入"
        summary = "%s %d · 未变 %d · 失败 %d" % (action, job.success, job.unchanged, job.failed)
        if job.cancelled:
            summary = "已停止 · " + summary
        self.report = summary + ("\n\n" + "\n".join(job.errors) if job.errors else "")
        self.job = None
        self.status(summary, "error" if job.failed else "warning" if job.cancelled else "success")
        self.update_actions()

    def show_details(self, ev=None):
        if not self.report:
            return
        if self.details_window is None:
            self.details_window = self.dispatcher.AddWindow({
                "ID": "AngleDetails", "WindowTitle": "执行结果", "Geometry": [280, 180, 580, 360], "Margin": 16,
            }, self.ui.VGroup({"Spacing": 10}, [
                self.ui.TextEdit({"ID": "ReportText", "ReadOnly": True, "AcceptRichText": False}),
                self.button("CloseDetails", "关闭"),
            ]))
            self.details_window.On.CloseDetails.Clicked = lambda ev: self.details_window.Hide()
            self.details_window.On.AngleDetails.Close = lambda ev: self.details_window.Hide()
        self.details_window.GetItems()["ReportText"].PlainText = self.report
        self.details_window.Show()
        self.details_window.Raise()

    def show_multicam(self, ev=None):
        if self.job is not None:
            return
        if self.multicam_open:
            self.multicam_window.Raise()
            return
        try:
            if not self.check_project():
                return
            if not supports_multicam(self.resolve):
                raise RuntimeError("创建多机位需要 Resolve 21.1 或更高版本")
            records = self.selected_records()
            if not records:
                raise ValueError("请选择文件夹")
            self.multicam_folder_ids = [record["uid"] for record in records]
            self.multicam_recursive = bool(self.window.GetItems()["RecursiveCheck"].Checked)
            if self.multicam_window is None:
                self.build_multicam_window()
            items = self.multicam_window.GetItems()
            self.multicam_done = self.multicam_busy = False
            self.multicam_open = True
            self.window.Enabled = False
            self.multicam_signature = None
            items["MulticamName"].Text = records[0]["folder"].GetName() + "_合板"
            self.target_records = list(self.records.values())
            combo = items["TargetFolder"]
            combo.Clear()
            target_index = 0
            current = self.project.GetMediaPool().GetCurrentFolder()
            for index, record in enumerate(self.target_records):
                combo.AddItem(record["path"])
                if current is not None and record["uid"] == object_uid(current):
                    target_index = index
            for index, record in enumerate(self.target_records):
                if record["path"] == "媒体池/" + DEFAULT_TARGET_PATH:
                    target_index = index
                    break
            combo.CurrentIndex = target_index
            self.update_multicam_preview()
            self.multicam_window.Show()
            self.multicam_window.Raise()
        except Exception as exc:
            self.multicam_open = False
            self.window.Enabled = True
            self.status(str(exc), "error")

    def build_multicam_window(self):
        ui = self.ui
        self.multicam_window = self.dispatcher.AddWindow({
            "ID": "AngleMulticam", "WindowTitle": "创建多机位", "Geometry": [280, 180, 540, 340],
            "MinimumSize": [500, 320], "Margin": 20,
        }, ui.VGroup({"Spacing": 12}, [
            ui.Label({"ID": "SourceSummary", "Text": "", "Weight": 0,
                      "Font": ui.Font({"PixelSize": 14, "Bold": True})}),
            ui.Label({"ID": "AngleSummary", "Text": "", "WordWrap": True, "Weight": 0}),
            ui.HGroup({"Weight": 0, "Spacing": 12}, [
                ui.Label({"Text": "名称", "Weight": 0, "MinimumSize": [60, 30]}),
                ui.LineEdit({"ID": "MulticamName", "MinimumSize": [0, 30]}),
            ]),
            ui.HGroup({"Weight": 0, "Spacing": 12}, [
                ui.Label({"Text": "同步方式", "Weight": 0, "MinimumSize": [60, 30]}),
                ui.ComboBox({"ID": "SyncMode", "MinimumSize": [0, 30]}),
            ]),
            ui.HGroup({"Weight": 0, "Spacing": 12}, [
                ui.Label({"Text": "保存位置", "Weight": 0, "MinimumSize": [60, 30]}),
                ui.ComboBox({"ID": "TargetFolder", "MinimumSize": [0, 30]}),
            ]),
            ui.Label({"ID": "MulticamStatus", "Text": "", "WordWrap": True, "Weight": 0}),
            ui.HGroup({"Weight": 0, "Spacing": 10}, [
                self.button("CloseMulticam", "关闭"), ui.Label({"Text": "", "Weight": 1}),
                self.button("CreateMulticam", "创建", True),
            ]),
        ]))
        for mode in SYNC_MODES:
            self.multicam_window.GetItems()["SyncMode"].AddItem(mode)
        self.multicam_window.On.SyncMode.CurrentIndexChanged = self.update_multicam_preview
        self.multicam_window.On.MulticamName.TextChanged = self.update_multicam_preview
        self.multicam_window.On.CreateMulticam.Clicked = self.run_multicam
        self.multicam_window.On.CloseMulticam.Clicked = self.close_multicam
        self.multicam_window.On.AngleMulticam.Close = self.close_multicam

    def multicam_sources(self):
        folders = [self.records[uid]["folder"] for uid in self.multicam_folder_ids if uid in self.records]
        return gather_sources(folders, self.multicam_recursive, video_only=True)

    def update_multicam_preview(self, ev=None):
        if not self.multicam_open or self.multicam_busy or self.multicam_done:
            return
        items = self.multicam_window.GetItems()
        error = ""
        try:
            clips, skipped = self.multicam_sources()
            angles = {}
            signature = []
            for clip in clips:
                angle = read_angle(clip)
                signature.append((clip_uid(clip), angle))
                angles[angle] = angles.get(angle, 0) + 1
            self.multicam_signature = tuple(sorted(signature))
            items["SourceSummary"].Text = "%d 个视频 · %d 个角度" % (len(clips), len([angle for angle in angles if angle.strip()]))
            items["AngleSummary"].Text = " · ".join("%s %d" % (angle or "未命名", count) for angle, count in angles.items())
            items["SourceSummary"].ToolTip = "已跳过 %d 个非视频或时间线片段" % skipped
            prepare_multicam(self.resolve, self.project, clips, items["MulticamName"].Text, items["SyncMode"].CurrentText)
        except Exception as exc:
            error = str(exc)
        items["MulticamStatus"].Text = error
        items["MulticamStatus"].StyleSheet = "color: #d6b678;"
        for name in ("MulticamName", "SyncMode", "TargetFolder"):
            items[name].Enabled = True
        items["CreateMulticam"].Text = "创建"
        items["CreateMulticam"].Enabled = not error
        self.multicam_window.RecalcLayout()

    def run_multicam(self, ev=None):
        if self.multicam_busy:
            return
        if self.multicam_done:
            self.close_multicam()
            return
        items = self.multicam_window.GetItems()
        try:
            current = self.manager.GetCurrentProject()
            if current is None or current.GetUniqueId() != self.project_id:
                raise RuntimeError("项目已切换，请关闭此窗口并刷新")
            clips, skipped = self.multicam_sources()
            signature = tuple(sorted((clip_uid(clip), read_angle(clip)) for clip in clips))
            if signature != self.multicam_signature:
                self.update_multicam_preview()
                raise RuntimeError("来源或角度已变化，请确认后重试")
            target_index = items["TargetFolder"].CurrentIndex
            if not 0 <= target_index < len(self.target_records):
                raise RuntimeError("请选择保存位置")
            self.multicam_busy = True
            for name in ("MulticamName", "SyncMode", "TargetFolder", "CreateMulticam", "CloseMulticam"):
                items[name].Enabled = False
            items["MulticamStatus"].Text = "正在创建…"
            self.multicam_window.RecalcLayout()
            target = self.target_records[target_index]["folder"]
            result = create_multicam(self.resolve, self.project, clips, items["MulticamName"].Text, items["SyncMode"].CurrentText, target)
            self.multicam_done = True
            summary = "已创建 %d 个多机位片段" % len(result["created"])
            self.status(summary, "success")
            items["MulticamStatus"].Text = summary + ("\n" + result["warning"] if result["warning"] else "")
            items["MulticamStatus"].StyleSheet = "color: #d6b678;" if result["warning"] else "color: #9bc5a5;"
            items["CreateMulticam"].Text = "完成"
        except Exception as exc:
            items["MulticamStatus"].Text = str(exc)
            items["MulticamStatus"].StyleSheet = "color: #e39791;"
            for name in ("MulticamName", "SyncMode", "TargetFolder"):
                items[name].Enabled = True
        finally:
            self.multicam_busy = False
            items["CreateMulticam"].Enabled = True
            items["CloseMulticam"].Enabled = True
            self.multicam_window.RecalcLayout()

    def close_multicam(self, ev=None):
        if self.multicam_busy:
            return
        self.multicam_window.Hide()
        self.multicam_open = False
        self.window.Enabled = True
        self.window.Raise()
        self.refresh()

    def close(self, ev=None):
        if self.multicam_open:
            self.multicam_window.Raise()
            return
        if self.job is not None:
            self.job.cancel()
            self.finish_job()
        self.dispatcher.ExitLoop()

    def run(self):
        try:
            self.window.Show()
            self.dispatcher.RunLoop()
        finally:
            self.timer.Stop()
            for window in (self.multicam_window, self.details_window, self.window):
                if window is not None:
                    window.Hide()


def _load_resolve(namespace=None):
    namespace = globals() if namespace is None else namespace
    host_bmd = namespace.get("bmd")
    host_resolve = namespace.get("resolve")
    host_app = namespace.get("app")
    if host_resolve is None and host_app is not None:
        host_resolve = host_app.GetResolve()
    if host_bmd is None:
        try:
            import DaVinciResolveScript as host_bmd
        except ImportError:
            if sys.platform == "darwin":
                module_path = Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules/DaVinciResolveScript.py")
            elif sys.platform.startswith("win"):
                module_path = Path("C:/ProgramData/Blackmagic Design/DaVinci Resolve/Support/Developer/Scripting/Modules/DaVinciResolveScript.py")
            else:
                module_path = Path("/opt/resolve/Developer/Scripting/Modules/DaVinciResolveScript.py")
            spec = importlib.util.spec_from_file_location("DaVinciResolveScript", module_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules["DaVinciResolveScript"] = module
            spec.loader.exec_module(module)
            host_bmd = sys.modules["DaVinciResolveScript"]
    if host_resolve is None:
        host_resolve = host_bmd.scriptapp("Resolve")
    if host_resolve is None:
        raise RuntimeError("请在 DaVinci Resolve 中运行此脚本")
    return host_resolve, host_bmd


def main(namespace=None):
    resolve, host_bmd = _load_resolve(namespace)
    fusion = resolve.Fusion()
    ui = fusion.UIManager
    if ui is None:
        raise RuntimeError("无法启动脚本界面，请检查 Resolve Studio 的脚本支持")
    AngleApp(resolve, ui, host_bmd.UIDispatcher(ui)).run()


if __name__ == "__main__" or globals().get("app") is not None:
    main(globals())
