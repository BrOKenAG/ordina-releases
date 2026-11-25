# ordina

[English](README.en.md) · [中文](README.md)

A macOS companion for DaVinci Resolve that helps editors set up projects and keep project media organized.

[Download the latest release](https://github.com/BrOKenAG/ordina-releases/releases/latest) · [Report an issue](https://github.com/BrOKenAG/ordina-releases/issues)

## What it does

- Create project folders from customizable templates on local drives, external drives, or mounted NAS volumes.
- Create and associate a DaVinci Resolve project in an existing local or PostgreSQL project library.
- Initialize project settings and media pool structure with the included Project Setup script.
- Register original footage folders and scan for media referenced from other locations. When automatic archiving is enabled, eligible files are copied and verified before Resolve references are updated. Original files are retained; some media types require manual review.
- Install bundled Resolve scripts or add Python scripts from your computer.
- Check for app updates from within ordina.
- Update plugin bundles independently of the app: Minutes Roughcut can be updated without installing a new ordina release.

## Requirements

- Apple silicon Mac running macOS 26 or later.
- DaVinci Resolve Studio for Resolve integration and scripts. Folder creation works independently.
- Enable **External scripting using: Local** in Resolve's system preferences.
- The Resolve connection uses `/usr/bin/python3`; install Xcode Command Line Tools if macOS requests them.
- Minutes Roughcut additionally requires Google Chrome; sending ranges directly from the browser needs an open Resolve project.

## Install and get started

1. Download the DMG from [Releases](https://github.com/BrOKenAG/ordina-releases/releases/latest) and drag ordina to Applications.
2. Choose a default project location and, if needed, an existing Resolve project library. Install the scripts you want to use; you can also configure these later in Settings.
3. Enter a project name, select a folder template and location, and optionally create a Resolve project and register original footage folders.
4. Open **Project Setup** from Resolve's **Workspace → Scripts → Utility** menu to initialize the project or view archive status.

Before creating a Resolve project, switch to the intended library and save and close your current project in Resolve. ordina will attempt to open Project Setup after creating the new project.

The current release is ad-hoc signed and has not been notarized by Apple. On first launch, macOS may require you to confirm opening it in **System Settings → Privacy & Security**.

## Included scripts

| Script | Purpose |
| --- | --- |
| Project Setup | Set resolution and frame rate, initialize media pool folders and a frame-rate-locking timeline, and view archive status. |
| Import YueLiu | Import review comments exported from YueLiu. Requires `openpyxl` in Resolve's Python environment. |
| Multicam Tools | Assist with camera-angle naming and multicamera assembly. |
| Minutes Roughcut | Send the ranges you keep in a Feishu Minutes transcript to Resolve and build a rough-cut timeline. Requires a Chrome extension. |

Install or update scripts from ordina's plugin manager. Open them from Resolve's **Workspace → Scripts → Utility** menu; restart Resolve if a newly installed script does not appear. Import YueLiu needs `openpyxl` in Resolve's Python environment. Minutes Roughcut also includes a browser extension; see below.

The plugin list comes from a signed registry rather than being hardcoded in the app. Adding a plugin only requires publishing its repository and updating the registry: an already-shipped ordina discovers, displays, installs, and updates it without rebuilding or re-releasing the client. See [docs/plugin-distribution.md](docs/plugin-distribution.md) for the descriptor fields and release flow.

Releasing a plugin needs no shell: a hidden developer centre in the test options accepts a ZIP, runs a local test, previews the exact changes and publishes through the durable transaction. The publishing engine is deployed separately from the app and can be updated and rolled back on its own. See [docs/developer-center.md](docs/developer-center.md).

## Minutes Roughcut

Minutes Roughcut sends the ranges you keep in a Feishu Minutes transcript to DaVinci Resolve and builds a rough-cut timeline from them. It ships as a Resolve script, a Chrome extension, and a local messaging host.

**Install.** Install Minutes Roughcut in **Settings → Plugin Manager**, then choose **Prepare Browser Extension**. ordina copies the extension folder path to the clipboard, opens `chrome://extensions` in Chrome, and reveals the folder in Finder. In Chrome, enable **Developer mode**, choose **Load unpacked**, and press ⌘⇧G to paste the copied path. Chrome does not allow third-party apps to silently install unpacked extensions, so these two steps cannot be automated.

**Use.** Open a Feishu Minutes page and choose **Send to Resolve** in the extension while a Resolve project is open. You can also export a JSON file and import it from **Workspace → Scripts → Minutes Roughcut** in Resolve. Requires macOS, Chrome, and DaVinci Resolve Studio.

**Updates.** Minutes Roughcut is distributed from its own signed release repository, separate from ordina app updates. The plugin manager checks for online versions and installs them directly, without a new ordina release.

> This plugin is experimental. Building the rough cut recreates the timeline and may not preserve some effects, grades, or links; try it on a project copy and verify the source media and start timecode first. Changes to Feishu pages or internal APIs can affect transcript extraction. Installation does not upload media; replaced files are backed up under `~/Library/Application Support/Minutes Roughcut/backups`.

## Media archiving

In Project Setup's archive status window, you can register original footage locations, scan manually, enable automatic archiving, and adjust the scan interval. The default background scan interval is five seconds. Registered original footage remains referenced at its existing location. Automatic processing only handles eligible media when its safety conditions are met; image sequences and other complex media may require manual attention.

## Build from source

Requires Xcode 26 or later, Xcode Command Line Tools, and Python 3. Swift Package Manager downloads the pinned Sparkle dependency.

```sh
git clone https://github.com/BrOKenAG/ordina-releases.git ordina
cd ordina
python3 scripts/build_app.py --output dist/ordina.app
```

You can also open `Ordina.xcodeproj` in Xcode and select the `Ordina` scheme. Run the source verification suite with `python3 scripts/verify.py`.

The build script uses standard `xcodebuild` and must run from a local, non-sandboxed terminal (or from Xcode itself). SwiftPM resolves package dependencies through `sandbox-exec`, which is denied in sandboxed automation environments (`sandbox_apply: Operation not permitted`). This is an environment permission limit, not a project configuration issue; check a terminal first with `/usr/bin/sandbox-exec -p '(version 1)(allow default)' /bin/echo OK`.

## Project documentation

- [Build and release workflow](docs/releases.md) — standard Xcode build, transactional publishing, checks, retries and rollback.
- [Plugin distribution architecture](docs/plugin-distribution.md) — per-plugin repositories, the signed registry and client compatibility.
- [Developer centre](docs/developer-center.md) — the ZIP contract, the JSON publishing protocol and the independently versioned publishing tool.

## Feedback

ordina is under active development. Bug reports and descriptions of your editing workflow are welcome in [GitHub Issues](https://github.com/BrOKenAG/ordina-releases/issues). Please omit private project names and file paths when sharing logs or screenshots.
