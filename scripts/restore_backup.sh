#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)
ENV_FILE="${PROJECT_DIR}/.env"

usage() {
  printf '%s\n' 'Usage: ./scripts/restore_backup.sh /exact/path/to/backup.dump --yes'
}

[ "$#" -eq 2 ] || { usage >&2; exit 2; }
[ "$2" = "--yes" ] || { usage >&2; exit 2; }
backup_file=$1
[ -f "${backup_file}" ] || { printf 'Backup not found: %s\n' "${backup_file}" >&2; exit 1; }
[ -f "${ENV_FILE}" ] || { printf 'Missing .env; initialize the deployment first.\n' >&2; exit 1; }
command -v docker >/dev/null 2>&1 || { printf 'Docker is required.\n' >&2; exit 1; }

cd "${PROJECT_DIR}"

# Always make a recovery point before replacing database objects.
"${SCRIPT_DIR}/deploy.sh" backup

docker compose --env-file "${ENV_FILE}" exec -T db sh -c \
  'pg_restore --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --clean --if-exists --no-owner --no-acl' \
  < "${backup_file}"

docker compose --env-file "${ENV_FILE}" restart api
printf 'Restore completed from %s\n' "${backup_file}"
