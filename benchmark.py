#!usr/bin/env python3
import subprocess
import time
import os
import csv
import sys

# --- Configuration ---
# FACT_SIZES = [100_000, 1_000_000, 10_000_000, 100_000_000]
# DIM_SIZES = [10_000, 100_000, 1_000_000, 10_000_000]
FACT_SIZES = [100_000_000]
DIM_SIZES = [10_000_000]
SELECTIVITIES = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
BF_MULTS = [0.5, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5]
BF_HASHES = [1, 2, 3, 4, 5]
WORKERS = [0, 2, 4]

# FACT_SIZES = [1_000_000]
# DIM_SIZES = [100_000]
# SELECTIVITIES = [0.5]
# BF_MULTS = [1.0]
# BF_HASHES = [3]

# FACT_SIZES = [1000]
# DIM_SIZES = [100]
# SELECTIVITIES = [0.5]
# BF_MULTS = [1.0]
# BF_HASHES = [3]
BF_ENABLE_OPTS = ["on", "off"]

DB_NAME = "postgres"
PGDATA = os.environ.get("PGDATA", os.path.expanduser("~/pgdata"))
RESULTS_FILE = "benchmark_results.csv"
LOG_FILE = "benchmark_log.txt"

# --- Server Control ---
def stop_server():
    print("Stopping server...")
    subprocess.run(["pg_ctl", "-D", PGDATA, "stop", "-m", "fast"], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    time.sleep(1)

def start_server():
    print("Starting server...")
    cmd = ["postgres", "-D", PGDATA]
    
    log_f = open(LOG_FILE, "a")
    proc = subprocess.Popen(cmd, stdout=log_f, stderr=subprocess.STDOUT)
    
    for _ in range(30):
        if subprocess.run(["pg_isready", "-q", "-d", DB_NAME], stdout=subprocess.DEVNULL).returncode == 0:
            return proc, log_f
        time.sleep(1)
    
    log_f.close()
    raise Exception("Server failed to start")

# --- Persistent Connection ---
class PsqlConnection:
    def __init__(self, db_name):
        # Start psql in interactive mode but reading from pipe
        # -X: no .psqlrc
        # -q: quiet (no welcome message)
        self.proc = subprocess.Popen(
            ["psql", "-X", "-q", "-d", db_name],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, # Merge stderr to capture errors
            text=True,
            bufsize=0 # Unbuffered
        )
        self.sentinel = "___END_OF_QUERY___"

    def run(self, sql):
        # Send query followed by sentinel echo
        # We use \echo to print the sentinel to stdout
        full_cmd = f"{sql}\n\\echo {self.sentinel}\n"
        
        try:
            self.proc.stdin.write(full_cmd)
            self.proc.stdin.flush()
        except BrokenPipeError:
            print("Error: psql process died.")
            return []

        output = []
        while True:
            line = self.proc.stdout.readline()
            if not line: break # EOF
            
            clean_line = line.strip()
            if clean_line == self.sentinel:
                break
            
            output.append(clean_line)
            
        return output

    def close(self):
        if self.proc:
            self.proc.stdin.close()
            self.proc.terminate()
            self.proc.wait()

# --- Main ---
def main():
    # Initialize results
    with open(RESULTS_FILE, "w") as f:
        writer = csv.writer(f)
        writer.writerow(["fact_rows", "dim_rows", "selectivity", "bf_enable", "bf_mult", "bf_hashes", "workers", "time_ms"])

    stop_server()
    server_proc, log_f = start_server()
    conn = None

    try:
        conn = PsqlConnection(DB_NAME)
        
        # Open CSV once for appending
        with open(RESULTS_FILE, "a") as csv_f:
            writer = csv.writer(csv_f)
            
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
                                    for workers in WORKERS:
                                    
                                      if bf_enable == "off" and (bf_mult != BF_MULTS[0] or bf_hashes != BF_HASHES[0]):
                                          continue
                                      
                                      print(f"Benchmarking: F={fact_rows} D={dim_rows} S={sel} | BF={bf_enable} M={bf_mult} H={bf_hashes} T={workers}")
                                      
                                      # Construct SQL with SET commands
                                      set_cmds = f"SET enable_bloom_filter={bf_enable}; SET bloom_filter_multiplier={bf_mult}; SET bloom_filter_hash_functions={bf_hashes}; SET max_parallel_workers_per_gather={workers};"
                                      query_sql = f"{set_cmds} EXPLAIN (ANALYZE, TIMING OFF) SELECT count(*) FROM {fact_table} f JOIN {dim_table} d ON f.int_value = d.int_value;"
                                      
                                      # Warmup
                                      conn.run(query_sql)
                                      
                                      # Measure 3 times and take minimum
                                      min_exec_time = float('inf')
                                      for i in range(3):
                                          output = conn.run(query_sql)
                                          
                                          current_time = -1
                                          for line in output:
                                              if "Execution Time:" in line:
                                                  try:
                                                      current_time = float(line.split()[2])
                                                  except:
                                                      pass
                                                  break
                                          
                                          if current_time != -1:
                                              if current_time < min_exec_time:
                                                  min_exec_time = current_time
                                          else:
                                              print(f"  Query failed or parse error in run {i+1}. Output snippet: {output[:3]}")
                                      
                                      exec_time = min_exec_time if min_exec_time != float('inf') else -1

                                      if exec_time == -1:
                                          print(f"  Query failed for all runs.")
                                      else:
                                          print(f"  Result (min of 3): {exec_time} ms")
                                      
                                      # Write to open CSV file
                                      writer.writerow([fact_rows, dim_rows, sel, bf_enable, bf_mult, bf_hashes, workers, exec_time])
                                      csv_f.flush() # Ensure data is written to disk

    except KeyboardInterrupt:
        print("Benchmark interrupted.")
    finally:
        if conn: conn.close()
        if server_proc:
            server_proc.terminate()
            server_proc.wait()
        if log_f:
            log_f.close()
        stop_server()

if __name__ == "__main__":
    main()
