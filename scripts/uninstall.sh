#!/usr/bin/env bash
set -euo pipefail

PATH_BLOCK_START="# >>> AFKBOT PATH >>>"
PATH_BLOCK_END="# <<< AFKBOT PATH <<<"

DRY_RUN="false"
YES="false"
INSTALL_DIR=""
BIN_DIR=""

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
  --install-dir <path>  Managed install root to remove.
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
      --install-dir)
        [[ $# -ge 2 ]] || fail "--install-dir requires a value"
        INSTALL_DIR="$2"
        shift 2
        ;;
      --yes)
        YES="true"
        shift
        ;;
      --dry-run)
        DRY_RUN="true"
        shift
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

detect_platform() {
  case "$(uname -s)" in
    Darwin)
      [[ -n "${INSTALL_DIR}" ]] || INSTALL_DIR="${HOME}/Library/Application Support/AFKBOT"
      ;;
    Linux)
      [[ -n "${INSTALL_DIR}" ]] || INSTALL_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/afkbot"
      ;;
    *)
      fail "unsupported platform: $(uname -s)"
      ;;
  esac
  BIN_DIR="${INSTALL_DIR}/bin"
}

remove_cli_aliases() {
  local candidate="" dir="" seen=""
  local -a path_dirs=()
  local -a extra_dirs=("${HOME}/.local/bin" "${HOME}/bin" "/usr/local/bin" "/opt/homebrew/bin")
  IFS=':' read -r -a path_dirs <<< "${PATH:-}"
  for dir in "${path_dirs[@]}" "${extra_dirs[@]}"; do
    [[ -n "${dir}" ]] || continue
    case " ${seen} " in
      *" ${dir} "*) continue ;;
    esac
    seen="${seen} ${dir}"
    candidate="${dir}/afk"
    [[ -L "${candidate}" ]] || continue
    if [[ "$(readlink "${candidate}" 2>/dev/null || true)" != "${BIN_DIR}/afk" ]]; then
      continue
    fi
    if [[ "${DRY_RUN}" == "true" ]]; then
      log "[dry-run] remove CLI alias ${candidate}"
      continue
    fi
    rm -f "${candidate}"
  done
}

confirm() {
  if [[ "${YES}" == "true" ]]; then
    return 0
  fi
  if [[ ! -t 0 || ! -t 1 ]]; then
    fail "confirmation required; rerun with --yes"
  fi
  printf 'Remove AFKBOT install at %s? [y/N] ' "${INSTALL_DIR}"
  read -r answer
  case "${answer}" in
    y|Y|yes|YES) ;;
    *) fail "uninstall cancelled" ;;
  esac
}

remove_path_block_from_file() {
  local target="$1"
  [[ -f "${target}" ]] || return 0
  if ! grep -Fq "${PATH_BLOCK_START}" "${target}"; then
    return 0
  fi
  if [[ "${DRY_RUN}" == "true" ]]; then
    log "[dry-run] remove AFKBOT PATH block from ${target}"
    return 0
  fi
  local temp_file
  temp_file="$(mktemp "${TMPDIR:-/tmp}/afkbot-uninstall.XXXXXX")"
  awk -v start="${PATH_BLOCK_START}" -v end="${PATH_BLOCK_END}" '
    $0 == start {skip=1; next}
    $0 == end {skip=0; next}
    skip != 1 {print}
  ' "${target}" >"${temp_file}"
  mv "${temp_file}" "${target}"
}

remove_path_block() {
  remove_path_block_from_file "${HOME}/.profile"
  remove_path_block_from_file "${HOME}/.bashrc"
  remove_path_block_from_file "${HOME}/.bash_profile"
  remove_path_block_from_file "${HOME}/.zshrc"
  remove_path_block_from_file "${HOME}/.zprofile"
}

stop_managed_service() {
  case "$(uname -s)" in
    Darwin)
      local plist="${HOME}/Library/LaunchAgents/io.afkbot.afkbot.plist"
      [[ -f "${plist}" ]] || return 0
      if [[ "${DRY_RUN}" == "true" ]]; then
        log "[dry-run] unload managed launchd service ${plist}"
        return 0
      fi
      local uid_value
      uid_value="$(id -u)"
      launchctl bootout "gui/${uid_value}" "${plist}" >/dev/null 2>&1 || \
        launchctl bootout "user/${uid_value}" "${plist}" >/dev/null 2>&1 || true
      ;;
    Linux)
      [[ -f "/etc/systemd/system/afkbot.service" ]] || return 0
      if [[ "${DRY_RUN}" == "true" ]]; then
        log "[dry-run] stop managed systemd service afkbot.service"
        return 0
      fi
      systemctl stop afkbot.service >/dev/null 2>&1 || \
        sudo -n env "PATH=${PATH:-}" systemctl stop afkbot.service >/dev/null 2>&1 || true
      ;;
  esac
}

clear_runtime_state() {
  local launcher="${BIN_DIR}/afk"
  if [[ ! -x "${launcher}" ]]; then
    return 0
  fi
  if ! run "${launcher}" uninstall --yes; then
    log "WARNING uninstall helper failed; continuing with filesystem cleanup."
  fi
}

remove_install_root() {
  if [[ ! -d "${INSTALL_DIR}" ]]; then
    return 0
  fi
  run rm -rf "${INSTALL_DIR}"
}

main() {
  parse_args "$@"
  detect_platform
  confirm
  stop_managed_service
  clear_runtime_state
  remove_cli_aliases
  remove_path_block
  remove_install_root
  log "AFKBOT uninstall complete."
}

main "$@"
