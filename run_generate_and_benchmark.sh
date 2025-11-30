#!/usr/bin/env bash
set -euo pipefail

# Run from the repository root
cd "$(dirname "$0")"

# Default to LOCAL if PG_MODE is not set
PG_MODE="${PG_MODE:-LOCAL}"

if [ "$PG_MODE" == "AWS" ]; then
    echo "Running in AWS mode"
    export PGDATA="/mnt/pgdata/data"
    PGBUILD="/mnt/pgdata/pg_install"
else
    echo "Running in LOCAL mode"
    export PGDATA="$HOME/pgdata"
    PGBUILD="$HOME/pgbuild"
fi

# Add postgres binaries to PATH
export PATH="$PGBUILD/bin:$PATH"

echo "Using PGDATA=$PGDATA"
echo "Using PGBUILD=$PGBUILD"

echo "Running data generation..."
python3 generate_data.py

echo "Running benchmark..."
python3 benchmark.py
