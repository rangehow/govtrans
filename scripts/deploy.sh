#!/usr/bin/env sh
set -eu
umask 077

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)
ENV_FILE="${PROJECT_DIR}/.env"
ENV_TEMPLATE="${PROJECT_DIR}/.env.production.example"

cd "${PROJECT_DIR}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

note() {
  printf '%s\n' "$*"
}

generate_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
    return
  fi
  if [ -r /dev/urandom ] && command -v od >/dev/null 2>&1; then
    od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
    return
  fi
  die "openssl or /dev/urandom + od is required to generate deployment secrets"
}

replace_env_value() {
  key=$1
  value=$2
  target=$3
  temp_file="${target}.tmp.$$"
  awk -v env_key="${key}" -v env_value="${value}" '
    index($0, env_key "=") == 1 { print env_key "=" env_value; next }
    { print }
  ' "${target}" > "${temp_file}"
  chmod 600 "${temp_file}"
  mv "${temp_file}" "${target}"
}

require_docker() {
  command -v docker >/dev/null 2>&1 || die \
    "Docker is not installed. Follow https://docs.docker.com/engine/install/"
  docker compose version >/dev/null 2>&1 || die \
    "Docker Compose plugin is missing. Install docker-compose-plugin or Docker Desktop."
  docker info >/dev/null 2>&1 || die \
    "Docker daemon is unavailable. Start Docker (or fix current-user permissions) and retry."
}

verify_wheel() {
  wheel="vendor/tofu-agent/tofu_agent-0.17.0-py3-none-any.whl"
  expected="681ddbeaf599b7932308e42a5ae7330064dc4bbe9c68e2b30af6561d3f9daca8"
  [ -f "${wheel}" ] || die "bundled Tofu wheel is missing: ${wheel}"
  if command -v sha256sum >/dev/null 2>&1; then
    actual=$(sha256sum "${wheel}" | awk '{print $1}')
  elif command -v shasum >/dev/null 2>&1; then
    actual=$(shasum -a 256 "${wheel}" | awk '{print $1}')
  else
    die "sha256sum or shasum is required to verify the bundled Tofu runtime"
  fi
  [ "${actual}" = "${expected}" ] || die \
    "bundled Tofu wheel checksum mismatch (expected ${expected}, got ${actual})"
}

check_env() {
  [ -f "${ENV_FILE}" ] || die "missing .env; run ./scripts/deploy.sh init first"
  chmod 600 "${ENV_FILE}"
  if grep -Eq '^(POSTGRES_PASSWORD|TOFU_API_KEY)=GENERATE_' "${ENV_FILE}"; then
    die ".env still contains generated-secret placeholders; rerun init"
  fi
  if grep -Eq '^DASHSCOPE_API_KEY=(|replace-with-your-real-dashscope-key)$' "${ENV_FILE}"; then
    die "set a real DASHSCOPE_API_KEY in .env before starting"
  fi
}

init_env() {
  [ -f "${ENV_TEMPLATE}" ] || die "missing ${ENV_TEMPLATE}"
  if [ ! -f "${ENV_FILE}" ]; then
    cp "${ENV_TEMPLATE}" "${ENV_FILE}"
    chmod 600 "${ENV_FILE}"
    note "Created .env from .env.production.example."
  else
    chmod 600 "${ENV_FILE}"
    note "Keeping existing .env; only unresolved generated placeholders will be filled."
  fi

  if grep -q '^POSTGRES_PASSWORD=GENERATE_POSTGRES_PASSWORD$' "${ENV_FILE}"; then
    replace_env_value POSTGRES_PASSWORD "$(generate_secret)" "${ENV_FILE}"
  fi
  if grep -q '^TOFU_API_KEY=GENERATE_TOFU_API_KEY$' "${ENV_FILE}"; then
    replace_env_value TOFU_API_KEY "$(generate_secret)" "${ENV_FILE}"
  fi
  if [ -n "${DASHSCOPE_API_KEY:-}" ]; then
    replace_env_value DASHSCOPE_API_KEY "${DASHSCOPE_API_KEY}" "${ENV_FILE}"
    note "Stored DASHSCOPE_API_KEY from the current process environment."
  fi

  verify_wheel
  note "Initialization complete."
  if grep -Eq '^DASHSCOPE_API_KEY=(|replace-with-your-real-dashscope-key)$' "${ENV_FILE}"; then
    note "Next: edit .env and replace DASHSCOPE_API_KEY, then run ./scripts/deploy.sh up"
  else
    note "Next: run ./scripts/deploy.sh up"
  fi
}

