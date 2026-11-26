#!/usr/bin/env python3
"""Full-module Swift type-check under the REAL Xcode compile settings.

Why this exists
---------------
The per-feature harnesses in verify.py compile only a subset of the sources and
do NOT enable the project's `SWIFT_UPCOMING_FEATURE_MEMBER_IMPORT_VISIBILITY`.
That let a missing `import Combine` in OptionalPluginsView.swift slip through:
the real Xcode build (SwiftEmitModule) is the first place every source is
compiled together with MemberImportVisibility, and it failed with
"type 'PluginCenterModel' does not conform to protocol 'ObservableObject'".

This script type-checks EVERY app source with the same upcoming-feature set,
Debug (-D DEBUG) and Release (-O, no DEBUG), via the swiftc driver (which
configures macro plugins correctly). It never truncates sources and is not a
substitute for a full xcodebuild: it validates compilation/type-check only.

Requirements: Xcode 26.x and a resolved Sparkle.framework under
.build/Build/Products (produced by any prior build / package resolution).

    python3 scripts/verify_swift_typecheck.py

Writes docs/validation/swift-typecheck.json.
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "validation" / "swift-typecheck.json"

# Matches the -enable-upcoming-feature flags in the real xcodebuild
# swift-frontend command. The project additionally pins
# SWIFT_UPCOMING_FEATURE_MEMBER_IMPORT_VISIBILITY = YES in project.pbxproj;
# we assert that below so this list cannot silently drift from the project.
UPCOMING = [
    "DisableOutwardActorInference",
    "InferSendableFromCaptures",
    "GlobalActorIsolatedTypesUsability",
    "MemberImportVisibility",
    "InferIsolatedConformances",
    "NonisolatedNonsendingByDefault",
]

PLUGIN_DIRS = [
    pathlib.Path("/Applications/Xcode.app/Contents/Developer/Platforms/"
                 "MacOSX.platform/Developer/usr/lib/swift/host/plugins"),
    pathlib.Path("/Applications/Xcode.app/Contents/Developer/Toolchains/"
                 "XcodeDefault.xctoolchain/usr/lib/swift/host/plugins"),
]


def sources(configuration):
    files = sorted(str(p) for p in (ROOT / "Ordina").rglob("*.swift"))
    # Xcode-generated asset symbols, if a prior build produced them.
    derived = ROOT / ".build/Build/Intermediates.noindex/Ordina.build" / configuration / "Ordina.build/DerivedSources"
    for gen in sorted(derived.glob("GeneratedAssetSymbols.swift")):
        if str(gen) not in files:
            files.append(str(gen))
    return files


def sparkle_products():
    for cfg in ("Debug", "Release"):
        d = ROOT / ".build" / "Build" / "Products" / cfg
        if (d / "Sparkle.framework").is_dir():
            return d
    return None


def assert_project_settings():
    pbx = (ROOT / "Ordina.xcodeproj" / "project.pbxproj").read_text()
    count = len(re.findall(r"SWIFT_UPCOMING_FEATURE_MEMBER_IMPORT_VISIBILITY\s*=\s*YES", pbx))
    if count == 0:
        raise SystemExit("工程未开启 MEMBER_IMPORT_VISIBILITY，请核对 project.pbxproj")
    return count


def typecheck(srcs, prods, debug):
    cmd = ["/usr/bin/xcrun", "swiftc", "-typecheck",
           "-target", "arm64-apple-macos26.0", "-swift-version", "5"]
    for f in UPCOMING:
        cmd += ["-enable-upcoming-feature", f]
    cmd += ["-enable-bare-slash-regex"]
    cmd += ["-D", "DEBUG", "-Onone"] if debug else ["-O"]
    cmd += ["-F", str(prods), "-I", str(prods)]
    for p in PLUGIN_DIRS:
        if p.is_dir():
            cmd += ["-plugin-path", str(p)]
    cmd += srcs
    env = dict(os.environ)
    env.setdefault("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    env.setdefault("NO_PROXY", "localhost,127.0.0.1,::1")
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(ROOT))
    errors = [l for l in (r.stderr or "").splitlines() if "error:" in l.lower()]
    return r.returncode, errors


def main():
    pinned = assert_project_settings()
    prods = sparkle_products()
    if prods is None:
        raise SystemExit(
            "缺少 Sparkle.framework：请先完成一次 Xcode 包解析/构建"
            "（bash scripts/local_xcode_build.sh --debug），再运行本检查。")
    checks = {}
    details = {}
    ok = True
    source_counts = {}
    for label, debug in (("debug", True), ("release", False)):
        srcs = sources("Debug" if debug else "Release")
        source_counts[label] = len(srcs)
        rc, errors = typecheck(srcs, prods, debug)
        checks[label] = "pass" if rc == 0 and not errors else "fail"
        details[label] = {"returncode": rc, "errorCount": len(errors),
                          "errors": errors[:20]}
        ok &= (rc == 0 and not errors)
        print(f"  {label:8} rc={rc} errors={len(errors)} -> {checks[label]}")
        for e in errors[:10]:
            print("      " + e.strip())

    report = {
        "format": 1,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "passed": ok,
        "checks": checks,
        "sourceCount": source_counts,
        "upcomingFeatures": UPCOMING,
        "memberImportVisibilityPinnedInPbxproj": pinned,
        "sparkleProducts": str(prods),
        "method": "swiftc -typecheck (full module, real upcoming features)",
        "note": "编译/类型检查验证；完整 xcodebuild 需在 macOS 原生终端执行",
        "details": details,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    print(f"\n报告：{REPORT.relative_to(ROOT)}")
    print("Swift 全模块真实编译设置 typecheck：" + ("全部通过" if ok else "失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
