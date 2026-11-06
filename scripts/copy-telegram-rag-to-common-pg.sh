#!/usr/bin/env bash
# Copy telegram-rag data once from the running Postgres 15 container into common-pg.
# Does not mount or delete the postgres_data volume.
set -euo pipefail

allow_nonempty=0
if [[ ${1:-} == "--allow-nonempty" ]]; then
	allow_nonempty=1
elif [[ -n ${1:-} ]]; then
	echo "unknown argument: $1" >&2
	exit 1
fi

if [[ -z ${TARGET_DATABASE_URL:-} ]]; then
	echo "TARGET_DATABASE_URL is required" >&2
	exit 1
fi

source_container="${SOURCE_CONTAINER:-telegram-rag-postgres-1}"
source_running="$(docker inspect -f '{{.State.Running}}' "$source_container" 2>/dev/null || true)"
if [[ $source_running != "true" ]]; then
	echo "source container is not running" >&2
	exit 1
fi

if docker compose ps --status running --services 2>/dev/null | grep -Eq '^(api|indexer)$'; then
	echo "stop api and indexer before copying" >&2
	exit 1
fi

source_user="$(docker exec "$source_container" printenv POSTGRES_USER)"
source_password="$(docker exec "$source_container" printenv POSTGRES_PASSWORD)"
source_db="$(docker exec "$source_container" printenv POSTGRES_DB)"

other_sessions="$(
	docker exec -e PGPASSWORD="$source_password" "$source_container" \
		psql -U "$source_user" -d "$source_db" -Atqc \
		"SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() AND backend_type = 'client backend'"
)"
if [[ $other_sessions != "0" ]]; then
	echo "source database has other sessions; refuse to dump" >&2
	exit 1
fi

target_tables="$(
	docker run --rm --network common-pg -e TARGET_DATABASE_URL="$TARGET_DATABASE_URL" postgres:18-alpine \
		psql "$TARGET_DATABASE_URL" -Atqc \
		"SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r'"
)"
if [[ $target_tables != "0" && $allow_nonempty != "1" ]]; then
	echo "target database is not empty; pass --allow-nonempty to restore again" >&2
	exit 1
fi

dump_file="$(mktemp "${TMPDIR:-/tmp}/telegram-rag-copy.XXXXXX.dump")"
chmod 600 "$dump_file"
dump_ok=0
restore_ok=0
cleanup() {
	if [[ $restore_ok == "1" || $dump_ok != "1" ]]; then
		rm -f "$dump_file"
	else
		echo "restore failed; dump kept at $dump_file" >&2
	fi
}
trap cleanup EXIT

docker run --rm --network "container:${source_container}" \
	-e PGPASSWORD="$source_password" \
	-v "$dump_file:/dump/copy.dump" \
	postgres:18-alpine \
	pg_dump --host 127.0.0.1 --username "$source_user" --dbname "$source_db" \
	--format=custom --quote-all-identifiers --file=/dump/copy.dump
dump_ok=1

docker run --rm --network common-pg \
	-e TARGET_DATABASE_URL="$TARGET_DATABASE_URL" \
	-v "$dump_file:/dump/copy.dump:ro" \
	postgres:18-alpine \
	pg_restore --dbname "$TARGET_DATABASE_URL" --no-owner --role=telegram_rag --exit-on-error \
	/dump/copy.dump
restore_ok=1

echo "copy finished"
