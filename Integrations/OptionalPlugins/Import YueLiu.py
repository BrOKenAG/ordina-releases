# -*- coding: utf-8 -*-
"""Import YueLiu review comments, filter and track completed changes. Importing this module does not access Resolve."""

import copy
import datetime
from fractions import Fraction
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys


PLUGIN_VERSION = "v2.3"
WINDOW_ID = "com.13rOKen4nGeL.resolve.ImportYueliu"
MARKER_NAME = "阅流批注"
DATA_PREFIX = "YueliuReview:2:"
MARKER_FIELDS = ("color", "name", "note", "duration", "customData")
AUTHOR_COL_CANDIDATES = ("评论人", "评论作者", "评论者", "作者", "用户名", "用户", "昵称", "Reviewer", "Author")
STATUS_COL_CANDIDATES = ("处理状态", "评论状态", "批注状态", "意见状态", "状态", "Status")
AUTHOR_COLORS = ("Cyan", "Blue", "Purple", "Pink", "Sky", "Mint", "Lemon", "Sand", "Fuchsia", "Rose", "Lavender", "Cocoa", "Cream", "Red")


def fps_spec(value):
    value = re.sub(r"\s*DF\s*$", "", str(value).strip(), flags=re.I)
    number = float(value)
    if not math.isfinite(number) or not 1 <= number <= 240:
        raise ValueError("无法识别时间线帧率")
    rate = Fraction(value)
    for rounded, exact in ((23.976, Fraction(24000, 1001)), (29.97, Fraction(30000, 1001)),
                           (59.94, Fraction(60000, 1001)), (119.88, Fraction(120000, 1001))):
        if abs(number - rounded) < 0.001:
            rate = exact
            break
    nominal = int(number + 0.5)
    drop = 2 if abs(number - 29.97) < 0.001 else 4 if abs(number - 59.94) < 0.001 else 0
    return rate, nominal, drop


