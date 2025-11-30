import subprocess
import time
import os
import random
import csv
import signal
import sys

# --- Configuration ---
FACT_SIZES = [100_000, 1_000_000, 10_000_000, 100_000_000]
DIM_SIZES = [10_000, 100_000, 1_000_000, 10_000_000]
SELECTIVITIES = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
BF_MULTS = [0.8, 0.9, 1.0, 1.1, 1.2]
BF_HASHES = [1, 2, 3, 4, 5]
BF_ENABLE_OPTS = ["on", "off"]

DB_NAME = "postgres"
PGDATA = os.path.expanduser("~/pgdata")
RESULTS_FILE = "benchmark_results.csv"
LOG_FILE = "benchmark_log.txt"

# --- Data Generation ---
def get_random_string():
    return random.randbytes(4).hex()

def generate_data(fact_rows, dim_rows, selectivity):
    print(f"Generating data: Fact={fact_rows}, Dim={dim_rows}, Sel={selectivity}")
    
    # Files
    dim_csv = "dim_gemini.csv"
    fact_csv = "fact_gemini.csv"
    
    # Generate Dim
    with open(dim_csv, "w") as f:
        for i in range(dim_rows):
            f.write(f"{i},{get_random_string()}\n")
            
    # Generate Fact
    num_matching = int(fact_rows * selectivity)
    num_non_matching = fact_rows - num_matching
    
    with open(fact_csv, "w") as f:
        # Matching
        for _ in range(num_matching):
            key = random.randint(0, dim_rows - 1)
            f.write(f"{key},{get_random_string()}\n")
        # Non-matching
        for _ in range(num_non_matching):
            key = random.randint(dim_rows, dim_rows * 2 + num_non_matching)
            f.write(f"{key},{get_random_string()}\n")

    # Load into Postgres (Server must be running!)
    # Ideally we load data once per data-config. 
    # But we need the server running to load data.
    # We will assume server is running or start it temporarily.
    return dim_csv, fact_csv

def setup_tables(dim_csv, fact_csv):
    # Drop and Create
    run_psql("DROP TABLE IF EXISTS fact_gemini, dim_gemini CASCADE;")
    run_psql("CREATE TABLE fact_gemini (int_value INTEGER, str_value CHAR(8));")
    run_psql("CREATE TABLE dim_gemini (int_value INTEGER, str_value CHAR(8));")
    
    # Load
    run_psql(f"\\copy dim_gemini FROM '{dim_csv}' WITH CSV")
    run_psql(f"\\copy fact_gemini FROM '{fact_csv}' WITH CSV")
    
    # Cleanup CSVs
    if os.path.exists(dim_csv): os.remove(dim_csv)
    if os.path.exists(fact_csv): os.remove(fact_csv)

# --- Server Control ---
def stop_server():
    print("Stopping server...")
    subprocess.run(["pg_ctl", "-D", PGDATA, "stop", "-m", "fast"], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    time.sleep(2)

def start_server(enable_bf, bf_mult, bf_hashes):
    print(f"Starting server: BF={enable_bf}, Mult={bf_mult}, Hash={bf_hashes}")
    
    cmd = [
        "postgres", "-D", PGDATA,
        "-c", f"enable_bloom_filter={enable_bf}",
        "-c", f"bloom_filter_multiplier={bf_mult}",
        "-c", f"bloom_filter_hash_functions={bf_hashes}"
    ]
    
    # Start in background
    proc = subprocess.Popen(cmd, stdout=open(LOG_FILE, "a"), stderr=subprocess.STDOUT)
    
    # Wait for ready
    for _ in range(30):
        if subprocess.run(["pg_isready", "-q"], stdout=subprocess.DEVNULL).returncode == 0:
            return proc
        time.sleep(1)
    
    raise Exception("Server failed to start")

def run_psql(sql):
    subprocess.run(["psql", "-d", DB_NAME, "-c", sql], check=True)

def run_benchmark_query():
    sql = "EXPLAIN (ANALYZE, TIMING OFF) SELECT count(*) FROM fact_gemini f JOIN dim_gemini d ON f.int_value = d.int_value;"
    # We use TIMING OFF in EXPLAIN to minimize overhead, but we want the total execution time.
    # Actually, user asked for timing.
    # Let's just use psql's timing or parse EXPLAIN ANALYZE output.
    # Parsing EXPLAIN ANALYZE Execution Time is best.
    
    result = subprocess.run(
        ["psql", "-d", DB_NAME, "-c", sql], 
        capture_output=True, text=True
    )
    
    if result.returncode != 0:
        print("Query failed:", result.stderr)
        return -1
        
    # Parse Execution Time
    for line in result.stdout.splitlines():
        if "Execution Time:" in line:
            # Format: " Execution Time: 123.456 ms"
            parts = line.split()
            return float(parts[2])
    return -1

# --- Main ---
def main():
    # 1. Build
    print("Building Postgres...")
    subprocess.run("make -j8 && make install", shell=True, check=True, cwd=os.getcwd())
    
    # Initialize results
    with open(RESULTS_FILE, "w") as f:
        writer = csv.writer(f)
        writer.writerow(["fact_rows", "dim_rows", "selectivity", "bf_enable", "bf_mult", "bf_hashes", "time_ms"])

    # Ensure server is stopped initially
    stop_server()

    try:
        for fact_rows in FACT_SIZES:
            for dim_rows in DIM_SIZES:
                if dim_rows > fact_rows: continue
                
                for sel in SELECTIVITIES:
                    # 2. Generate Data
                    # We need a running server to load data.
                    # Start with default config for loading.
                    server_proc = start_server("off", 1, 1)
                    
                    try:
                        dim_csv, fact_csv = generate_data(fact_rows, dim_rows, sel)
                        setup_tables(dim_csv, fact_csv)
                    finally:
                        # Stop server to prepare for benchmark loops
                        server_proc.terminate()
                        server_proc.wait()
                        stop_server()

                    # 3. Benchmark Loops
                    for bf_enable in BF_ENABLE_OPTS:
                        for bf_mult in BF_MULTS:
                            for bf_hashes in BF_HASHES:
                                
                                # Optimization: If BF is off, mult/hashes don't matter.
                                # Run only once for 'off' to save time.
                                if bf_enable == "off" and (bf_mult != BF_MULTS[0] or bf_hashes != BF_HASHES[0]):
                                    continue
                                
                                server_proc = start_server(bf_enable, bf_mult, bf_hashes)
                                
                                try:
                                    exec_time = run_benchmark_query()
                                    print(f"  Result: {exec_time} ms")
                                    
                                    with open(RESULTS_FILE, "a") as f:
                                        writer = csv.writer(f)
                                        writer.writerow([fact_rows, dim_rows, sel, bf_enable, bf_mult, bf_hashes, exec_time])
                                finally:
                                    server_proc.terminate()
                                    server_proc.wait()
                                    stop_server()

    except KeyboardInterrupt:
        print("Benchmark interrupted.")
    finally:
        stop_server()

if __name__ == "__main__":
    main()
