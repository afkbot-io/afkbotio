#!/usr/bin/env bash
set -euo pipefail

PROGRAM_NAME="AFKBOT"
DEFAULT_REPO_URL="${AFKBOT_INSTALL_REPO_URL:-https://github.com/afkbot-io/afkbotio.git}"
DEFAULT_GIT_REF="${AFKBOT_INSTALL_GIT_REF:-main}"
UV_INSTALL_URL="${AFKBOT_UV_INSTALL_URL:-https://astral.sh/uv/install.sh}"
PATH_BLOCK_START="# >>> AFKBOT PATH >>>"
PATH_BLOCK_END="# <<< AFKBOT PATH <<<"

DRY_RUN="false"
SKIP_SETUP="false"
REPO_URL="${DEFAULT_REPO_URL}"
GIT_REF="${DEFAULT_GIT_REF}"
INSTALL_DIR=""
PLATFORM=""
APP_ROOT_DIR=""
RUNTIME_DIR=""
VENV_DIR=""
UV_BIN=""
BIN_DIR=""
AFK_LAUNCHER_PATH=""
CURRENT_APP_DIR=""
ORIGINAL_PATH="${PATH:-}"
PROFILE_PATH=""
CLI_ALIAS_PATH=""

log() {
  printf '%s\n' "$1"
}

fail() {
  printf 'ERROR [install_failed] %s\n' "$1" >&2
  exit 1
}

usage() {
  cat <<'USAGE'
Usage: scripts/install.sh [options]

Options:
  --install-dir <path>  Install root. Defaults to a user-local AFKBOT directory.
  --repo-url <url>      GitHub repo URL or local source path to install.
  --git-ref <ref>       Branch or tag to install from a remote repo. Default: main.
  --skip-setup          Skip `afk setup --bootstrap-only --yes`.
  --dry-run             Print actions without changing the machine.
  -h, --help            Show this help.

The installer is idempotent:
- first run installs uv, Python 3.12, a managed source snapshot, and a venv;
- later runs stage a fresh source snapshot, reinstall the CLI, and keep runtime state in place.
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
      --install-dir)
        [[ $# -ge 2 ]] || fail "--install-dir requires a value"
        INSTALL_DIR="$2"
        shift 2
        ;;
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
      PLATFORM="macos"
      [[ -n "${INSTALL_DIR}" ]] || INSTALL_DIR="${HOME}/Library/Application Support/AFKBOT"
      ;;
    Linux)
      PLATFORM="linux"
      [[ -n "${INSTALL_DIR}" ]] || INSTALL_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/afkbot"
      ;;
    *)
      fail "unsupported platform: $(uname -s)"
      ;;
  esac

  APP_ROOT_DIR="${INSTALL_DIR}/app"
  RUNTIME_DIR="${INSTALL_DIR}/runtime"
  VENV_DIR="${INSTALL_DIR}/venv"
  UV_BIN="${INSTALL_DIR}/.uv/uv"
  BIN_DIR="${INSTALL_DIR}/bin"
  AFK_LAUNCHER_PATH="${BIN_DIR}/afk"
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
  require_downloader
  run mkdir -p "$(dirname "${UV_BIN}")"
  if [[ "${DRY_RUN}" == "true" ]]; then
    log "[dry-run] install uv to ${INSTALL_DIR}/.uv"
    return 0
  fi
  local temp_script
  temp_script="$(mktemp "${TMPDIR:-/tmp}/afkbot-uv.XXXXXX")"
  download_to_file "${UV_INSTALL_URL}" "${temp_script}"
  env UV_UNMANAGED_INSTALL="$(dirname "${UV_BIN}")" sh "${temp_script}"
  rm -f "${temp_script}"
  [[ -x "${UV_BIN}" ]] || fail "uv installation completed but ${UV_BIN} was not created"
}

run_uv() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    run "${UV_BIN}" "$@"
    return 0
  fi
  "${UV_BIN}" "$@"
}

