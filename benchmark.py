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
SELECTIVITIES = [0, 0.2, 0.4, 0.6, 0.8, 1.0]
BF_MULTS = [0.5, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5]
BF_HASHES = [1, 2, 3, 4, 5]
BF_ENABLE_OPTS = ["on", "off"]

DB_NAME = "postgres"
PGDATA = os.path.expanduser("~/pgdata")
RESULTS_FILE = "benchmark_results.csv"
LOG_FILE = "benchmark_log.txt"

# --- Server Control ---
def stop_server():
    print("Stopping server...")
    subprocess.run(["pg_ctl", "-D", PGDATA, "stop", "-m", "fast"], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    time.sleep(1)

def start_server(enable_bf, bf_mult, bf_hashes):
    # print(f"Starting server: BF={enable_bf}, Mult={bf_mult}, Hash={bf_hashes}")
    
    cmd = [
        "postgres", "-D", PGDATA,
        "-c", f"enable_bloom_filter={enable_bf}",
        "-c", f"bloom_filter_multiplier={bf_mult}",
        "-c", f"bloom_filter_hash_functions={bf_hashes}"
    ]
    
    proc = subprocess.Popen(cmd, stdout=open(LOG_FILE, "a"), stderr=subprocess.STDOUT)
    
    for _ in range(30):
        if subprocess.run(["pg_isready", "-q", "-d", DB_NAME], stdout=subprocess.DEVNULL).returncode == 0:
            return proc
        time.sleep(1)
    
    raise Exception("Server failed to start")

def run_psql(sql):
    subprocess.run(["psql", "-d", DB_NAME, "-c", sql], check=True)

def run_query(fact_table, dim_table):
    sql = f"EXPLAIN (ANALYZE, TIMING OFF) SELECT count(*) FROM {fact_table} f JOIN {dim_table} d ON f.int_value = d.int_value;"
    result = subprocess.run(["psql", "-d", DB_NAME, "-c", sql], capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"Query failed for {fact_table} x {dim_table}:", result.stderr)
        return -1
        
    for line in result.stdout.splitlines():
        if "Execution Time:" in line:
            return float(line.split()[2])
    return -1

# --- Main ---
def main():
    # Initialize results
    with open(RESULTS_FILE, "w") as f:
        writer = csv.writer(f)
        writer.writerow(["fact_rows", "dim_rows", "selectivity", "bf_enable", "bf_mult", "bf_hashes", "time_ms"])

    stop_server()

    try:
        for fact_rows in FACT_SIZES:
            for dim_rows in DIM_SIZES:
                if dim_rows > fact_rows: continue
                
                dim_table = f"dim_gemini_{dim_rows}"

                for sel in SELECTIVITIES:
                    sel_str = str(sel).replace('.', '_')
                    fact_table = f"fact_gemini_{fact_rows}_{dim_rows}_{sel_str}"
                    
                    # Loops
                    for bf_enable in BF_ENABLE_OPTS:
                        for bf_mult in BF_MULTS:
                            for bf_hashes in BF_HASHES:
                                
                                if bf_enable == "off" and (bf_mult != BF_MULTS[0] or bf_hashes != BF_HASHES[0]):
                                    continue
                                
                                print(f"Benchmarking: F={fact_rows} D={dim_rows} S={sel} | BF={bf_enable} M={bf_mult} H={bf_hashes}")
                                server_proc = start_server(bf_enable, bf_mult, bf_hashes)
                                
                                try:
                                    # Warmup (In-session)
                                    run_query(fact_table, dim_table)
                                    
                                    # Measure
                                    exec_time = run_query(fact_table, dim_table)
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
