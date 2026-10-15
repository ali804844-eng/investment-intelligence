#!/usr/bin/env bash
# Nightly logical backup of the PostgreSQL database with 14-day retention.
# Usage: BACKUP_DIR=/var/backups/ii DATABASE_URL=postgresql://ii:pw@localhost:5432/ii ./backup.sh
# Docker: docker compose exec -T db pg_dump -U ii -Fc ii > ii-$(date +%F).dump
set -euo pipefail
BACKUP_DIR="${BACKUP_DIR:-/var/backups/ii}"
URL="${DATABASE_URL/postgresql+psycopg/postgresql}"
mkdir -p "$BACKUP_DIR"
f="$BACKUP_DIR/ii-$(date -u +%Y%m%dT%H%M%SZ).dump"
pg_dump --format=custom --no-owner --dbname="$URL" --file="$f"
pg_restore --list "$f" > /dev/null   # verify the archive is readable
find "$BACKUP_DIR" -name 'ii-*.dump' -mtime +14 -delete
echo "backup written: $f"
# Restore: pg_restore --clean --if-exists --no-owner --dbname="$URL" <file.dump>