ensure_python_runtime() {
  run_uv python install 3.12
  run_uv venv --seed --allow-existing --python 3.12 "${VENV_DIR}"
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

build_archive_url() {
  local normalized="${REPO_URL}"
  case "${normalized}" in
    git@github.com:*)
      normalized="https://github.com/${normalized#git@github.com:}"
      ;;
  esac
  normalized="${normalized%.git}"
  normalized="${normalized%/}"
  case "${normalized}" in
    https://github.com/*|http://github.com/*|https://www.github.com/*|http://www.github.com/*)
      printf '%s/archive/%s.tar.gz\n' "${normalized}" "${GIT_REF}"
      return 0
      ;;
  esac
  fail "remote managed installs require a GitHub repository URL or a local source path"
}

build_release_id() {
  date -u +"%Y%m%d%H%M%S-$$"
}

install_source_snapshot() {
  local local_source="" release_id="" archive_url="" venv_python=""
  release_id="$(build_release_id)"
  CURRENT_APP_DIR="${APP_ROOT_DIR}/${release_id}"
  run mkdir -p "${APP_ROOT_DIR}"

  if local_source="$(resolve_local_source_path)"; then
    if [[ "${DRY_RUN}" == "true" ]]; then
      log "[dry-run] copy local source ${local_source} -> ${CURRENT_APP_DIR}"
      return 0
    fi
    run mkdir -p "${CURRENT_APP_DIR}"
    run cp -a "${local_source}/." "${CURRENT_APP_DIR}/"
    run rm -rf "${CURRENT_APP_DIR}/.git" "${CURRENT_APP_DIR}/.venv" "${CURRENT_APP_DIR}/.pytest_cache" \
      "${CURRENT_APP_DIR}/.ruff_cache" "${CURRENT_APP_DIR}/.mypy_cache" "${CURRENT_APP_DIR}/build" \
      "${CURRENT_APP_DIR}/dist"
  else
    archive_url="$(build_archive_url)"
    venv_python="${VENV_DIR}/bin/python"
    if [[ "${DRY_RUN}" == "true" ]]; then
      log "[dry-run] download ${archive_url}"
      log "[dry-run] extract remote source into ${CURRENT_APP_DIR}"
      return 0
    fi
    [[ -x "${venv_python}" ]] || fail "virtualenv python not found: ${venv_python}"
    AFK_INSTALL_ARCHIVE_URL="${archive_url}" \
    AFK_INSTALL_TARGET_DIR="${CURRENT_APP_DIR}" \
    "${venv_python}" - <<'PY'
import os
import shutil
import tarfile
import tempfile
from pathlib import Path
from urllib.request import urlopen

archive_url = os.environ["AFK_INSTALL_ARCHIVE_URL"]
target_dir = Path(os.environ["AFK_INSTALL_TARGET_DIR"]).resolve(strict=False)
temp_dir = Path(tempfile.mkdtemp(prefix="afkbot-source-")).resolve(strict=False)
archive_path = temp_dir / "source.tar.gz"
extract_dir = temp_dir / "extract"
extract_dir.mkdir(parents=True, exist_ok=True)
try:
    with urlopen(archive_url, timeout=30) as response, archive_path.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    with tarfile.open(archive_path, "r:gz") as archive:
        root = extract_dir.resolve(strict=False)
        for member in archive.getmembers():
            resolved_target = (extract_dir / member.name).resolve(strict=False)
            if root not in resolved_target.parents and resolved_target != root:
                raise SystemExit(f"Remote archive contains unsafe path: {member.name}")
        archive.extractall(extract_dir)
    entries = [item for item in extract_dir.iterdir() if item.is_dir()]
    if len(entries) != 1:
        raise SystemExit("Remote archive did not contain one source directory.")
    extracted_root = entries[0]
    if not (extracted_root / "pyproject.toml").exists():
        raise SystemExit("Remote archive is missing pyproject.toml.")
    if not (extracted_root / "afkbot").exists():
        raise SystemExit("Remote archive is missing the afkbot package.")
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(extracted_root), str(target_dir))
finally:
    shutil.rmtree(temp_dir, ignore_errors=True)
