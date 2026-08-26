#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)
RELEASE_DIR="${PROJECT_DIR}/release"

cd "${PROJECT_DIR}"

command -v rsync >/dev/null 2>&1 || {
  printf 'rsync is required to build a clean deployment package.\n' >&2
  exit 1
}
command -v git >/dev/null 2>&1 || {
  printf 'git is required to enumerate the clean deployment file set.\n' >&2
  exit 1
}
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
  printf 'Run this command from a Git working tree.\n' >&2
  exit 1
}

if command -v python >/dev/null 2>&1; then
  scan_python=python
elif command -v python3 >/dev/null 2>&1; then
  scan_python=python3
else
  printf 'Python 3 is required to run the pre-package secret scan.\n' >&2
  exit 1
fi
"${scan_python}" scripts/scan_secrets.py

stage_root=$(mktemp -d)
stage_project="${stage_root}/govtrans"
cleanup() {
  case "${stage_root}" in
    /tmp/*) rm -rf -- "${stage_root}" ;;
  esac
}
trap cleanup EXIT HUP INT TERM
mkdir -p "${stage_project}" "${RELEASE_DIR}"

# Package exactly the tracked plus untracked/non-ignored repository files.
# This allowlist automatically excludes real env files, databases, build
# output, IDE state, local Tofu history/memories and every other ignored
# machine artifact without relying on an ever-growing denylist.
git ls-files --cached --others --exclude-standard -z | \
  rsync -a --from0 --files-from=- ./ "${stage_project}/"

install -m 0644 .env.example "${stage_project}/.env.example"
install -m 0644 .env.production.example "${stage_project}/.env.production.example"

for required in \
  docker-compose.yml \
  scripts/deploy.sh \
  vendor/tofu-agent/tofu_agent-0.17.0-py3-none-any.whl \
  vendor/tofu-agent/requirements.lock
do
  [ -f "${stage_project}/${required}" ] || {
    printf 'Refusing to package: required file is ignored or missing: %s\n' "${required}" >&2
    exit 1
  }
done

[ ! -e "${stage_project}/.env" ] || {
  printf 'Refusing to package a real .env file.\n' >&2
  exit 1
}
[ ! -d "${stage_project}/data" ] || {
  printf 'Refusing to package local application data.\n' >&2
  exit 1
}
[ ! -d "${stage_project}/.tofu" ] || {
  printf 'Refusing to package local Tofu state.\n' >&2
  exit 1
}

stamp=$(date -u '+%Y%m%dT%H%M%SZ')
archive="${RELEASE_DIR}/govtrans-deploy-${stamp}.tar.gz"
archive_name=$(basename "${archive}")
tar -C "${stage_root}" -czf "${archive}" govtrans

if command -v sha256sum >/dev/null 2>&1; then
  (cd "${RELEASE_DIR}" && sha256sum "${archive_name}" > "${archive_name}.sha256")
else
  (cd "${RELEASE_DIR}" && shasum -a 256 "${archive_name}" > "${archive_name}.sha256")
fi

printf 'Deployment archive: %s\nChecksum: %s.sha256\n' "${archive}" "${archive}"
