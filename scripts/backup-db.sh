#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
umask 077
mkdir -p runtime/backups
file="runtime/backups/aftercare-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose --env-file deployment/.env.production -f deployment/compose.yaml exec -T postgres pg_dump -U postgres -d aftercare -Fc > "$file"
[ -s "$file" ]
printf 'Database backup saved to %s\n' "$file"
printf 'Also retain the matching private bucket snapshot/version history. Store both off this host.\n'