def timecode_to_frames(timecode, framerate, drop_frame=None):
    """四段为 SMPTE 帧标签；三段/小数/Excel 时间为经过时长。无效返回 -1。"""
    try:
        rate, nominal, drop = fps_spec(framerate)
        if isinstance(timecode, datetime.time):
            seconds = Fraction(timecode.hour * 3600 + timecode.minute * 60 + timecode.second) + Fraction(timecode.microsecond, 1000000)
            return int(seconds * rate + Fraction(1, 2))
        if isinstance(timecode, datetime.timedelta):
            seconds = Fraction(str(timecode.total_seconds()))
            return int(seconds * rate + Fraction(1, 2)) if seconds >= 0 else -1
        if timecode == 0 and not isinstance(timecode, bool):
            return 0
        text = str(timecode).strip()
        match = re.fullmatch(r"(\d+):([0-5]\d):([0-5]\d)([:;])(\d{1,3})", text)
        if match:
            hours, minutes, seconds, separator, frames = match.groups()
            hours, minutes, seconds, frames = map(int, (hours, minutes, seconds, frames))
            if frames >= nominal:
                return -1
            is_drop = separator == ";" or drop_frame is True
            total = (hours * 3600 + minutes * 60 + seconds) * nominal + frames
            if is_drop:
                if not drop or (minutes % 10 and seconds == 0 and frames < drop):
                    return -1
                total_minutes = hours * 60 + minutes
                total -= drop * (total_minutes - total_minutes // 10)
            return total
        match = re.fullmatch(r"(?:(\d+):)?([0-5]\d):([0-5]\d(?:[.,]\d+)?)", text)
        if match:
            hours, minutes, seconds = match.groups()
            elapsed = int(hours or 0) * 3600 + int(minutes) * 60 + Fraction(seconds.replace(",", "."))
            return int(elapsed * rate + Fraction(1, 2))
    except (ValueError, TypeError, OverflowError, ZeroDivisionError):
        pass
    return -1


def frames_to_timecode(frame, framerate, drop_frame=False):
    _, nominal, drop = fps_spec(framerate)
    frame = int(frame)
    if frame < 0:
        raise ValueError("标记位置不能为负")
    if drop_frame:
        if not drop:
            raise ValueError("该帧率不支持丢帧时间码")
        ten_minutes = nominal * 600 - drop * 9
        minute = nominal * 60 - drop
        blocks, remainder = divmod(frame, ten_minutes)
        frame += drop * 9 * blocks
        if remainder >= drop:
            frame += drop * ((remainder - drop) // minute)
    seconds, ff = divmod(frame, nominal)
    minutes, ss = divmod(seconds, 60)
    hh, mm = divmod(minutes, 60)
    return "%02d:%02d:%02d%s%02d" % (hh, mm, ss, ";" if drop_frame else ":", ff)


def marker_frame_for_timecode(tc_frames, mode, start_tc_frames):
    return tc_frames - start_tc_frames if mode == "absolute" else tc_frames


def pick_timecode_mode(tc_frames_list, duration_frames, start_tc_frames, offset=0):
    def hits(mode):
        return sum(0 <= marker_frame_for_timecode(frame, mode, start_tc_frames) + offset < duration_frames
                   for frame in tc_frames_list if frame >= 0)
    relative, absolute = hits("relative"), hits("absolute")
    return ("absolute", absolute, relative) if absolute > relative else ("relative", relative, absolute)


def plain_text(value):
    return "" if value is None else str(value).strip()


def read_review_workbook(path):
    try:
        import openpyxl
    except ImportError as exc:
        raise RuntimeError("缺少 openpyxl，请在 Resolve 使用的 Python 环境中安装后重试") from exc
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    tables = []
    try:
        for sheet in workbook.worksheets:
            sheet.reset_dimensions()
            columns, records = None, []
            for row_number, cells in enumerate(sheet.iter_rows(values_only=True), 1):
                labels = [re.sub(r"\s+", "", plain_text(cell)).casefold() for cell in cells]
                def index_for(names):
                    return next((labels.index(name.casefold()) for name in names if name.casefold() in labels), None)
                tc_index = index_for(("画面时间戳", "时间码", "时间戳", "Timecode", "Timestamp"))
                comment_index = index_for(("评论内容", "批注内容", "意见内容", "评论", "Comment", "Notes"))
                if tc_index is not None and comment_index is not None:
                    columns = {"tc": tc_index, "comment": comment_index,
                               "author": index_for(AUTHOR_COL_CANDIDATES), "source_status": index_for(STATUS_COL_CANDIDATES)}
                    continue
                if columns is None:
                    continue
                def cell(key):
                    index = columns[key]
                    return cells[index] if index is not None and index < len(cells) else None
                comment = plain_text(cell("comment"))
                if not comment:
                    continue
                records.append({"tc": cell("tc"), "comment": comment, "author": plain_text(cell("author")),
                                "source_status": plain_text(cell("source_status")), "row": row_number, "sheet": sheet.title})
            if columns is not None:
                tables.append({"name": sheet.title, "rows": records,
                               "has_author": columns["author"] is not None, "has_status": columns["source_status"] is not None})
        if not tables:
            raise ValueError("未找到时间戳和评论内容表头，请选择阅流导出的 XLSX")
        return {"sheets": tables}
    finally:
        workbook.close()


def timeline_context(timeline):
    if timeline is None:
        raise RuntimeError("请先打开时间线")
    getter = getattr(timeline, "GetSettings", None)
    settings = getter() if callable(getter) else None
    if not isinstance(settings, dict):
        settings = {key: timeline.GetSetting(key) for key in ("timelineFrameRate", "timelineDropFrameTimecode")}
    fps = float(re.sub(r"\s*DF\s*$", "", str(settings.get("timelineFrameRate", "")), flags=re.I))
    fps_spec(fps)
    drop = str(settings.get("timelineDropFrameTimecode", "0")).lower() in ("1", "true")
    start, end = int(timeline.GetStartFrame()), int(timeline.GetEndFrame())
    start_tc = timeline.GetStartTimecode()
    start_tc_frames = timecode_to_frames(start_tc, fps, drop)
    if start_tc_frames < 0:
        raise RuntimeError("无法识别时间线起始时间码，请检查时间线设置")
    result = {"id": timeline.GetUniqueId(), "name": timeline.GetName(), "fps": fps, "drop": drop,
              "start": start, "end": end, "duration": max(0, end - start), "start_tc": start_tc, "tc_start": start_tc_frames}
    result["signature"] = (result["id"], fps, drop, start, end, start_tc)
    return result


def entry_for_row(row):
    raw = [plain_text(row.get("tc")), plain_text(row.get("author")), plain_text(row.get("comment"))]
    identity = hashlib.sha256(json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"id": identity, "tc": raw[0], "author": raw[1], "comment": raw[2],
            "source_status": plain_text(row.get("source_status")), "local_state": None}


def effective_status(entry):
    if entry.get("local_state") == "done":
        return "已修改"
    if entry.get("local_state") == "deferred":
        return "暂不修改"
    if entry.get("local_state") == "open":
        return "待修改"
    return entry.get("source_status") or "待修改"


def marker_snapshot(marker):
    if marker is None:
        return None
    return {key: marker.get(key, "" if key != "duration" else 1) for key in MARKER_FIELDS}


def owned_entries(marker):
    if not isinstance(marker, dict):
        return None
    custom = marker.get("customData", "")
    if not isinstance(custom, str) or not custom.startswith(DATA_PREFIX):
        return None
    try:
        data = json.loads(custom[len(DATA_PREFIX):])
        entries = data["entries"]
        if data.get("version") != 2 or not isinstance(entries, list) or not entries:
            return None
        for entry in entries:
            if not isinstance(entry, dict) or not all(isinstance(entry.get(key), str) for key in ("id", "tc", "author", "comment", "source_status")):
                return None
            if entry.get("local_state") not in (None, "open", "done", "deferred"):
                return None
        return copy.deepcopy(entries)
    except (ValueError, KeyError, TypeError):
        return None


def marker_for_entries(entries):
    completed = sum(effective_status(entry) == "已修改" for entry in entries)
    deferred = sum(effective_status(entry) == "暂不修改" for entry in entries)
    if deferred == len(entries):
        color, name = "Yellow", MARKER_NAME + " · 暂不修改"
    elif completed == len(entries):
        color, name = "Green", MARKER_NAME + " · 已修改"
    elif completed or deferred:
        color, name = "Yellow", MARKER_NAME + (" · 部分完成" if completed else " · 部分搁置")
    else:
        author = entries[0]["author"]
        color = entries[0].get("open_color") or AUTHOR_COLORS[int(hashlib.sha256(author.encode("utf-8")).hexdigest()[:8], 16) % len(AUTHOR_COLORS)]
        name = MARKER_NAME
    note = "\n\n".join("【%s · %s】%s" % (entry["author"] or "未署名", effective_status(entry), entry["comment"]) for entry in entries)
    custom = DATA_PREFIX + json.dumps({"version": 2, "entries": entries}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return dict(color=color, name=name, note=note, duration=1, customData=custom)


def marker_is_edited(marker, entries):
    expected = marker_for_entries(entries)
    return marker.get("name") != expected["name"] or marker.get("note") != expected["note"]


def timeline_review_rows(markers):
    rows = []
    for frame, marker in sorted(markers.items()):
        for entry in owned_entries(marker) or []:
            entry["frame"] = int(frame)
            rows.append(entry)
    return rows


def build_import_plan(rows, context, existing, author_filter=None, status_filter=None, mode="auto", offset=0):
    if mode not in ("auto", "relative", "absolute"):
        raise ValueError("请选择有效的时间基准")
    offset = int(str(offset).strip())
    existing_rows = {entry["id"]: entry for entry in timeline_review_rows(existing)}
    candidates = []
    for source in rows:
        entry = entry_for_row(source)
        previous = existing_rows.get(entry["id"])
        if previous:
            entry["local_state"] = previous.get("local_state")
        if author_filter is not None and entry["author"] != author_filter:
            continue
        if status_filter is not None and effective_status(entry) != status_filter:
            continue
        candidates.append((source, entry, timecode_to_frames(source.get("tc"), context["fps"])))
    frame_list = [frame for _, _, frame in candidates if frame >= 0]
    detected, hits, other = pick_timecode_mode(frame_list, context["duration"], context["tc_start"], offset)
    ambiguous = mode == "auto" and context["tc_start"] != 0 and hits == other and hits > 0
    selected_mode = detected if mode == "auto" else mode
    stats = {"invalid": 0, "outside": 0, "duplicate": 0, "conflict": 0, "new": 0, "merge": 0}
    plan = {"operations": [], "rows": [], "stats": stats, "mode": selected_mode, "blocked": ambiguous,
            "warning": "时间基准有歧义，请选择相对或绝对" if ambiguous else "", "context": context["signature"]}
    groups, shown, seen = {}, {}, set()
    for source, entry, tc_frames in candidates:
        frame = marker_frame_for_timecode(tc_frames, selected_mode, context["tc_start"]) + offset
        display = dict(entry, frame=frame, result="")
        plan["rows"].append(display)
        if tc_frames < 0:
            display["result"], stats["invalid"] = "无效时间码", stats["invalid"] + 1
        elif not 0 <= frame < context["duration"]:
            display["result"], stats["outside"] = "超出范围", stats["outside"] + 1
        elif entry["id"] in seen:
            display["result"], stats["duplicate"] = "重复", stats["duplicate"] + 1
        elif entry["id"] in existing_rows and existing_rows[entry["id"]]["frame"] != frame:
            display["result"], stats["conflict"] = "已导入其他位置", stats["conflict"] + 1
        else:
            groups.setdefault(frame, []).append(entry)
            shown.setdefault(frame, []).append(display)
        seen.add(entry["id"])
    for frame, incoming in groups.items():
        before = marker_snapshot(existing.get(frame))
        old = owned_entries(before) if before is not None else []
        if before is not None and (old is None or marker_is_edited(before, old)):
            for display in shown[frame]:
                display["result"] = "已有标记冲突"
                stats["conflict"] += 1
            continue
        merged = {entry["id"]: entry for entry in old}
        for entry, display in zip(incoming, shown[frame]):
            previous = merged.get(entry["id"])
            if previous:
                entry["local_state"] = previous.get("local_state")
            if previous == entry:
                display["result"], stats["duplicate"] = "已导入", stats["duplicate"] + 1
            else:
                display["result"] = "合并" if before is not None else "新增"
                stats["merge" if before is not None else "new"] += 1
                merged[entry["id"]] = entry
        after = marker_for_entries(list(merged.values()))
        if before is not None:
            after["duration"] = before["duration"]
        if after != before and not all(display["result"] == "已导入" for display in shown[frame]):
            plan["operations"].append({"frame": frame, "before": before, "after": after})
    if ambiguous:
        plan["operations"] = []
    return plan


def review_local_state(value):
    if value is True:
        return "done"
    if value is False:
        return "open"
    if value in ("done", "open", "deferred"):
        return value
    raise ValueError("无效的批注状态")


def build_status_plan(markers, selected_ids, completed):
    operations = []
    for frame, marker in markers.items():
        entries = owned_entries(marker)
        if entries is None or not any(entry["id"] in selected_ids for entry in entries):
            continue
        if marker_is_edited(marker, entries):
            raise RuntimeError("标记备注已被手动修改，请核对后再更新状态")
        for entry in entries:
            if entry["id"] in selected_ids:
                entry["local_state"] = review_local_state(completed)
        after = marker_for_entries(entries)
        before = marker_snapshot(marker)
        after["duration"] = before["duration"]
        if after != before:
            operations.append({"frame": int(frame), "before": before, "after": after})
    return {"operations": operations}


def is_legacy_review(marker):
    return marker.get("name") == MARKER_NAME and not marker.get("customData")


def locate_review_marker(markers, frame):
    if frame in markers:
        marker = markers[frame]
        return (int(frame), marker) if owned_entries(marker) is not None or is_legacy_review(marker) else None
    candidates = [(int(start), marker) for start, marker in markers.items()
                  if start <= frame < start + max(1, float(marker.get("duration", 1)))
                  and (owned_entries(marker) is not None or is_legacy_review(marker))]
    if len(candidates) > 1:
        raise RuntimeError("此处有多个范围标记，请移至目标标记起点")
    return candidates[0] if candidates else None


def build_playhead_status_plan(markers, context, frame, completed):
    target = locate_review_marker(markers, frame)
    if target is None:
        raise RuntimeError("请将播放头移到阅流批注标记处")
    frame, marker = target
    entries = owned_entries(marker)
    if entries is not None:
        return build_status_plan({frame: marker}, {entry["id"] for entry in entries}, completed)
    before = marker_snapshot(marker)
    note = plain_text(marker.get("note"))
    match = re.fullmatch(r"【([^】]+)】(.*)", note, flags=re.S)
    author, comment = match.groups() if match else ("", note)
    entry = entry_for_row({"tc": frames_to_timecode(frame, context["fps"], context["drop"]),
                           "author": author, "comment": comment, "source_status": ""})
    entry["local_state"] = review_local_state(completed)
    entry["open_color"] = before["color"]
    after = marker_for_entries([entry])
    after["duration"] = before["duration"]
    return {"operations": [{"frame": frame, "before": before, "after": after}]}


def build_clear_plan(markers, include_legacy=False):
    operations = []
    for frame, marker in markers.items():
        if owned_entries(marker) is not None or (include_legacy and is_legacy_review(marker)):
            operations.append({"frame": int(frame), "before": marker_snapshot(marker), "after": None})
    return {"operations": operations}


def marker_at(timeline, frame):
    markers = timeline.GetMarkers()
    if not isinstance(markers, dict):
        raise RuntimeError("无法读取时间线标记")
    return marker_snapshot(markers.get(frame))


def add_marker(timeline, frame, marker):
    return timeline.AddMarker(frame, marker["color"], marker["name"], marker["note"], marker["duration"], marker["customData"])


def apply_marker_operation(timeline, operation):
    frame, before, after = operation["frame"], operation["before"], operation["after"]
    current = marker_at(timeline, frame)
    if current != before:
        raise RuntimeError("标记已变化，请刷新后重试")
    if before == after:
        return
    deleted = False
    try:
        if before is not None:
            deleted = True  # 先记录删除尝试，读回中断时仍走恢复流程。
            if not timeline.DeleteMarkerAtFrame(frame) or marker_at(timeline, frame) is not None:
                raise RuntimeError("删除标记失败")
        if after is not None:
            if not add_marker(timeline, frame, after) or marker_at(timeline, frame) != after:
                raise RuntimeError("写入标记失败或读回不一致")
    except Exception as exc:
        if deleted and before is not None:
            try:
                current = marker_at(timeline, frame)
                if current == after and after is not None:
                    timeline.DeleteMarkerAtFrame(frame)
                    current = marker_at(timeline, frame)
                if current is None:
                    add_marker(timeline, frame, before)
                if marker_at(timeline, frame) != before:
                    raise RuntimeError("原标记恢复失败，请检查该位置")
            except Exception as restore_exc:
                raise RuntimeError(str(exc) + "；" + str(restore_exc)) from exc
        raise


class MarkerJob:
    def __init__(self, timeline, operations):
        self.timeline = timeline
        self.operations = copy.deepcopy(operations)
        self.index = self.success = self.failed = 0
        self.errors, self.changes = [], []
        self.done = not operations
        self.cancelled = False

    @classmethod
    def for_undo(cls, timeline, changes):
        return cls(timeline, [{"frame": change["frame"], "before": change["after"], "after": change["before"]}
                              for change in reversed(changes)])

    def cancel(self):
        self.done = self.cancelled = True

    def step(self, limit=4):
        if self.done:
            return
        for operation in self.operations[self.index:self.index + limit]:
            attempted = False
            try:
                before = marker_at(self.timeline, operation["frame"])
                if before != operation["before"]:
                    raise RuntimeError("标记已变化，请刷新后重试")
                attempted = True
                apply_marker_operation(self.timeline, operation)
                self.success += 1
            except Exception as exc:
                self.failed += 1
                self.errors.append("第 %d 帧：%s" % (operation["frame"], exc))
            finally:
                if attempted:
                    try:
                        current = marker_at(self.timeline, operation["frame"])
                        if current != before and (current == operation["after"] or current is None):
                            self.changes.append({"frame": operation["frame"], "before": before, "after": current})
                    except Exception:
                        self.changes.append(copy.deepcopy(operation))
                self.index += 1
        self.done = self.index >= len(self.operations)


def file_fingerprint(path):
    stat = Path(path).stat()
    return stat.st_size, stat.st_mtime_ns


class ReviewApp:
    def __init__(self, resolve, ui, dispatcher, fusion=None):
        self.resolve, self.ui, self.dispatcher, self.fusion = resolve, ui, dispatcher, fusion
        self.manager = resolve.GetProjectManager()
        self.project_id = self.timeline = self.context = None
        self.markers, self.plan = {}, None
        self.file_path = self.workbook = self.file_stamp = None
        self.author_values, self.status_values = [None], [None]
        self.visible_rows, self.node_rows = [], {}
        self.row_nodes, self.row_cells, self.row_order = {}, {}, []
        self.render_binding = None
        self.filter_choices = {}
        self.updating = False
        self.job = None
        self.job_kind = ""
        self.job_binding = None
        self.undo_changes, self.undo_binding = [], None
        self.report = self.preview_report = ""
        self.main_open = True
        self.panel_open = self.clear_open = False
        self.panel = self.clear_window = self.details_window = None
        self.panel_target = None
        self.panel_notice = None
        self.window = self.build_window()
        self.timer = ui.Timer({"ID": "ReviewTimer", "Interval": 350, "SingleShot": False})
        self.dispatcher.On.Timeout = self.tick
        self.refresh()

    def button(self, identity, text, primary=False, **options):
        props = {"ID": identity, "Text": text, "Weight": 0, "MinimumSize": [72, 32]}
        if primary:
            props["StyleSheet"] = (
                "QPushButton {background: #3677a8; color: #fff; border: 1px solid #4885b2;"
                "border-radius: 4px; padding: 7px 14px;}"
                "QPushButton:hover {background: #4085b8;}"
                "QPushButton:focus {border: 1px solid #b1d5f5;}"
                "QPushButton:disabled {background: #33383e; color: #7f858c; border-color: #42474e;}"
            )
        props.update(options)
        return self.ui.Button(props)

    def label(self, text, width=60):
        return self.ui.Label({"Text": text, "Weight": 0, "MinimumSize": [width, 28]})

    def build_window(self):
        ui = self.ui
        window = self.dispatcher.AddWindow({
            "ID": WINDOW_ID, "WindowTitle": "Import YueLiu · " + PLUGIN_VERSION,
            "Geometry": [160, 100, 780, 660], "MinimumSize": [700, 580], "Margin": 18,
        }, ui.VGroup({"Spacing": 10}, [
            ui.HGroup({"Weight": 0, "Spacing": 10}, [
                ui.Label({"ID": "TargetLabel", "Text": "", "WordWrap": True,
                          "Font": ui.Font({"PixelSize": 14, "Bold": True})}),
                self.button("RefreshBtn", "刷新"),
            ]),
            ui.HGroup({"Weight": 0, "Spacing": 8}, [
                ui.LineEdit({"ID": "FileInput", "ReadOnly": True, "PlaceholderText": "选择 XLSX", "MinimumSize": [0, 30]}),
                self.button("BrowseBtn", "选择"),
            ]),
            ui.HGroup({"Weight": 0, "Spacing": 10}, [
                self.label("评论人"), ui.ComboBox({"ID": "AuthorFilter", "MinimumSize": [0, 30]}),
                self.label("状态", 40), ui.ComboBox({"ID": "StatusFilter", "MinimumSize": [0, 30]}),
            ]),
            ui.HGroup({"Weight": 0, "Spacing": 10}, [
                self.label("时间基准"), ui.ComboBox({"ID": "TimeMode", "MinimumSize": [0, 30]}),
                self.label("偏移(帧)", 70), ui.LineEdit({"ID": "FrameOffset", "Text": "0", "MinimumSize": [70, 30], "Weight": 0.4}),
            ]),
            ui.Tree({"ID": "ReviewTree", "ColumnCount": 4, "SelectionMode": "ExtendedSelection",
                     "AlternatingRowColors": True, "RootIsDecorated": False, "UniformRowHeights": True,
                     "Weight": 1, "MinimumSize": [0, 240], "ToolTip": "双击定位时间线；通过“标注”操作播放头处的批注"}),
            ui.HGroup({"Weight": 0, "Spacing": 8}, [
                ui.Label({"ID": "SummaryLabel", "Text": "", "WordWrap": True}), self.button("DetailsBtn", "详情"),
            ]),
            ui.Label({"ID": "StatusLabel", "Text": "", "WordWrap": True, "Weight": 0}),
            ui.HGroup({"Weight": 0, "Spacing": 8}, [
                self.button("CloseBtn", "关闭"), self.button("ClearBtn", "清除"), self.button("UndoBtn", "撤销"),
                ui.Label({"Text": "", "Weight": 1}), self.button("PanelBtn", "标注"), self.button("ImportBtn", "导入", True),
            ]),
        ]))
        items = window.GetItems()
        for text in ("自动", "相对", "绝对"):
            items["TimeMode"].AddItem(text)
        items["ReviewTree"].SetHeaderLabels(["时间码", "评论人", "状态", "意见"])
        for column, width in enumerate((120, 80, 85, 400)):
            items["ReviewTree"].ColumnWidth[column] = width
        items["UndoBtn"].ToolTip = "撤销上次标记操作，仅本次运行有效"
        items["PanelBtn"].ToolTip = "打开悬浮面板，完成或恢复播放头处的批注标记"
        items["TimeMode"].ToolTip = "相对：视频从 0 起算；绝对：包含时间线起始时间码"
        window.On[WINDOW_ID].Close = self.close_main
        window.On.CloseBtn.Clicked = self.close_main
        window.On.RefreshBtn.Clicked = self.refresh
        window.On.BrowseBtn.Clicked = self.browse
        window.On.AuthorFilter.CurrentIndexChanged = self.filters_changed
        window.On.StatusFilter.CurrentIndexChanged = self.filters_changed
        window.On.TimeMode.CurrentIndexChanged = self.filters_changed
        window.On.FrameOffset.TextChanged = self.filters_changed
        window.On.ImportBtn.Clicked = self.import_reviews
        window.On.UndoBtn.Clicked = self.undo
        window.On.ClearBtn.Clicked = self.show_clear
        window.On.PanelBtn.Clicked = self.show_panel
        window.On.DetailsBtn.Clicked = self.show_details
        window.On.ReviewTree.ItemDoubleClicked = self.seek_selected
        return window

    def status(self, text="", kind="normal"):
        colors = {"normal": "#9ca2aa", "error": "#e39791", "success": "#9bc5a5", "warning": "#d6b678"}
        item = self.window.GetItems()["StatusLabel"]
        self.set_changed(item, Text=text, StyleSheet="color: " + colors.get(kind, colors["normal"]) + ";")

    def read_target(self):
        project = self.manager.GetCurrentProject()
        if project is None:
            raise RuntimeError("请先打开项目")
        timeline = project.GetCurrentTimeline()
        context = timeline_context(timeline)
        return project, timeline, context

    def binding(self):
        return (self.project_id, self.context["signature"]) if self.context is not None else None

    def read_markers(self, timeline):
        markers = timeline.GetMarkers()
        if not isinstance(markers, dict):
            raise RuntimeError("无法读取时间线标记，请刷新后重试")
        return markers

    def refresh(self, ev=None):
        if self.job is not None or self.clear_open:
            return
        self.timeline = self.context = self.project_id = None
        self.markers = {}
        message = ""
        try:
            project, self.timeline, self.context = self.read_target()
            self.project_id = project.GetUniqueId()
            self.markers = self.read_markers(self.timeline)
            self.window.GetItems()["TargetLabel"].Text = project.GetName() + " / " + self.context["name"]
            if not self.context["duration"]:
                message = "当前时间线为空"
        except Exception as exc:
            self.timeline = self.context = self.project_id = None
            self.markers = {}
            self.window.GetItems()["TargetLabel"].Text = "未打开时间线"
            message = str(exc)
        if self.file_path is not None:
            try:
                if file_fingerprint(self.file_path) != self.file_stamp:
                    self.workbook = read_review_workbook(self.file_path)
                    self.file_stamp = file_fingerprint(self.file_path)
            except Exception as exc:
                self.workbook = None
                message = "文件读取失败：" + str(exc)
        self.status(message, "warning" if message else "normal")
        self.populate_filters()
        self.rebuild_view()

    def source_rows(self):
        return [row for sheet in self.workbook["sheets"] for row in sheet["rows"]] if self.workbook else []

    def view_entries(self, markers=None, context=None):
        markers = self.markers if markers is None else markers
        context = self.context if context is None else context
        rows = timeline_review_rows(markers)
        if context:
            for frame, marker in sorted(markers.items()):
                if is_legacy_review(marker):
                    note = plain_text(marker.get("note"))
                    match = re.fullmatch(r"【([^】]+)】(.*)", note, flags=re.S)
                    author, comment = match.groups() if match else ("", note)
                    entry = entry_for_row({"tc": frames_to_timecode(int(frame), context["fps"], context["drop"]),
                                           "author": author, "comment": comment})
                    rows.append(dict(entry, frame=int(frame), legacy=True))
        return sorted(rows, key=lambda entry: entry["frame"])

    def filtered_entries(self, rows):
        author = self.filter_value("AuthorFilter", self.author_values)
        state = self.filter_value("StatusFilter", self.status_values)
        return [entry for entry in rows if (author is None or entry["author"] == author)
                and (state is None or effective_status(entry) == state)]

    def filter_value(self, identity, choices):
        index = self.window.GetItems()[identity].CurrentIndex
        return choices[index] if 0 <= index < len(choices) else None

    def populate_filters(self):
        author = self.filter_value("AuthorFilter", self.author_values)
        status = self.filter_value("StatusFilter", self.status_values)
        rows = self.view_entries() + [entry_for_row(row) for row in self.source_rows()]
        self.author_values = [None] + sorted({entry["author"] for entry in rows} | ({author} if author is not None else set()))
        base = {"待修改", "已修改", "暂不修改"}
        self.status_values = [None, "待修改", "已修改", "暂不修改"] + sorted(
            ({effective_status(entry) for entry in rows} | ({status} if status is not None else set())) - base)
        self.updating = True
        try:
            for identity, choices, selected, all_label in (("AuthorFilter", self.author_values, author, "全部评论人"),
                                                          ("StatusFilter", self.status_values, status, "全部状态")):
                if self.filter_choices.get(identity) == choices:
                    continue
                combo = self.window.GetItems()[identity]
                combo.Clear()
                for value in choices:
                    combo.AddItem(all_label if value is None else value or "（未署名）")
                combo.CurrentIndex = choices.index(selected) if selected in choices else 0
                self.filter_choices[identity] = list(choices)
        finally:
            self.updating = False

    def filters_changed(self, ev=None):
        if not self.updating and self.job is None:
            self.rebuild_view()

    def rebuild_view(self):
        items = self.window.GetItems()
        self.plan = None
        self.preview_report = ""
        self.visible_rows = self.filtered_entries(self.view_entries())
        try:
            summary = "%d 条批注 · 已修改 %d" % (len(self.visible_rows), sum(effective_status(entry) == "已修改" for entry in self.visible_rows))
            if self.context is not None and self.workbook:
                mode_index = items["TimeMode"].CurrentIndex
                mode = ("auto", "relative", "absolute")[mode_index if 0 <= mode_index <= 2 else 0]
                self.plan = build_import_plan(self.source_rows(), self.context, self.markers,
                                              self.filter_value("AuthorFilter", self.author_values),
                                              self.filter_value("StatusFilter", self.status_values), mode, items["FrameOffset"].Text)
                stats = self.plan["stats"]
                issues = [entry for entry in self.plan["rows"] if entry["result"] in
                          ("无效时间码", "超出范围", "已有标记冲突", "已导入其他位置")]
                self.preview_report = "\n\n".join("%s · %s · %s\n%s" %
                    (entry["tc"], entry["author"] or "未署名", entry["result"], entry["comment"]) for entry in issues)
                summary += " · 待导入 %d" % len(self.plan["operations"])
                if stats["conflict"] or stats["outside"] or stats["invalid"]:
                    summary += " · 冲突 %d / 越界 %d / 无效 %d" % (stats["conflict"], stats["outside"], stats["invalid"])
                if self.plan["warning"]:
                    self.status(self.plan["warning"], "warning")
            self.set_changed(items["SummaryLabel"], Text=summary)
            self.render_rows()
        except Exception as exc:
            self.plan = None
            self.status(str(exc), "error")
        self.update_actions()

    @staticmethod
    def set_changed(widget, **properties):
        for name, value in properties.items():
            if getattr(widget, name, None) != value:
                setattr(widget, name, value)

    def render_rows(self):
        tree = self.window.GetItems()["ReviewTree"]
        if self.render_binding != self.binding():
            tree.Clear()
            self.row_nodes, self.row_cells, self.row_order = {}, {}, []
            self.render_binding = self.binding()
        keys = [(entry["frame"], entry["id"], bool(entry.get("legacy"))) for entry in self.visible_rows]
        wanted = set(keys)
        for index in range(len(self.row_order) - 1, -1, -1):
            key = self.row_order[index]
            if key not in wanted:
                tree.TakeTopLevelItem(index)
                del self.row_nodes[key]
                self.row_cells.pop(key, None)
                self.row_order.pop(index)
        self.node_rows = {}
        for index, (key, entry) in enumerate(zip(keys, self.visible_rows)):
            if key not in self.row_nodes:
                node = tree.NewItem()
                tree.InsertTopLevelItem(index, node)
                self.row_nodes[key] = node
                self.row_order.insert(index, key)
            else:
                node = self.row_nodes[key]
                old_index = self.row_order.index(key)
                if old_index != index:
                    tree.TakeTopLevelItem(old_index)
                    tree.InsertTopLevelItem(index, node)
                    self.row_order.insert(index, self.row_order.pop(old_index))
            position = entry["tc"]
            if self.context and entry["frame"] >= 0:
                position = frames_to_timecode(self.context["tc_start"] + entry["frame"], self.context["fps"], self.context["drop"])
            cells = (position, entry["author"] or "未署名", effective_status(entry), entry["comment"])
            previous = self.row_cells.get(key, ())
            for column, text in enumerate(cells):
                if column >= len(previous) or previous[column] != text:
                    node.Text[column] = text.replace("\n", " ")[:160]
                    node.ToolTip[column] = text
            self.row_cells[key] = cells
            self.node_rows[str(node)] = entry

    def update_actions(self):
        items = self.window.GetItems()
        busy = self.job is not None
        for name in ("RefreshBtn", "BrowseBtn", "AuthorFilter", "StatusFilter", "FileInput", "TimeMode", "FrameOffset"):
            self.set_changed(items[name], Enabled=not busy)
        self.set_changed(items["ImportBtn"], Text="停止" if busy else "导入",
                         Enabled=busy or bool(self.plan and self.plan["operations"] and not self.plan["blocked"]))
        self.set_changed(items["ClearBtn"], Enabled=not busy and bool(build_clear_plan(self.markers, True)["operations"]))
        self.set_changed(items["UndoBtn"], Enabled=not busy and bool(self.undo_changes) and self.undo_binding == self.binding())
        self.set_changed(items["PanelBtn"], Enabled=not busy and self.context is not None)
        self.set_changed(items["DetailsBtn"], Enabled=bool(self.report or self.preview_report))
        if self.panel_open and busy:
            for name in ("DoneBtn", "DeferBtn", "PrevBtn", "NextBtn"):
                self.set_changed(self.panel.GetItems()[name], Enabled=False)

    def browse(self, ev=None):
        if self.fusion is None or self.job is not None:
            return
        path = self.fusion.RequestFile()
        if path:
            self.load_file(path)

    def load_file(self, path):
        if self.job is not None:
            return
        self.file_path = self.workbook = self.file_stamp = None
        items = self.window.GetItems()
        items["FileInput"].Text = ""
        try:
            path = Path(path)
            if path.suffix.lower() != ".xlsx":
                raise ValueError("请选择 XLSX 文件")
            self.status("读取中…")
            stamp = file_fingerprint(path)
            workbook = read_review_workbook(path)
            if file_fingerprint(path) != stamp:
                raise RuntimeError("文件读取期间已变化，请重新选择")
            self.file_path, self.file_stamp, self.workbook = path, stamp, workbook
            items["FileInput"].Text = path.name
            items["FileInput"].ToolTip = str(path)
            self.refresh()
        except Exception as exc:
            self.populate_filters()
            self.rebuild_view()
            self.status("读取失败：" + str(exc), "error")

    def import_reviews(self, ev=None):
        if self.clear_open:
            return
        if self.job is not None:
            self.job.cancel()
            return
        try:
            project, timeline, context = self.read_target()
            binding = project.GetUniqueId(), context["signature"]
            if binding != self.binding():
                self.refresh()
                raise RuntimeError("当前时间线已变化，请核对预览后重试")
            if self.file_path is None or file_fingerprint(self.file_path) != self.file_stamp:
                raise RuntimeError("文件已变化，请刷新预览")
            self.markers = self.read_markers(timeline)
            self.rebuild_view()
            if self.plan is None or self.plan["blocked"] or not self.plan["operations"]:
                raise RuntimeError(self.plan["warning"] if self.plan and self.plan["warning"] else "没有待导入的标记")
            self.start_job("导入", timeline, self.plan["operations"], binding)
        except Exception as exc:
            self.status(str(exc), "error")

    def show_panel(self, ev=None):
        if self.clear_open:
            return
        if self.panel is None:
            ui = self.ui
            self.panel = self.dispatcher.AddWindow({
                "ID": "ReviewQuickPanel", "WindowTitle": "修改标注",
                "Geometry": [980, 160, 380, 220], "MinimumSize": [340, 190], "Margin": 12,
                "WindowFlags": {"Window": True, "WindowStaysOnTopHint": True},
            }, ui.VGroup({"Spacing": 10}, [
                ui.HGroup({"Weight": 0, "Spacing": 8}, [
                    ui.Label({"ID": "QuickPosition", "Text": "", "WordWrap": True, "Weight": 1,
                              "Font": ui.Font({"PixelSize": 13, "Bold": True})}),
                    ui.Button({"ID": "ListBtn", "Text": "≡", "FixedSize": [26, 26], "Weight": 0,
                               "Flat": True, "ToolTip": "列表", "AccessibleName": "列表",
                               "Font": ui.Font({"PixelSize": 21}),
                               "StyleSheet": "QPushButton {border: 0; padding: 0; border-radius: 4px;} QPushButton:hover {background: #3b3d43;}"}),
                ]),
                ui.Label({"ID": "QuickComment", "Text": "", "WordWrap": True, "Weight": 0,
                          "MinimumSize": [0, 24], "MaximumSize": [10000, 60]}),
                ui.Label({"Text": "", "Weight": 1}),
                ui.HGroup({"ID": "QuickActions", "Weight": 0, "Spacing": 12}, [
                    ui.Label({"Text": "", "Weight": 1}),
                    self.button("DoneBtn", "完成", True, FixedSize=[112, 40], Font=ui.Font({"PixelSize": 14, "Bold": True})),
                    self.button("DeferBtn", "搁置", FixedSize=[112, 40], Font=ui.Font({"PixelSize": 14, "Bold": True})),
                    ui.Label({"Text": "", "Weight": 1}),
                ]),
                ui.HGroup({"ID": "QuickNavigation", "Weight": 0, "Spacing": 8}, [
                    self.button("PrevBtn", "上一个"), ui.Label({"ID": "QuickCount", "Text": "", "Alignment": {"AlignHCenter": True, "AlignVCenter": True}, "Weight": 1}),
                    self.button("NextBtn", "下一个"),
                ]),
            ]))
            self.panel.On.DeferBtn.Clicked = lambda ev: self.complete_here("toggle_deferred")
            self.panel.On.PrevBtn.Clicked = lambda ev: self.navigate_review(-1)
            self.panel.On.NextBtn.Clicked = lambda ev: self.navigate_review(1)
            self.panel.On.DoneBtn.Clicked = lambda ev: self.complete_here("toggle")
            self.panel.On.ListBtn.Clicked = self.show_list
            self.panel.On.ReviewQuickPanel.Close = self.close_panel
            self.panel.GetItems()["DoneBtn"].ToolTip = "将播放头处的批注标记设为已修改；同一点内的全部意见会一起完成"
            for name in ("PrevBtn", "NextBtn"):
                self.panel.GetItems()[name].FixedSize = [72, 32]
            self.panel.GetItems()["DeferBtn"].ToolTip = "标记为暂不修改；同一点内的全部意见一起搁置"
            for name in ("PrevBtn", "NextBtn"):
                self.panel.GetItems()[name].ToolTip = "按当前评论人和状态筛选定位批注"
        self.panel_open = True
        self.main_open = False
        self.window.Hide()
        self.panel.Show()
        self.panel.Raise()
        self.poll_panel()
        if self.job is None:
            self.timer.Interval = 350
        self.timer.Start()

    def current_marker_target(self):
        project, timeline, context = self.read_target()
        tc_frames = timecode_to_frames(timeline.GetCurrentTimecode(), context["fps"], context["drop"])
        if tc_frames < 0:
            raise RuntimeError("无法读取播放头位置")
        frame = tc_frames - context["tc_start"]
        markers = self.read_markers(timeline)
        located = locate_review_marker(markers, frame)
        if located is None:
            raise RuntimeError("移到批注标记处")
        marker_frame, marker = located
        binding = project.GetUniqueId(), context["signature"]
        return {"timeline": timeline, "context": context, "binding": binding, "markers": markers,
                "frame": marker_frame, "marker": marker,
                "signature": (binding, marker_frame, marker_snapshot(marker))}

    def navigation_context(self):
        project, timeline, context = self.read_target()
        tc_frames = timecode_to_frames(timeline.GetCurrentTimecode(), context["fps"], context["drop"])
        if tc_frames < 0:
            raise RuntimeError("无法读取播放头位置")
        frame = tc_frames - context["tc_start"]
        markers = self.read_markers(timeline)
        located = locate_review_marker(markers, frame)
        if located is not None:
            frame = located[0]
        points = sorted({entry["frame"] for entry in self.filtered_entries(self.view_entries(markers, context))
                         if 0 <= entry["frame"] < context["duration"]})
        return project, timeline, context, frame, markers, points

    def poll_panel(self):
        if not self.panel_open or self.job is not None:
            return
        items = self.panel.GetItems()
        values = {"DoneBtn": {"Enabled": False, "Text": "完成"}, "DeferBtn": {"Enabled": False, "Text": "搁置"},
                  "PrevBtn": {"Enabled": False}, "NextBtn": {"Enabled": False}, "QuickCount": {"Text": ""},
                  "QuickComment": {"Text": "", "ToolTip": "", "StyleSheet": ""}}
        self.panel_target = None
        try:
            if self.clear_open:
                raise RuntimeError("清除确认中")
            project, timeline, context, frame, markers, points = self.navigation_context()
            values["PrevBtn"]["Enabled"] = any(point < frame for point in points)
            values["NextBtn"]["Enabled"] = any(point > frame for point in points)
            values["QuickCount"]["Text"] = ("%d / %d" % (points.index(frame) + 1, len(points))) if frame in points else ("%d 个标记" % len(points) if points else "")
            located = locate_review_marker(markers, frame)
            if located is None:
                self.panel_notice = None
                raise RuntimeError("移到批注标记处")
            marker_frame, marker = located
            binding = project.GetUniqueId(), context["signature"]
            self.panel_target = {"timeline": timeline, "context": context, "binding": binding, "markers": markers,
                                 "frame": marker_frame, "marker": marker, "signature": (binding, marker_frame, marker_snapshot(marker))}
            entries = owned_entries(marker)
            statuses = [effective_status(entry) for entry in entries] if entries else ["待修改"]
            total = len(statuses)
            state = statuses[0] if len(set(statuses)) == 1 else "部分完成" if "已修改" in statuses else "部分搁置" if "暂不修改" in statuses else "待修改"
            tc = frames_to_timecode(context["tc_start"] + marker_frame, context["fps"], context["drop"])
            color = "color: #9bc5a5;" if state == "已修改" else "color: #d6b678;" if state == "暂不修改" else ""
            values["QuickPosition"] = {"Text": tc + " · " + state, "ToolTip": context["name"], "StyleSheet": color}
            comment = entries[0]["comment"] if entries else marker.get("note", "")
            text = ("%d 条意见 · " % total if total > 1 else "") + comment.replace("\n", " ")
            values["QuickComment"] = {"Text": text if len(text) <= 42 else text[:39] + "…", "ToolTip": marker.get("note", ""), "StyleSheet": ""}
            all_done = all(status == "已修改" for status in statuses)
            all_deferred = all(status == "暂不修改" for status in statuses)
            values["DoneBtn"] = {"Enabled": True, "Text": "恢复" if all_done else "完成",
                                  "ToolTip": "恢复为待修改" if all_done else "将同一点内的全部意见设为已修改"}
            values["DeferBtn"] = {"Enabled": True, "Text": "取消搁置" if all_deferred else "搁置",
                                   "ToolTip": "恢复为待修改" if all_deferred else "将同一点内的全部意见设为暂不修改"}
            if self.panel_notice is not None and self.panel_notice["key"] == (binding, marker_frame):
                message = self.panel_notice["message"]
                values["QuickComment"] = {"Text": message[:60], "ToolTip": message, "StyleSheet": "color: #e39791;"}
            else:
                self.panel_notice = None
        except Exception as exc:
            values["QuickPosition"] = {"Text": str(exc)[:60], "ToolTip": str(exc), "StyleSheet": "color: #9ca2aa;"}
        for identity, properties in values.items():
            self.set_changed(items[identity], **properties)

    def navigate_review(self, direction):
        if self.job is not None or self.clear_open:
            return
        try:
            project, timeline, context, frame, markers, points = self.navigation_context()
            candidates = [point for point in points if point > frame] if direction > 0 else [point for point in points if point < frame]
            if candidates:
                target = min(candidates) if direction > 0 else max(candidates)
                tc = frames_to_timecode(context["tc_start"] + target, context["fps"], context["drop"])
                if not timeline.SetCurrentTimecode(tc):
                    raise RuntimeError("无法定位该批注")
            self.poll_panel()
        except Exception as exc:
            self.status(str(exc), "error")

    def complete_here(self, completed):
        if self.job is not None or self.clear_open:
            return
        target = None
        self.panel_notice = None
        try:
            target = self.current_marker_target()
            self.panel_target = target  # 点击时读取当前标记，避免沿用上一点的按钮状态。
            entries = owned_entries(target["marker"]) or []
            if completed == "toggle":
                completed = not bool(entries and all(effective_status(entry) == "已修改" for entry in entries))
            elif completed == "toggle_deferred":
                completed = "open" if entries and all(effective_status(entry) == "暂不修改" for entry in entries) else "deferred"
            plan = build_playhead_status_plan(target["markers"], target["context"], target["frame"], completed)
            if plan["operations"]:
                self.start_job({"done": "完成", "open": "恢复", "deferred": "搁置"}[review_local_state(completed)], target["timeline"], plan["operations"], target["binding"])
            else:
                self.poll_panel()
        except Exception as exc:
            if target is not None:
                self.panel_notice = {"key": (target["binding"], target["frame"]), "message": str(exc)}
            self.poll_panel()
            self.status(str(exc), "error")

    def show_list(self, ev=None):
        self.main_open = True
        self.window.Show()
        self.window.Raise()
        if self.clear_open:
            self.clear_window.Raise()
        elif self.job is None:
            self.refresh()

    def close_panel(self, ev=None):
        if self.panel is not None:
            self.panel.Hide()
        self.panel_open = False
        self.panel_target = None
        if self.job is None:
            self.timer.Stop()
        if not self.main_open:
            self.dispatcher.ExitLoop()

    def seek_selected(self, ev=None):
        try:
            selected = self.window.GetItems()["ReviewTree"].SelectedItems() or {}
            nodes = list(selected.values()) if isinstance(selected, dict) else list(selected)
            entry = next((self.node_rows[str(node)] for node in nodes if str(node) in self.node_rows), None)
            if entry is None:
                return
            project, timeline, context = self.read_target()
            if (project.GetUniqueId(), context["signature"]) != self.binding():
                self.refresh()
                raise RuntimeError("时间线已变化，请重新选择批注")
            frame = entry.get("frame")
            if frame is None or not 0 <= frame < context["duration"]:
                raise RuntimeError("该批注没有有效的时间线位置")
            tc = frames_to_timecode(context["tc_start"] + frame, context["fps"], context["drop"])
            if not timeline.SetCurrentTimecode(tc):
                raise RuntimeError("无法定位该批注")
            self.show_panel()
        except Exception as exc:
            self.status(str(exc), "error")

    def start_job(self, kind, timeline, operations, binding):
        if self.job is not None:
            raise RuntimeError("操作正在进行，请等待完成或停止")
        self.job = MarkerJob(timeline, operations)
        self.job_kind, self.job_binding = kind, binding
        self.report = ""
        self.panel_notice = None
        self.status(kind + "中…")
        if self.panel_open:
            self.panel.GetItems()["QuickComment"].Text = kind + "中…"
        self.update_actions()
        self.timer.Interval = 20
        self.timer.Start()

    def tick(self, ev=None):
        if self.job is None:
            if self.panel_open:
                self.poll_panel()
            return
        changed = False
        try:
            project, timeline, context = self.read_target()
            changed = (project.GetUniqueId(), context["signature"]) != self.job_binding
            if changed:
                self.job.cancel()
            self.job.step(4)
            if not self.job.done:
                self.status(self.job_kind + " %d / %d" % (self.job.index, len(self.job.operations)))
                return
        except Exception as exc:
            self.job.cancel()
            self.job.failed += 1
            self.job.errors.append(str(exc))
        self.finish_job()
        if changed:
            self.status("项目或时间线已变化，操作已停止", "warning")

    def finish_job(self):
        job, kind = self.job, self.job_kind
        if job is None:
            return
        self.timer.Stop()
        if kind == "撤销":
            remaining = []
            for change in self.undo_changes:
                try:
                    if marker_at(job.timeline, change["frame"]) != change["before"]:
                        remaining.append(change)
                except Exception:
                    remaining.append(change)
            self.undo_changes = remaining
        elif job.changes:
            self.undo_changes, self.undo_binding = job.changes, self.job_binding
        labels = {"导入": "已导入", "完成": "已完成", "恢复": "已恢复", "搁置": "已搁置", "清除": "已清除", "撤销": "已撤销"}
        summary = "%s %d 个标记" % (labels.get(kind, kind), job.success)
        if job.failed:
            summary += " · 失败 %d" % job.failed
        if job.cancelled:
            summary = "已停止 · " + summary
        self.report = "\n".join(job.errors)
        if job.failed and self.panel_target is not None:
            self.panel_notice = {"key": (self.panel_target["binding"], self.panel_target["frame"]),
                                 "message": self.report or summary}
        self.job = None
        self.refresh()
        self.status(summary, "error" if job.failed else "warning" if job.cancelled else "success")
        self.update_actions()
        if self.panel_open:
            self.poll_panel()
            self.timer.Interval = 350
            self.timer.Start()

    def undo(self, ev=None):
        if self.job is not None or self.clear_open:
            return
        try:
            project, timeline, context = self.read_target()
            binding = project.GetUniqueId(), context["signature"]
            if not self.undo_changes or binding != self.undo_binding:
                raise RuntimeError("当前时间线没有可撤销的操作")
            job = MarkerJob.for_undo(timeline, self.undo_changes)
            self.start_job("撤销", timeline, job.operations, binding)
        except Exception as exc:
            self.status(str(exc), "error")

    def show_clear(self, ev=None):
        if self.job is not None or self.clear_open:
            return
        try:
            project, timeline, context = self.read_target()
            self.clear_binding = project.GetUniqueId(), context["signature"]
            self.clear_markers = self.read_markers(timeline)
            if not build_clear_plan(self.clear_markers, True)["operations"]:
                raise RuntimeError("当前时间线没有阅流批注标记")
            if self.clear_window is None:
                ui = self.ui
                self.clear_window = self.dispatcher.AddWindow({
                    "ID": "ReviewClearWindow", "WindowTitle": "清除批注", "Geometry": [320, 220, 430, 210], "Margin": 18,
                }, ui.VGroup({"Spacing": 12}, [
                    ui.Label({"ID": "ClearCount", "Text": "", "WordWrap": True}),
                    ui.CheckBox({"ID": "IncludeLegacy", "Text": "包含旧版同名标记", "Checked": False, "Weight": 0}),
                    ui.Label({"ID": "ClearWarning", "Text": "旧版仅按名称识别，同名手动标记也会清除。", "WordWrap": True, "Weight": 0}),
                    ui.HGroup({"Weight": 0, "Spacing": 8}, [
                        self.button("CancelClear", "取消"), ui.Label({"Text": "", "Weight": 1}), self.button("ConfirmClear", "清除"),
                    ]),
                ]))
                self.clear_window.On.IncludeLegacy.Toggled = self.update_clear_count
                self.clear_window.On.CancelClear.Clicked = self.close_clear
                self.clear_window.On.ConfirmClear.Clicked = self.confirm_clear
                self.clear_window.On.ReviewClearWindow.Close = self.close_clear
            self.clear_window.GetItems()["IncludeLegacy"].Checked = False
            self.clear_open = True
            self.window.Enabled = False
            if self.panel_open:
                self.poll_panel()
            self.update_clear_count()
            self.clear_window.Show()
            self.clear_window.Raise()
        except Exception as exc:
            self.status(str(exc), "error")

    def update_clear_count(self, ev=None):
        items = self.clear_window.GetItems()
        include = bool(items["IncludeLegacy"].Checked)
        count = len(build_clear_plan(self.clear_markers, include)["operations"])
        items["ClearCount"].Text = "清除 %d 个标记？" % count
        items["ConfirmClear"].Enabled = count > 0
        items["ClearWarning"].Visible = include
        self.clear_window.RecalcLayout()

    def close_clear(self, ev=None):
        if self.clear_window is not None:
            self.clear_window.Hide()
        self.clear_open = False
        self.window.Enabled = True
        if self.panel_open:
            self.poll_panel()

    def confirm_clear(self, ev=None):
        if self.job is not None:
            return
        try:
            project, timeline, context = self.read_target()
            binding = project.GetUniqueId(), context["signature"]
            if binding != self.clear_binding:
                self.close_clear()
                self.refresh()
                raise RuntimeError("时间线已变化，清除已取消")
            include = bool(self.clear_window.GetItems()["IncludeLegacy"].Checked)
            operations = build_clear_plan(self.clear_markers, include)["operations"]
            self.close_clear()
            if operations:
                self.start_job("清除", timeline, operations, binding)
        except Exception as exc:
            self.status(str(exc), "error")

    def show_details(self, ev=None):
        report = "\n\n".join(part for part in (self.report, self.preview_report) if part)
        if not report:
            return
        if self.details_window is None:
            self.details_window = self.dispatcher.AddWindow({
                "ID": "ReviewDetails", "WindowTitle": "执行结果", "Geometry": [280, 180, 600, 360], "Margin": 16,
            }, self.ui.VGroup({"Spacing": 10}, [
                self.ui.TextEdit({"ID": "ReportText", "ReadOnly": True, "AcceptRichText": False}), self.button("CloseDetails", "关闭"),
            ]))
            self.details_window.On.CloseDetails.Clicked = lambda ev: self.details_window.Hide()
            self.details_window.On.ReviewDetails.Close = lambda ev: self.details_window.Hide()
        self.details_window.GetItems()["ReportText"].PlainText = report
        self.details_window.Show()
        self.details_window.Raise()

    def close_main(self, ev=None):
        if self.clear_open:
            self.clear_window.Raise()
            return
        if self.job is not None:
            self.job.cancel()
            self.finish_job()
        self.main_open = False
        self.window.Hide()
        if not self.panel_open:
            self.dispatcher.ExitLoop()

    def run(self):
        try:
            self.window.Show()
            self.dispatcher.RunLoop()
        finally:
            self.timer.Stop()
            for window in (self.clear_window, self.details_window, self.panel, self.window):
                if window is not None:
                    window.Hide()


def load_host(namespace):
    host_bmd = namespace.get("bmd")
    resolve = namespace.get("resolve")
    if resolve is None and namespace.get("app") is not None:
        resolve = namespace["app"].GetResolve()
    if host_bmd is None:
        try:
            import DaVinciResolveScript as host_bmd
        except ImportError:
            if sys.platform == "darwin":
                path = Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules/DaVinciResolveScript.py")
            elif sys.platform.startswith("win"):
                path = Path("C:/ProgramData/Blackmagic Design/DaVinci Resolve/Support/Developer/Scripting/Modules/DaVinciResolveScript.py")
            else:
                path = Path("/opt/resolve/Developer/Scripting/Modules/DaVinciResolveScript.py")
            spec = importlib.util.spec_from_file_location("DaVinciResolveScript", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules["DaVinciResolveScript"] = module
            spec.loader.exec_module(module)
            host_bmd = sys.modules["DaVinciResolveScript"]
    resolve = resolve if resolve is not None else host_bmd.scriptapp("Resolve")
    if resolve is None:
        raise RuntimeError("请在 DaVinci Resolve 中运行此脚本")
    return resolve, host_bmd


def main(namespace=None):
    resolve, bmd = load_host(globals() if namespace is None else namespace)
    fusion = resolve.Fusion()
    ui = fusion.UIManager
    existing = ui.FindWindow(WINDOW_ID)
    if existing is not None:
        panel = ui.FindWindow("ReviewQuickPanel")
        if panel is not None:
            ui.QueueEvent(panel.GetItems()["ListBtn"], "Clicked", {})
        else:
            existing.Show()
            existing.Raise()
        return
    ReviewApp(resolve, ui, bmd.UIDispatcher(ui), fusion).run()


if __name__ == "__main__" or globals().get("app") is not None:
    main(globals())
