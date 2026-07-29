#!/bin/bash
# PATCHED BY PROVENANCE -- see DECISIONS.md D2.1
# Upstream used "${psql[@]}", a bash array defined by the postgres entrypoint. That array
# only exists when the entrypoint *sources* this file, which it does only when the file is
# NOT executable. A Windows git checkout marks it 755, so Docker COPYs it executable, the
# entrypoint runs it as a subprocess, the array is empty and init fails with
# `--dbname=provenance: command not found`. Calling psql directly works either way.
set -e

echo "Loading pgmer2 extension into $POSTGRES_DB"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'EOSQL'
  CREATE EXTENSION IF NOT EXISTS pgmer2;
EOSQL
