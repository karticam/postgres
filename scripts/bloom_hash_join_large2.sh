#!/bin/bash
set -e

PGDATA="$HOME/pgdata"
DBNAME="cp1db"

SET_CMD="SET max_parallel_workers_per_gather = 0;"
QUERY="EXPLAIN (ANALYZE, TIMING ON, SUMMARY ON) SELECT COUNT(fval) FROM fact JOIN dim USING (id) WHERE dval < '2';"

CSV_FILE="execution_times_large.csv"
PLOT_SCRIPT="plot_times_large.py"

echo "run,branch,planning_ms,execution_ms" > $CSV_FILE

# ----------------------------------------
# Helpers
# ----------------------------------------

start_pg() {
    echo "🔧 Starting Postgres..."
    postgres -D "$PGDATA" > pg_server.log 2>&1 &
    PG_PID=$!

    echo -n "⏳ Waiting for server..."
    until pg_isready -d "$DBNAME" -q; do
        sleep 0.5
    done
    echo " ✓ Ready!"
}

stop_pg() {
    if ps -p $PG_PID > /dev/null 2>&1; then
        echo "🛑 Stopping Postgres..."
        kill $PG_PID
        sleep 1
    fi
}

extract_planning() {
    echo "$1" | grep "Planning Time" | awk '{print $(NF-1)}'
}

extract_execution() {
    echo "$1" | grep "Execution Time" | awk '{print $(NF-1)}'
}

run_branch() {
    local branch=$1
    echo "==============================="
    echo "🔀 Checking out branch: $branch"
    echo "==============================="

    git checkout -q "$branch"

    echo "⚙️  Building Postgres (make -j8 && make install)..."
    make -j8 > /dev/null
    make install > /dev/null
    echo "   ✓ Build complete"

    # Start server for this branch
    start_pg

    echo "🏁 Running benchmark for $branch"
    for i in {1..10}; do
        echo "   → Run #$i"

        RAW=$(psql -d "$DBNAME" -X -A -t <<EOF
$SET_CMD;
$QUERY;
EOF
        )

        PLAN=$(extract_planning "$RAW")
        EXEC=$(extract_execution "$RAW")

        echo "Raw output: \n$RAW"

        echo "      Planning:  $PLAN ms"
        echo "      Execution: $EXEC ms"

        echo "$i,$branch,$PLAN,$EXEC" >> "$CSV_FILE"
    done

    # Stop server for this branch
    stop_pg
}

# ----------------------------------------
# Run benchmarks
# ----------------------------------------

# 1. Base branch (LIP)
run_branch "karticam_aarryas/lip"

# 2. Modified branch (cp1)
run_branch "cp1"

echo "📄 CSV saved to $CSV_FILE"
