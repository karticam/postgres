#!/usr/bin/env python3
import subprocess
import time
import os
import csv
import sys

# ... (imports remain the same)
import argparse

# --- Configuration ---
# PGDATA will be set in main() based on arguments
PGDATA = os.path.expanduser("~/pgdata") 
DB_NAME = "postgres"

# Determine script directory to ensure files are saved in ./vondra
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "vondra_benchmark_log.txt")

# Filter criteria to vary selectivity
ALL_FILTERS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', 'a', 'b', 'c', 'd', 'e', 'f']

# Test configurations
CONFIGS = [
    {"bloom": True, "independent": False, "label": "Shared_Bloom"},     # Bloom ON, Shared (Original)
    {"bloom": True, "independent": True,  "label": "Independent_Bloom"}, # Bloom ON, Independent (New Optimization)
    {"bloom": False, "independent": False, "label": "No_Bloom"}         # Bloom OFF
]

def stop_server():
    print("Stopping server...")
    subprocess.run(["pg_ctl", "-D", PGDATA, "stop", "-m", "fast"], 
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)

def start_server(bloom_on, independent_on, workers):
    print(f"Starting server: Bloom={bloom_on}, Independent={independent_on}, Workers={workers}")
    
    cmd = [
        "postgres", "-D", PGDATA,
        "-c", f"enable_bloom_filter={'on' if bloom_on else 'off'}",
        "-c", f"enable_independent_bloom_filter={'on' if independent_on else 'off'}",
    ]
    
    if workers > 0:
        cmd.extend([
            "-c", f"max_parallel_workers_per_gather={workers}",
            "-c", f"max_parallel_workers={workers * 2}",
            "-c", "enable_parallel_hash=on"
        ])
    else:
        cmd.extend([
            "-c", "max_parallel_workers_per_gather=0",
            "-c", "enable_parallel_hash=off"
        ])

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    for _ in range(30):
        if subprocess.run(["pg_isready", "-q", "-d", DB_NAME], stdout=subprocess.DEVNULL).returncode == 0:
            time.sleep(1)
            return proc
        time.sleep(1)
    
    raise Exception("Server failed to start")

def run_query(filter_val):
    sql = f"EXPLAIN (ANALYZE, TIMING ON, SUMMARY ON) SELECT COUNT(fval) FROM fact JOIN dim USING (id) WHERE dval < '{filter_val}'"
    result = subprocess.run(["psql", "-d", DB_NAME, "-c", sql], capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"Query failed for filter < '{filter_val}':", result.stderr)
        return -1, 0
    print(result.stdout)
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
    global PGDATA
    
    parser = argparse.ArgumentParser(description="Run Vondra Benchmark")
    parser.add_argument("--filter", help="Specific filter criteria to run (e.g. '1', 'a'). If not set, runs all.", default=None)
    parser.add_argument("--runs", type=int, help="Number of runs per configuration", default=3)
    parser.add_argument("--skip-data-check", action="store_true", help="Skip checking/generating data")
    parser.add_argument("--env", choices=["local", "aws"], default="local", help="Environment to run in (local or aws)")
    parser.add_argument("--workers", type=int, help="Specific number of parallel workers per gather. If not set, runs [0, 2, 4].", default=None)
    args = parser.parse_args()

    # Configure Environment
    if args.env == "aws":
        PGDATA = "/mnt/pgdata/data"
        # Add AWS bin path
        os.environ["PATH"] = "/mnt/pgdata/pg_install/bin:" + os.environ["PATH"]
        print(f"Running in AWS mode. PGDATA={PGDATA}")
    else:
        PGDATA = os.path.expanduser("~/pgdata")
        print(f"Running in Local mode. PGDATA={PGDATA}")

    filters_to_run = [args.filter] if args.filter else ALL_FILTERS
    
    # Determine worker configurations
    if args.workers is not None:
        worker_opts = [args.workers]
    else:
        worker_opts = [0, 2, 4]

    stop_server()

    try:
        if not args.skip_data_check:
            print("--- Checking Data ---")
            # Start with 0 workers for check
            temp_proc = start_server(False, False, 0)
            check_res = subprocess.run(
                ["psql", "-d", DB_NAME, "-tAc", "SELECT to_regclass('fact')"], 
                capture_output=True, text=True
            )
            if not check_res.stdout.strip():
                print("Tables not found. Generating data...")
                gen_script = os.path.join(SCRIPT_DIR, "generate_vondra_data.py")
                # Pass env to generation script
                subprocess.run(["python3", gen_script, "--env", args.env], check=True)
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
                writer.writerow(["filter_val", "config_label", "bloom_enabled", "independent_enabled", "worker_conf", "avg_time_ms", "avg_workers", "runs"])
            
            for workers in worker_opts:
                for conf in CONFIGS:
                    bloom_on = conf["bloom"]
                    independent_on = conf["independent"]
                    label = conf["label"]
                    
                    server_proc = start_server(bloom_on, independent_on, workers)
                    
                    try:
                        print(f"Benchmarking: Filter < '{filter_val}' | Label={label} | Workers={workers}")
                        
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
                                writer.writerow([filter_val, label, bloom_on, independent_on, workers, avg_time, avg_workers, len(times)])
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
