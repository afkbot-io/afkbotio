#!/usr/bin/env bash
set -euo pipefail

LEGACY_PATH_BLOCK_START="# >>> AFKBOT PATH >>>"
LEGACY_PATH_BLOCK_END="# <<< AFKBOT PATH <<<"

DRY_RUN="false"
YES="false"
PLATFORM=""
UV_BIN_DIR=""
UV_BIN=""
AFK_BIN_DIR=""
AFK_BIN=""
LEGACY_INSTALL_DIR=""

log() {
  printf '%s\n' "$1"
}

fail() {
  printf 'ERROR [uninstall_failed] %s\n' "$1" >&2
  exit 1
}

usage() {
  cat <<'USAGE'
Usage: scripts/uninstall.sh [options]

Options:
  --yes                 Skip confirmation prompt.
  --dry-run             Print actions without changing the machine.
  -h, --help            Show this help.
USAGE
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
      --yes)
        YES="true"
        shift
        ;;
      --dry-run)
        DRY_RUN="true"
        shift
        ;;
      --install-dir)
        fail "--install-dir is not supported by the uv tool uninstaller"
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
      PLATFORM="macos"
      LEGACY_INSTALL_DIR="${HOME}/Library/Application Support/AFKBOT"
      ;;
    Linux)
      PLATFORM="linux"
      LEGACY_INSTALL_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/afkbot"
      ;;
    *)
      fail "unsupported platform: $(uname -s)"
      ;;
  esac
  UV_BIN_DIR="$(default_user_bin_dir)"
  UV_BIN="${UV_BIN_DIR}/uv"
  AFK_BIN_DIR="${UV_BIN_DIR}"
  AFK_BIN="${AFK_BIN_DIR}/afk"
}

confirm() {
  if [[ "${YES}" == "true" ]]; then
    return 0
  fi
  if [[ ! -t 0 || ! -t 1 ]]; then
    fail "confirmation required; rerun with --yes"
  fi
  printf 'Remove AFKBOT tool install and local runtime state? [y/N] '
  read -r answer
  case "${answer}" in
    y|Y|yes|YES) ;;
    *) fail "uninstall cancelled" ;;
  esac
}

resolve_afk_bin_dir() {
  if [[ ! -x "${UV_BIN}" || "${DRY_RUN}" == "true" ]]; then
    printf '%s\n' "${AFK_BIN_DIR}"
    return 0
  fi
  "${UV_BIN}" tool dir --bin
}

clear_runtime_state() {
  if [[ ! -x "${AFK_BIN}" ]]; then
    return 0
  fi
  if ! run "${AFK_BIN}" uninstall --yes; then
    log "WARNING uninstall helper failed; continuing with tool cleanup."
  fi
}

remove_uv_tool() {
  if [[ ! -x "${UV_BIN}" ]]; then
    return 0
  fi
  if ! run "${UV_BIN}" tool uninstall afkbotio; then
    log "WARNING uv tool uninstall failed; continuing with legacy cleanup."
  fi
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
  temp_file="$(mktemp "${TMPDIR:-/tmp}/afkbot-uninstall.XXXXXX")"
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

remove_legacy_install_root() {
  if [[ ! -d "${LEGACY_INSTALL_DIR}" ]]; then
    return 0
  fi
  run rm -rf "${LEGACY_INSTALL_DIR}"
}

main() {
  parse_args "$@"
  detect_platform
  confirm
  AFK_BIN_DIR="$(resolve_afk_bin_dir)"
  AFK_BIN="${AFK_BIN_DIR}/afk"
  clear_runtime_state
  remove_uv_tool
  remove_legacy_path_blocks
  remove_legacy_cli_aliases
  remove_legacy_install_root
  log "AFKBOT uninstall complete."
}

main "$@"
