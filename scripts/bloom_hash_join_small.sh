#!/bin/bash
set -e

PGDATA="$HOME/pgdata"
DBNAME="cp1db"

QUERY="EXPLAIN (ANALYZE, BUFFERS) SELECT f.id, f.payload AS fact_payload, d.payload AS dim_payload FROM fact2 f JOIN dim2 d ON f.id = d.id;"

CSV_FILE="execution_times_small.csv"
PLOT_SCRIPT="plot_small.py"

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

    echo "⚙️ Building Postgres..."
    make -j8 > /dev/null
    make install > /dev/null
    echo "   ✓ Build done"

    # Start server for this branch
    start_pg

    echo "🏁 Running benchmark for $branch"
    for i in {1..10}; do
        echo "   → Run #$i"
        
        RAW=$(psql -d "$DBNAME" -X -A -t -c "$QUERY")

        PLAN=$(extract_planning "$RAW")
        EXEC=$(extract_execution "$RAW")

        echo "Raw output: \n$RAW"

        echo "      Planning:  $PLAN ms"
        echo "      Execution: $EXEC ms"

        echo "$i,$branch,$PLAN,$EXEC" >> "$CSV_FILE"
    done

    stop_pg
}

# ----------------------------------------
# Run both branches
# ----------------------------------------

run_branch "karticam_aarryas/lip"
run_branch "cp1"

echo "📄 CSV saved to $CSV_FILE"