PY
  fi

  [[ "${DRY_RUN}" == "true" || -f "${CURRENT_APP_DIR}/pyproject.toml" ]] || fail "pyproject.toml missing in ${CURRENT_APP_DIR}"
  [[ "${DRY_RUN}" == "true" || -d "${CURRENT_APP_DIR}/afkbot" ]] || fail "afkbot package missing in ${CURRENT_APP_DIR}"
}

install_python_package() {
  local venv_python="${VENV_DIR}/bin/python"
  [[ "${DRY_RUN}" == "true" || -x "${venv_python}" ]] || fail "virtualenv python not found: ${venv_python}"
  run "${venv_python}" -m pip install --upgrade pip
  run "${venv_python}" -m pip install --upgrade -e "${CURRENT_APP_DIR}"
}

escaped_double_quotes() {
  printf '%s' "$1" | sed 's/"/\\"/g'
}

write_cli_launcher() {
  local venv_python="${VENV_DIR}/bin/python"
  [[ "${DRY_RUN}" == "true" || -x "${venv_python}" ]] || fail "virtualenv python not found: ${venv_python}"
  run mkdir -p "${BIN_DIR}"
  if [[ "${DRY_RUN}" == "true" ]]; then
    log "[dry-run] write ${AFK_LAUNCHER_PATH}"
    return 0
  fi
  AFK_WRITE_INSTALL_DIR="${INSTALL_DIR}" \
  AFK_WRITE_RUNTIME_DIR="${RUNTIME_DIR}" \
  AFK_WRITE_APP_DIR="${CURRENT_APP_DIR}" \
  AFK_WRITE_SOURCE_URL="${REPO_URL}" \
  AFK_WRITE_SOURCE_REF="${GIT_REF}" \
  AFK_WRITE_PYTHON="${venv_python}" \
  "${venv_python}" - <<'PY'
import os
from pathlib import Path

from afkbot.services.managed_install import ManagedInstallContext, write_managed_launcher

context = ManagedInstallContext(
    install_dir=Path(os.environ["AFK_WRITE_INSTALL_DIR"]),
    runtime_dir=Path(os.environ["AFK_WRITE_RUNTIME_DIR"]),
    app_dir=Path(os.environ["AFK_WRITE_APP_DIR"]),
    source_url=os.environ["AFK_WRITE_SOURCE_URL"],
    source_ref=os.environ["AFK_WRITE_SOURCE_REF"],
)
write_managed_launcher(
    context=context,
    python_executable=Path(os.environ["AFK_WRITE_PYTHON"]),
    app_dir=Path(os.environ["AFK_WRITE_APP_DIR"]),
)
PY
}

path_contains() {
  case ":${PATH:-}:" in
    *":$1:"*) return 0 ;;
    *) return 1 ;;
  esac
}

path_value_contains() {
  local path_value="$1"
  local path_entry="$2"
  case ":${path_value}:" in
    *":${path_entry}:"*) return 0 ;;
    *) return 1 ;;
  esac
}

resolve_profile_path() {
  local shell_name
  shell_name="$(basename "${SHELL:-}")"
  case "${shell_name}" in
    zsh)
      if [[ -f "${HOME}/.zprofile" ]]; then
        printf '%s\n' "${HOME}/.zprofile"
      else
        printf '%s\n' "${HOME}/.zshrc"
      fi
      ;;
    bash)
      if [[ "${PLATFORM}" == "macos" ]]; then
        printf '%s\n' "${HOME}/.bash_profile"
      else
        printf '%s\n' "${HOME}/.bashrc"
      fi
      ;;
    *)
      printf '%s\n' "${HOME}/.profile"
      ;;
  esac
}

