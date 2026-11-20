import json
import sys


class BridgeError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise BridgeError(message)


def database_key(database):
    return {key: database.get(key, "") for key in ("DbType", "DbName", "IpAddress")}


def require_available_project(manager):
    current = manager.GetCurrentProject()
    if current is None:
        return
    name = current.GetName()
    message = "请先在 Resolve 中保存并关闭「" + (name or "当前工程") + "」，再重试。"
    require(name == "Untitled Project" and current.GetTimelineCount() == 0, message)
    media_pool = current.GetMediaPool()
    root = media_pool.GetRootFolder() if media_pool else None
    require(root is not None, message)
    require(root.GetClipList() == [] and root.GetSubFolderList() == [], message)
    saved_projects = manager.GetProjectListInCurrentFolder()
    require(isinstance(saved_projects, list) and name not in saved_projects, message)


def open_result(resolve, project, show_setup):
    identity = project.GetUniqueId()
    if show_setup:
        from pathlib import Path
        script = Path(__file__).with_name("Project Setup.py").resolve()
        require(script.is_file(), "缺少 Project Setup 资源。")
        dispatched = resolve.Fusion().RunScript(str(script), {"OrdoExpectedProjectID": identity})
        require(dispatched is not False, "无法打开 Project Setup。")
    return {"opened": True, "projectID": identity}


def run(resolve, request):
    require(resolve is not None, "无法连接 Resolve，请启动 Studio 并将外部脚本使用设为「本地」。")
    if request["action"] == "archive_scan":
        from archive_service import support_directory
        from archive_worker import scan_once
        return scan_once(resolve, dict(request, _scanStateDirectory=str(support_directory())))
    if request["action"] in {"archive_execute", "storage_update"}:
        from archive_plan import run as archive_request
        from archive_service import support_directory
        from archive_worker import scan_lock
        with scan_lock(support_directory()):
            return archive_request(resolve, request)
    manager = resolve.GetProjectManager()
    database = manager.GetCurrentDatabase()
    if request["action"] in {"open", "open_new_project"}:
        from pathlib import Path
        root = Path(request["projectRoot"]).resolve(strict=True)
        metadata = json.loads((root / ".magic-resolve.json").read_text(encoding="utf-8"))
        require(database_key(database) == database_key(metadata.get("database", {})), "请先在 Resolve 切换到此工程的项目库。")
        current = manager.GetCurrentProject()
        if current is not None and current.GetUniqueId() == metadata.get("projectID"):
            return open_result(resolve, current, request["action"] == "open_new_project")
        require_available_project(manager)
        folders = metadata.get("folders")
        require(isinstance(folders, list) and bool(folders) and all(isinstance(folder, str) and folder not in {"", ".", ".."} and "/" not in folder for folder in folders), "此关联未记录工程路径，请在 Resolve 中手动打开。")
        original = []
        while True:
            folder = manager.GetCurrentFolder()
            if not manager.GotoParentFolder():
                break
            original.insert(0, folder)
        try:
            require(manager.GotoRootFolder(), "无法打开项目库根目录。")
            for folder in folders:
                require(manager.OpenFolder(folder), "未找到工程文件夹：" + folder)
            opened = manager.LoadProject(metadata["projectName"])
            require(opened, "未找到 Resolve 工程。")
            require(opened.GetUniqueId() == metadata.get("projectID"), "工程标识与关联记录不一致，请在 Resolve 检查；未修改工程。")
        finally:
            if manager.GotoRootFolder():
                for folder in original:
                    if not manager.OpenFolder(folder):
                        break
        return open_result(resolve, opened, request["action"] == "open_new_project")
    if request["action"] == "bind":
        from pathlib import Path
        import subprocess
        from media_archive import write_json
        root = Path(request["projectRoot"]).resolve(strict=True)
        require(root.is_dir(), "请选择可访问的项目目录。")
        current = manager.GetCurrentProject()
        require(current is not None and bool(current.GetName()) and bool(current.GetUniqueId()) and current.GetName() != 'Untitled Project', "请先打开要关联的工程。")
        metadata = {"projectID": current.GetUniqueId(), "database": database, "projectName": current.GetName()}
        manifest = root / '.magic-resolve.json'
        if manifest.exists():
            previous = json.loads(manifest.read_text(encoding='utf-8'))
            require(previous.get('projectID') == metadata['projectID'] and database_key(previous.get('database', {})) == database_key(database), "此目录已关联其他工程，请选择正确目录。")
            previous.update(metadata)
            metadata = previous
        current_now = manager.GetCurrentProject()
        require(current_now is not None and current_now.GetUniqueId() == metadata['projectID'] and database_key(manager.GetCurrentDatabase()) == database_key(database), "工程已切换，请重新关联。")
        write_json(manifest, metadata)
        return metadata
    if request["action"] == "probe":
        return {"database": database, "databases": manager.GetDatabaseList() or [],
                "currentProject": manager.GetCurrentProject().GetName() if manager.GetCurrentProject() else None}
    require(database_key(database) == database_key(request["database"]), "当前项目库与所选项目库不同，请在 Resolve 中切换后重试。")
    require(database.get("DbType") in {"PostgreSQL", "Disk"}, "请选择 Resolve 已登记的本地或网络项目库。")
    require_available_project(manager)
    components = request["folders"]
    project_name = request["projectName"]
    require(len(components) == 3 and all(isinstance(name, str) and name and "/" not in name for name in components), "项目路径无效。")
    require(project_name and "/" not in project_name, "工程名称无效。")
    original = []
    while True:
        folder = manager.GetCurrentFolder()
        if not manager.GotoParentFolder():
            break
        original.insert(0, folder)
    try:
        require(manager.GotoRootFolder(), "无法打开项目库根目录。")
        for component in components:
            folders = manager.GetFolderListInCurrentFolder() or []
            if component not in folders:
                if request["action"] == "preflight":
                    return {"ready": True}
                require(manager.CreateFolder(component), "无法创建项目库文件夹：" + component)
            require(manager.OpenFolder(component), "无法打开项目库文件夹：" + component)
        require(not (manager.GetProjectListInCurrentFolder() or []), "此项目文件夹已有工程，请检查后重试。")
        if request["action"] == "preflight":
            return {"ready": True}
        require(request["action"] == "create", "未知操作。")
        created = manager.CreateProject(project_name)
        require(created, "创建工程失败，请检查项目库后重试。")
        require(manager.SaveProject(), "工程已创建，但保存未确认。请在 Resolve 中检查，勿重复创建。")
        identity = created.GetUniqueId()
        require(manager.CloseProject(created), "工程已创建并保存，请在 Resolve 中检查。")
        return {"projectID": identity, "database": database, "folders": components, "projectName": project_name}
    finally:
        if manager.GotoRootFolder():
            for component in original:
                if not manager.OpenFolder(component):
                    break


def main():
    sys.dont_write_bytecode = True
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        sys.path.insert(0, "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules")
        import DaVinciResolveScript as api
        response = {"ok": True, "data": run(api.scriptapp("Resolve"), json.load(sys.stdin))}
    except Exception as error:
        response = {"ok": False, "error": str(error)}
    print("MAGIC_RESOLVE:" + json.dumps(response, ensure_ascii=False))


if __name__ == "__main__":
    main()
