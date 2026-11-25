#!/bin/bash
# ordina 真实 Xcode 构建（在本机「终端」App 或 Xcode 内运行）
#
# 为什么需要单独脚本：当前 agent/IDE 会话里 xcodebuild 的 SwiftPM
# "Resolve Package Graph" 阶段会调用 sandbox-exec，报
#   xcodebuild: error: Could not resolve package dependencies:
#     sandbox-exec: sandbox_apply: Operation not permitted   (exit 74)
# 本轮已按「优先申请正常本机执行权限」重试：关闭 agent 沙盒、以本机权限直接
# 运行 xcodebuild -list / -resolvePackageDependencies / build，仍稳定复现该错误。
# 诊断补充：
#   * 直接调用 /usr/bin/sandbox-exec -p '(version 1)(allow default)' 是成功的，
#     说明失败特定于 Xcode 26.3 / SwiftPM 的 package-graph sandbox profile；
#   * SWIFTPM_DISABLE_SANDBOX=1 曾让一次 -resolvePackageDependencies -skipPackageUpdates
#     短暂成功，但同一命令重复执行与正式 build 均再次失败，不能作为稳定方案；
#   * 本仓库没有独立 Package.swift（Sparkle 只在 pbxproj 中引用），无法绕开
#     xcodebuild 单独用 swift package resolve --disable-sandbox 预热。
# 因此最可靠的完整构建入口仍是 Xcode.app 图形界面（或本机原生「终端」跑本脚本），
# 而不要用 swiftc -typecheck、单独编译的 harness 或旧 App 替换来冒充完整 Xcode
# 构建：它们不产生可签名的 .app，也不验证 SPM 依赖与资源。
#
# 用法（在 macOS 原生终端）：
#   bash scripts/local_xcode_build.sh --deps       # 仅解析依赖
#   bash scripts/local_xcode_build.sh --debug      # Debug 完整构建
#   bash scripts/local_xcode_build.sh --release   # Release 构建 + 签名校验
#   bash scripts/local_xcode_build.sh --all       # 以上全部，并启动 App
#   bash scripts/local_xcode_build.sh --all --launch-isolated  # 用隔离环境启动
#
# 注意：真实 App 的「隔离验收」必须用 --debug 产物。lab 开关（隔离 Registry、
# 隔离公钥、隔离发布后端）由 `#if DEBUG || ORDINA_LAB` 控制，Release 构建会
# 编译剔除这些分支，App 会回落到正式 Pages / Registry / 生产公钥。
# dist/ordina.app 是正式 1.10.0 出包产物，只用于正式构建验证，不用于隔离验收。
#
# 所有输出同时写入 dist/build-logs/，失败时打印完整日志路径。
set -euo pipefail

WORKSPACE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="${WORKSPACE}/Ordina.xcodeproj"
SCHEME="Ordina"
DERIVED_DATA="${WORKSPACE}/.build"
LOG_DIR="${WORKSPACE}/dist/build-logs"
LOG_FILE="${LOG_DIR}/xcodebuild-$(date +%Y%m%d-%H%M%S).log"
DEBUG_APP="${DERIVED_DATA}/Build/Products/Debug/ordina.app"
RELEASE_APP="${WORKSPACE}/dist/ordina.app"

export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
mkdir -p "${LOG_DIR}"

log() { printf '%s\n' "$*" | tee -a "${LOG_FILE}"; }
run() {
  log ""
  log ">>> $*"
  if "$@" 2>&1 | tee -a "${LOG_FILE}"; then
    log "<<< 成功"
  else
    log "<<< 失败"
    log "完整日志：${LOG_FILE}"
    exit 1
  fi
}

MODE="--all"
LAUNCH_ISOLATED=0
for argument in "$@"; do
  case "${argument}" in
    --deps|--debug|--release|--all) MODE="${argument}" ;;
    --launch-isolated) LAUNCH_ISOLATED=1 ;;
    *) log "未知参数：${argument}"; exit 2 ;;
  esac
done

log "工作区：${WORKSPACE}"
log "Xcode 工程：${PROJECT}"
log "Scheme：${SCHEME}"
log "DerivedData：${DERIVED_DATA}"
log "日志：${LOG_FILE}"

if [[ ! -d "${PROJECT}" ]]; then
  log "找不到 Xcode 工程：${PROJECT}"
  exit 1
fi

# 1) 依赖解析（SwiftPM 会解析 Sparkle；这一步在非沙盒本机终端可正常执行）
if [[ "${MODE}" == "--deps" || "${MODE}" == "--all" ]]; then
  run xcodebuild -resolvePackageDependencies \
      -project "${PROJECT}" -scheme "${SCHEME}" \
      -derivedDataPath "${DERIVED_DATA}"
fi

# 2) Debug 完整构建
if [[ "${MODE}" == "--debug" || "${MODE}" == "--all" ]]; then
  run xcodebuild -project "${PROJECT}" -scheme "${SCHEME}" \
      -configuration Debug \
      -destination 'platform=macOS,arch=arm64' \
      -derivedDataPath "${DERIVED_DATA}" \
      CODE_SIGNING_ALLOWED=NO build
  log "Debug 产物：${DEBUG_APP}"
fi

# 3) Release 完整构建（build_app.py 内含签名、资源与 Sparkle 嵌套组件校验）
if [[ "${MODE}" == "--release" || "${MODE}" == "--all" ]]; then
  run /usr/bin/python3 "${WORKSPACE}/scripts/build_app.py" \
      --output "${RELEASE_APP}"
  log "Release 产物：${RELEASE_APP}"
fi

# 4) 实际 App 启动方式
if [[ "${MODE}" == "--all" ]]; then
  if [[ "${LAUNCH_ISOLATED}" == "1" ]]; then
    log "隔离验收启动请使用 scripts/prepare_app_acceptance.py 生成的 launch.sh"
    log "（它会设置临时 HOME 与隔离 Registry，不接触真实插件数据）"
  else
    log "启动 Release App：open ${RELEASE_APP}"
    open "${RELEASE_APP}" || log "open 失败；也可直接运行 ${RELEASE_APP}/Contents/MacOS/ordina"
  fi
fi

log ""
log "完成。完整日志：${LOG_FILE}"
