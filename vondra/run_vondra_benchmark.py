#!/usr/bin/env python3
import subprocess
import time
import os
import csv
import sys

# ... (imports remain the same)
import argparse

# --- Configuration ---
PGDATA = os.path.expanduser("~/pgdata")
DB_NAME = "postgres"

# Determine script directory to ensure files are saved in ./vondra
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "vondra_benchmark_log.txt")

# Filter criteria to vary selectivity
ALL_FILTERS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', 'a', 'b', 'c', 'd', 'e', 'f']

# Test configurations
BLOOM_OPTS = [True, False]
PARALLEL_OPTS = [True, False]

def stop_server():
    print("Stopping server...")
    subprocess.run(["pg_ctl", "-D", PGDATA, "stop", "-m", "fast"], 
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)

def start_server(bloom_on, parallel_on):
    print(f"Starting server: Bloom={bloom_on}, Parallel={parallel_on}")
    
    cmd = [
        "postgres", "-D", PGDATA,
        "-c", f"enable_bloom_filter={'on' if bloom_on else 'off'}",
    ]
    
    if parallel_on:
        cmd.extend([
            "-c", "max_parallel_workers_per_gather=4",
            "-c", "max_parallel_workers=8",
            "-c", "enable_parallel_hash=on"
        ])
    else:
        cmd.extend([
            "-c", "max_parallel_workers_per_gather=0",
            "-c", "enable_parallel_hash=off"
        ])

    with open(LOG_FILE, "a") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
    
    for _ in range(30):
        if subprocess.run(["pg_isready", "-q", "-d", DB_NAME], stdout=subprocess.DEVNULL).returncode == 0:
            time.sleep(1)
            return proc
        time.sleep(1)
    
    raise Exception("Server failed to start")

def run_query(filter_val):
    sql = f"EXPLAIN (ANALYZE, TIMING OFF, SUMMARY ON) SELECT COUNT(fval) FROM fact JOIN dim USING (id) WHERE dval < '{filter_val}'"
    result = subprocess.run(["psql", "-d", DB_NAME, "-c", sql], capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"Query failed for filter < '{filter_val}':", result.stderr)
        return -1, 0
        
    exec_time = -1
    workers = 0
    
    for line in result.stdout.splitlines():
        if "Execution Time:" in line:
            exec_time = float(line.split()[2])
        if "Workers Launched:" in line:
            try:
                workers = int(line.strip().split()[-1])
            except:
                pass
                
    return exec_time, workers

def main():
    parser = argparse.ArgumentParser(description="Run Vondra Benchmark")
    parser.add_argument("--filter", help="Specific filter criteria to run (e.g. '1', 'a'). If not set, runs all.", default=None)
    parser.add_argument("--runs", type=int, help="Number of runs per configuration", default=10)
    parser.add_argument("--skip-data-check", action="store_true", help="Skip checking/generating data")
    args = parser.parse_args()

    filters_to_run = [args.filter] if args.filter else ALL_FILTERS

    # Initialize results file logic moved inside loop

    stop_server()

    try:
        if not args.skip_data_check:
            print("--- Checking Data ---")
            temp_proc = start_server(False, False)
            check_res = subprocess.run(
                ["psql", "-d", DB_NAME, "-tAc", "SELECT to_regclass('fact')"], 
                capture_output=True, text=True
            )
            if not check_res.stdout.strip():
                print("Tables not found. Generating data...")
                gen_script = os.path.join(SCRIPT_DIR, "generate_vondra_data.py")
                subprocess.run(["python3", gen_script], check=True)
            else:
                print("Tables found. Skipping generation.")
            temp_proc.terminate()
            temp_proc.wait()
            stop_server()

        # Main Benchmark Loop
        # Outer loop: Filter (Selectivity)
        for filter_val in filters_to_run:
            print(f"\n=== Starting Study for Filter < '{filter_val}' ===")
            
            # Define results file for this filter
            results_file = os.path.join(SCRIPT_DIR, f"vondra_benchmark_results_{filter_val}.csv")
            
            # Truncate file and write header
            with open(results_file, "w") as f:
                writer = csv.writer(f)
                writer.writerow(["filter_val", "bloom_enabled", "parallel_enabled", "avg_time_ms", "avg_workers", "runs"])
            
            for parallel_on in PARALLEL_OPTS:
                for bloom_on in BLOOM_OPTS:
                    
                    server_proc = start_server(bloom_on, parallel_on)
                    
                    try:
                        print(f"Benchmarking: Filter < '{filter_val}' | Bloom={bloom_on} | Parallel={parallel_on}")
                        
                        times = []
                        worker_counts = []
                        
                        for i in range(args.runs):
                            t, w = run_query(filter_val)
                            if t != -1:
                                times.append(t)
                                worker_counts.append(w)
                        
                        if times:
                            avg_time = sum(times) / len(times)
                            avg_workers = sum(worker_counts) / len(worker_counts)
                            print(f"  Average Time: {avg_time:.2f} ms | Avg Workers: {avg_workers:.1f}")
                            
                            with open(results_file, "a") as f:
                                writer = csv.writer(f)
                                writer.writerow([filter_val, bloom_on, parallel_on, avg_time, avg_workers, len(times)])
                        else:
                            print("  All runs failed.")
                            
                    finally:
                        server_proc.terminate()
                        server_proc.wait()
                        stop_server()

    except KeyboardInterrupt:
        print("\nBenchmark interrupted.")
    except Exception as e:
        print(f"\nAn error occurred: {e}")
    finally:
        stop_server()

if __name__ == "__main__":
    main()
