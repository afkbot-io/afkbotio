#!/usr/bin/env bash
set -euo pipefail

PROGRAM_NAME="AFKBOT"
DEFAULT_REPO_URL="${AFKBOT_INSTALL_REPO_URL:-https://github.com/afkbot-io/afkbotio.git}"
DEFAULT_GIT_REF="${AFKBOT_INSTALL_GIT_REF:-main}"
UV_INSTALL_URL="${AFKBOT_UV_INSTALL_URL:-https://astral.sh/uv/install.sh}"
LEGACY_PATH_BLOCK_START="# >>> AFKBOT PATH >>>"
LEGACY_PATH_BLOCK_END="# <<< AFKBOT PATH <<<"

DRY_RUN="false"
SKIP_SETUP="false"
REPO_URL="${DEFAULT_REPO_URL}"
GIT_REF="${DEFAULT_GIT_REF}"
ORIGINAL_PATH="${PATH:-}"
UV_BIN_DIR=""
UV_BIN=""
AFK_BIN_DIR=""
AFK_BIN=""
TOOL_SOURCE=""
TOOL_INSTALL_MODE=""
LEGACY_INSTALL_DIR=""

log() {
  printf '%s\n' "$1"
}

warn() {
  printf 'WARNING %s\n' "$1" >&2
}

fail() {
  printf 'ERROR [install_failed] %s\n' "$1" >&2
  exit 1
}

usage() {
  cat <<'USAGE'
Usage: scripts/install.sh [options]

Options:
  --repo-url <url>      GitHub repo URL or local source path to install.
  --git-ref <ref>       Branch or tag to install from a remote repo. Default: main.
  --skip-setup          Skip `afk setup --bootstrap-only --yes`.
  --dry-run             Print actions without changing the machine.
  -h, --help            Show this help.

This installer:
- bootstraps uv into the user-local executable directory if needed;
- installs AFKBOT as an isolated uv tool;
- asks uv to add the tool bin directory to your shell PATH;
- seeds the runtime root with `afk setup --bootstrap-only --yes`.
USAGE
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

run() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    printf '[dry-run] '
    printf '%q ' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --repo-url)
        [[ $# -ge 2 ]] || fail "--repo-url requires a value"
        REPO_URL="$2"
        shift 2
        ;;
      --git-ref)
        [[ $# -ge 2 ]] || fail "--git-ref requires a value"
        GIT_REF="$2"
        shift 2
        ;;
      --skip-setup)
        SKIP_SETUP="true"
        shift
        ;;
      --dry-run)
        DRY_RUN="true"
        shift
        ;;
      --install-dir)
        [[ $# -ge 2 ]] || fail "--install-dir requires a value"
        warn "--install-dir is ignored by the uv tool installer"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        fail "unknown argument: $1"
        ;;
    esac
  done
}

default_user_bin_dir() {
  if [[ -n "${XDG_BIN_HOME:-}" ]]; then
    printf '%s\n' "${XDG_BIN_HOME}"
    return 0
  fi
  if [[ -n "${XDG_DATA_HOME:-}" ]]; then
    printf '%s\n' "${XDG_DATA_HOME}/../bin"
    return 0
  fi
  printf '%s\n' "${HOME}/.local/bin"
}

detect_platform() {
  case "$(uname -s)" in
    Darwin)
      LEGACY_INSTALL_DIR="${HOME}/Library/Application Support/AFKBOT"
      ;;
    Linux)
      LEGACY_INSTALL_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/afkbot"
      ;;
    *)
      fail "unsupported platform: $(uname -s)"
      ;;
  esac
  UV_BIN_DIR="$(default_user_bin_dir)"
  UV_BIN="${UV_BIN_DIR}/uv"
}

require_downloader() {
  if command_exists curl || command_exists wget; then
    return 0
  fi
  fail "curl or wget is required"
}

download_to_file() {
  local url="$1"
  local output="$2"
  require_downloader
  if command_exists curl; then
    run curl -fsSL --retry 3 --retry-delay 1 -o "${output}" "${url}"
    return
  fi
  run wget -qO "${output}" "${url}"
}

ensure_uv() {
  if [[ -x "${UV_BIN}" ]]; then
    return 0
  fi
  run mkdir -p "${UV_BIN_DIR}"
  if [[ "${DRY_RUN}" == "true" ]]; then
    log "[dry-run] install uv to ${UV_BIN_DIR}"
    return 0
  fi
  local temp_script
  temp_script="$(mktemp "${TMPDIR:-/tmp}/afkbot-uv.XXXXXX")"
  download_to_file "${UV_INSTALL_URL}" "${temp_script}"
  env UV_UNMANAGED_INSTALL="${UV_BIN_DIR}" sh "${temp_script}"
  rm -f "${temp_script}"
  [[ -x "${UV_BIN}" ]] || fail "uv installation completed but ${UV_BIN} was not created"
}

resolve_local_source_path() {
  case "${REPO_URL}" in
    file://*)
      printf '%s\n' "${REPO_URL#file://}"
      return 0
      ;;
  esac
  if [[ -d "${REPO_URL}" ]]; then
    printf '%s\n' "${REPO_URL}"
    return 0
  fi
  return 1
}