wait_for_health() {
  service=$1
  attempts=${2:-60}
  count=0
  while [ "${count}" -lt "${attempts}" ]; do
    container_id=$(docker compose --env-file "${ENV_FILE}" ps -q "${service}")
    if [ -n "${container_id}" ]; then
      health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container_id}" 2>/dev/null || true)
      if [ "${health}" = "healthy" ] || [ "${health}" = "running" ]; then
        note "${service}: ${health}"
        return 0
      fi
      if [ "${health}" = "unhealthy" ] || [ "${health}" = "exited" ] || [ "${health}" = "dead" ]; then
        docker compose --env-file "${ENV_FILE}" logs --tail=120 "${service}" >&2 || true
        die "${service} entered ${health} state"
      fi
    fi
    count=$((count + 1))
    sleep 5
  done
  docker compose --env-file "${ENV_FILE}" logs --tail=120 "${service}" >&2 || true
  die "timed out waiting for ${service} to become healthy"
}

start_stack() {
  require_docker
  check_env
  verify_wheel
  docker compose --env-file "${ENV_FILE}" config --quiet
  docker compose --env-file "${ENV_FILE}" up -d --build
  wait_for_health db 60
  wait_for_health tofu 60
  wait_for_health api 60
  wait_for_health web 30
  note "GovTrans is ready: http://localhost:$(awk -F= '$1=="GOVTRANS_PORT" {print $2}' "${ENV_FILE}" | tail -n 1)"
}

doctor() {
  require_docker
  check_env
  verify_wheel
  docker compose --env-file "${ENV_FILE}" config --quiet
  docker compose --env-file "${ENV_FILE}" ps
  docker compose --env-file "${ENV_FILE}" exec -T tofu \
    curl -fsS http://localhost:15001/health/ready >/dev/null
  docker compose --env-file "${ENV_FILE}" exec -T api \
    curl -fsS http://localhost:8100/healthz >/dev/null
  docker compose --env-file "${ENV_FILE}" exec -T web \
    wget -q --spider http://localhost/
  note "OK: Compose configuration, Tofu, API and Web health checks passed."
}

backup_database() {
  require_docker
  check_env
  backup_dir=${1:-"${PROJECT_DIR}/backups"}
  mkdir -p "${backup_dir}"
  backup_dir=$(CDPATH= cd -- "${backup_dir}" && pwd)
  stamp=$(date -u '+%Y%m%dT%H%M%SZ')
  output="${backup_dir}/govtrans-${stamp}.dump"
  output_name=$(basename "${output}")
  temp_output="${output}.tmp.$$"
  if ! docker compose --env-file "${ENV_FILE}" exec -T db sh -c \
    'pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --format=custom --no-owner --no-acl' \
    > "${temp_output}"; then
    rm -f -- "${temp_output}"
    die "database backup failed"
  fi
  chmod 600 "${temp_output}"
  mv "${temp_output}" "${output}"
  if command -v sha256sum >/dev/null 2>&1; then
    (cd "${backup_dir}" && sha256sum "${output_name}" > "${output_name}.sha256")
  else
    (cd "${backup_dir}" && shasum -a 256 "${output_name}" > "${output_name}.sha256")
  fi
  note "Backup written to ${output}"
}

usage() {
  cat <<'EOF'
Usage: ./scripts/deploy.sh COMMAND [ARG]

Commands:
  init              Create .env and generate internal secrets (idempotent)
  up                Build and start the complete stack, then wait for health
  doctor            Verify Compose, Tofu, API and Web health
  status            Show container status
  logs [service]    Follow all logs or one service (db, tofu, api, web)
  stop              Stop containers without deleting data volumes
  backup [dir]      Create a compressed-format PostgreSQL backup
EOF
}

command=${1:-help}
case "${command}" in
  init)
    init_env
    ;;
  up)
    start_stack
    ;;
  doctor)
    doctor
    ;;
  status)
    require_docker
    check_env
    docker compose --env-file "${ENV_FILE}" ps
    ;;
  logs)
    require_docker
    check_env
    if [ "$#" -ge 2 ]; then
      docker compose --env-file "${ENV_FILE}" logs -f --tail=200 "$2"
    else
      docker compose --env-file "${ENV_FILE}" logs -f --tail=200
    fi
    ;;
  stop)
    require_docker
    check_env
    docker compose --env-file "${ENV_FILE}" down
    ;;
  backup)
    backup_database "${2:-${PROJECT_DIR}/backups}"
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
