# ordina

A macOS companion for DaVinci Resolve that helps editors set up projects and keep project media organized.

[Download the latest release](https://github.com/BrOKenAG/ordina-releases/releases/latest) · [Report an issue](https://github.com/BrOKenAG/ordina-releases/issues) · [中文说明](https://github.com/BrOKenAG/ordina-releases/blob/main/README.md)

## What it does

- Create project folders from customizable templates on local drives, external drives, or mounted NAS volumes.
- Create and associate a DaVinci Resolve project in an existing local or PostgreSQL project library.
- Initialize project settings and media pool structure with the included Project Setup script.
- Register original footage folders and scan for media referenced from other locations. When automatic archiving is enabled, eligible files are copied and verified before Resolve references are updated. Original files are retained; some media types require manual review.
- Install bundled Resolve scripts or add Python scripts from your computer.
- Check for app updates from within ordina.

## Requirements

- Apple silicon Mac running macOS 26 or later.
- DaVinci Resolve Studio for Resolve integration and scripts. Folder creation works independently.
- Enable **External scripting using: Local** in Resolve's system preferences.
- The Resolve connection uses `/usr/bin/python3`; install Xcode Command Line Tools if macOS requests them.

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

Install scripts from ordina's plugin manager. Restart Resolve if a newly installed script does not appear in its menu.

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

## Feedback

ordina is under active development. Bug reports and descriptions of your editing workflow are welcome in [GitHub Issues](https://github.com/BrOKenAG/ordina-releases/issues). Please omit private project names and file paths when sharing logs or screenshots.