build_tool_source() {
  local local_source="" normalized=""
  if local_source="$(resolve_local_source_path)"; then
    TOOL_INSTALL_MODE="editable"
    TOOL_SOURCE="${local_source}"
    return 0
  fi

  normalized="${REPO_URL}"
  case "${normalized}" in
    git@github.com:*)
      normalized="https://github.com/${normalized#git@github.com:}"
      ;;
    http://github.com/*)
      normalized="https://github.com/${normalized#http://github.com/}"
      ;;
    https://www.github.com/*)
      normalized="https://github.com/${normalized#https://www.github.com/}"
      ;;
    http://www.github.com/*)
      normalized="https://github.com/${normalized#http://www.github.com/}"
      ;;
  esac
  normalized="${normalized%.git}"
  normalized="${normalized%/}"
  case "${normalized}" in
    https://github.com/*)
      TOOL_INSTALL_MODE="archive"
      TOOL_SOURCE="${normalized}/archive/${GIT_REF}.tar.gz"
      return 0
      ;;
  esac
  fail "installer supports a local source path or a GitHub repository URL"
}

remove_path_block_from_file() {
  local target="$1"
  [[ -f "${target}" ]] || return 0
  if ! grep -Fq "${LEGACY_PATH_BLOCK_START}" "${target}"; then
    return 0
  fi
  if [[ "${DRY_RUN}" == "true" ]]; then
    log "[dry-run] remove legacy AFKBOT PATH block from ${target}"
    return 0
  fi
  local temp_file
  temp_file="$(mktemp "${TMPDIR:-/tmp}/afkbot-install.XXXXXX")"
  awk -v start="${LEGACY_PATH_BLOCK_START}" -v end="${LEGACY_PATH_BLOCK_END}" '
    $0 == start {skip=1; next}
    $0 == end {skip=0; next}
    skip != 1 {print}
  ' "${target}" >"${temp_file}"
  mv "${temp_file}" "${target}"
}

remove_legacy_path_blocks() {
  remove_path_block_from_file "${HOME}/.profile"
  remove_path_block_from_file "${HOME}/.bashrc"
  remove_path_block_from_file "${HOME}/.bash_profile"
  remove_path_block_from_file "${HOME}/.zshrc"
  remove_path_block_from_file "${HOME}/.zprofile"
}

remove_legacy_cli_aliases() {
  local candidate="" dir="" target=""
  local -a path_dirs=()
  local -a extra_dirs=("${HOME}/.local/bin" "${HOME}/bin" "/usr/local/bin" "/opt/homebrew/bin")
  IFS=':' read -r -a path_dirs <<< "${PATH:-}"
  for dir in "${path_dirs[@]}" "${extra_dirs[@]}"; do
    [[ -n "${dir}" ]] || continue
    candidate="${dir}/afk"
    [[ -L "${candidate}" ]] || continue
    target="$(readlink "${candidate}" 2>/dev/null || true)"
    if [[ "${target}" != "${LEGACY_INSTALL_DIR}/bin/afk" ]]; then
      continue
    fi
    if [[ "${DRY_RUN}" == "true" ]]; then
      log "[dry-run] remove legacy AFKBOT alias ${candidate}"
      continue
    fi
    rm -f "${candidate}"
  done
}

install_tool() {
  if [[ "${TOOL_INSTALL_MODE}" == "editable" ]]; then
    run "${UV_BIN}" tool install --python 3.12 --reinstall --editable "${TOOL_SOURCE}"
    return 0
  fi
  run "${UV_BIN}" tool install --python 3.12 --reinstall "${TOOL_SOURCE}"
}

resolve_afk_bin_dir() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    printf '%s\n' "${UV_BIN_DIR}"
    return 0
  fi
  "${UV_BIN}" tool dir --bin
}

ensure_shell_integration() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    log "[dry-run] ${UV_BIN} tool update-shell"
    return 0
  fi
  if "${UV_BIN}" tool update-shell; then
    return 0
  fi
  log "WARNING uv tool update-shell failed; reopen your shell after install if \`afk\` is not yet visible."
}

run_bootstrap_setup() {
  if [[ "${SKIP_SETUP}" == "true" ]]; then
    return 0
  fi
  run "${AFK_BIN}" setup --bootstrap-only --yes
}

path_contains() {
  local target="$1"
  local path_env="${2:-${PATH:-}}"
  case ":${path_env}:" in
    *":${target}:"*) return 0 ;;
    *) return 1 ;;
  esac
}

print_success() {
  log ""
  log "${PROGRAM_NAME} install complete."
  log "Tool source: ${TOOL_SOURCE}"
  log "uv: ${UV_BIN}"
  log "CLI: ${AFK_BIN}"
  log ""
  if ! path_contains "${AFK_BIN_DIR}" "${ORIGINAL_PATH}"; then
    log "To use \`afk\` in this terminal immediately, run:"
    log "  export PATH=\"${AFK_BIN_DIR}:\$PATH\" && hash -r"
    log ""
    log "Or reopen the terminal to pick up the updated shell profile."
    log ""
  fi
  log "Next steps:"
  log "  afk setup"
  log "  afk doctor"
  log "  afk chat"
  log ""
  log "To update later, run \`afk update\` or \`uv tool upgrade afkbotio --reinstall\`."
}

main() {
  parse_args "$@"
  detect_platform
  ensure_uv
  build_tool_source
  install_tool
  ensure_shell_integration
  AFK_BIN_DIR="$(resolve_afk_bin_dir)"
  AFK_BIN="${AFK_BIN_DIR}/afk"
  export PATH="${AFK_BIN_DIR}:${UV_BIN_DIR}:${PATH:-}"
  run_bootstrap_setup
  remove_legacy_path_blocks
  remove_legacy_cli_aliases
  print_success
}

main "$@"
