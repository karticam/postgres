#!/usr/bin/env python3
import subprocess
import sys
import time

DB_NAME = "postgres"

def run_psql(sql):
    print(f"Executing: {sql}")
    start = time.time()
    subprocess.run(["psql", "-d", DB_NAME, "-c", sql], check=True)
    print(f"  Took {time.time() - start:.2f}s")

def main():
    # Check if server is running
    if subprocess.run(["pg_isready", "-q", "-d", DB_NAME], stdout=subprocess.DEVNULL).returncode != 0:
        print("Error: Postgres server is not running. Please start it first.")
        sys.exit(1)

    print("--- Generating Vondra Test Data ---")

    # 1. Clean up
    run_psql("DROP TABLE IF EXISTS fact;")
    run_psql("DROP TABLE IF EXISTS dim;")

    # 2. Create and populate DIM table (10M rows)
    print("\nCreating DIM table (10M rows)...")
    run_psql("CREATE TABLE dim (id INT, dval TEXT);")
    run_psql("INSERT INTO dim SELECT i, md5(i::text) FROM generate_series(1, 10000000) s(i);")
    run_psql("ALTER TABLE dim ADD PRIMARY KEY (id);") # Good practice for joins

    # 3. Create and populate FACT table (100M rows)
    print("\nCreating FACT table (100M rows)...")
    run_psql("CREATE TABLE fact (id INT, fval TEXT);")
    
    # Strategy: 5x insert from dim (50M), then 1x insert from fact (doubles to 100M)
    print("  Inserting 5x from dim...")
    for i in range(5):
        run_psql("INSERT INTO fact SELECT * FROM dim;")
        
    print("  Doubling fact table...")
    run_psql("INSERT INTO fact SELECT * FROM fact;")

    # 4. Analyze
    print("\nRunning VACUUM ANALYZE...")
    run_psql("VACUUM ANALYZE dim;")
    run_psql("VACUUM ANALYZE fact;")

    print("\nDone! Data generation complete.")

if __name__ == "__main__":
    main()