ensure_path_block() {
  local profile_path escaped_bin
  profile_path="$(resolve_profile_path)"
  PROFILE_PATH="${profile_path}"
  escaped_bin="$(escaped_double_quotes "${BIN_DIR}")"
  if ! path_contains "${BIN_DIR}"; then
    export PATH="${BIN_DIR}:${PATH:-}"
  fi
  if [[ -f "${profile_path}" ]] && grep -Fq "${PATH_BLOCK_START}" "${profile_path}"; then
    return 0
  fi
  if [[ "${DRY_RUN}" == "true" ]]; then
    log "[dry-run] append ${BIN_DIR} to PATH in ${profile_path}"
    return 0
  fi
  touch "${profile_path}"
  {
    printf '\n%s\n' "${PATH_BLOCK_START}"
    printf "export PATH=\"%s:\\\$PATH\"\n" "${escaped_bin}"
    printf '%s\n' "${PATH_BLOCK_END}"
  } >>"${profile_path}"
}

select_cli_alias_path() {
  local venv_python="${VENV_DIR}/bin/python"
  if [[ ! -x "${venv_python}" ]]; then
    [[ "${DRY_RUN}" == "true" ]] && return 0
    fail "virtualenv python not found: ${venv_python}"
  fi
  AFK_SELECT_LAUNCHER_PATH="${AFK_LAUNCHER_PATH}" \
  AFK_SELECT_PATH_ENV="${ORIGINAL_PATH}" \
  "${venv_python}" - <<'PY'
import os
from pathlib import Path

from afkbot.services.managed_install import pick_convenience_launcher_path

candidate = pick_convenience_launcher_path(
    launcher_path=Path(os.environ["AFK_SELECT_LAUNCHER_PATH"]),
    path_env=os.environ.get("AFK_SELECT_PATH_ENV", ""),
)
print("" if candidate is None else str(candidate))
PY
}

install_cli_alias() {
  local alias_path=""
  alias_path="$(select_cli_alias_path)"
  [[ -n "${alias_path}" ]] || return 0
  CLI_ALIAS_PATH="${alias_path}"
  if [[ "${DRY_RUN}" == "true" ]]; then
    log "[dry-run] symlink ${CLI_ALIAS_PATH} -> ${AFK_LAUNCHER_PATH}"
    return 0
  fi
  ln -sfn "${AFK_LAUNCHER_PATH}" "${CLI_ALIAS_PATH}"
}

run_bootstrap_setup() {
  if [[ "${SKIP_SETUP}" == "true" ]]; then
    return 0
  fi
  run "${AFK_LAUNCHER_PATH}" setup --bootstrap-only --yes
}

prune_old_app_dirs() {
  local candidate=""
  [[ -d "${APP_ROOT_DIR}" ]] || return 0
  shopt -s nullglob
  for candidate in "${APP_ROOT_DIR}"/*; do
    [[ -d "${candidate}" ]] || continue
    [[ "${candidate}" == "${CURRENT_APP_DIR}" ]] && continue
    run rm -rf "${candidate}"
  done
  shopt -u nullglob
}

print_success() {
  log ""
  log "${PROGRAM_NAME} install complete."
  log "Install root: ${INSTALL_DIR}"
  log "Runtime root: ${RUNTIME_DIR}"
  log "App source: ${CURRENT_APP_DIR}"
  log "CLI: ${AFK_LAUNCHER_PATH}"
  if [[ -n "${CLI_ALIAS_PATH}" ]]; then
    log "CLI alias: ${CLI_ALIAS_PATH}"
  fi
  log ""
  if [[ -z "${CLI_ALIAS_PATH}" ]] && ! path_value_contains "${ORIGINAL_PATH}" "${BIN_DIR}"; then
    log "Current shell:"
    log "  export PATH=\"${BIN_DIR}:\$PATH\""
    if [[ -n "${PROFILE_PATH}" ]]; then
      log "Future shells:"
      log "  source \"${PROFILE_PATH}\""
    fi
    log ""
  fi
  log "Next steps:"
  log "  afk setup"
  log "  afk doctor"
  log "  afk chat"
  log ""
  log "To update later, rerun this installer or use \`afk update\`."
}

main() {
  parse_args "$@"
  detect_platform
  ensure_uv
  ensure_python_runtime
  install_source_snapshot
  install_python_package
  run mkdir -p "${RUNTIME_DIR}"
  write_cli_launcher
  install_cli_alias
  ensure_path_block
  run_bootstrap_setup
  prune_old_app_dirs
  print_success
}

main "$@"
