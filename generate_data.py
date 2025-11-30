import os
import random
import subprocess
import sys

# --- Configuration ---
FACT_SIZES = [100_000, 1_000_000, 10_000_000, 100_000_000]
DIM_SIZES = [10_000, 100_000, 1_000_000, 10_000_000]
SELECTIVITIES = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]

DB_NAME = "postgres"

def get_random_string():
    return random.randbytes(4).hex()

def run_psql(sql):
    subprocess.run(["psql", "-d", DB_NAME, "-c", sql], check=True)

def table_exists(table_name):
    res = subprocess.run(
        ["psql", "-d", DB_NAME, "-tAc", f"SELECT 1 FROM pg_tables WHERE tablename='{table_name}'"], 
        capture_output=True, text=True
    )
    return res.stdout.strip() == "1"

def generate_dim_table(dim_rows):
    table_name = f"dim_gemini_{dim_rows}"
    if table_exists(table_name):
        print(f"Table {table_name} already exists. Skipping.")
        return

    print(f"Generating {table_name}...")
    filename = f"{table_name}.csv"
    
    with open(filename, "w") as f:
        for i in range(dim_rows):
            f.write(f"{i},{get_random_string()}\n")
            
    try:
        run_psql(f"CREATE TABLE {table_name} (int_value INTEGER PRIMARY KEY, str_value CHAR(8));")
        run_psql(f"\\copy {table_name} FROM '{filename}' WITH CSV")
        run_psql(f"ANALYZE {table_name};")
    finally:
        if os.path.exists(filename): os.remove(filename)

def generate_fact_table(fact_rows, dim_rows, selectivity):
    sel_str = str(selectivity).replace('.', '_')
    table_name = f"fact_gemini_{fact_rows}_{dim_rows}_{sel_str}"
    
    if table_exists(table_name):
        print(f"Table {table_name} already exists. Skipping.")
        return

    print(f"Generating {table_name}...")
    filename = f"{table_name}.csv"
    
    num_matching = int(fact_rows * selectivity)
    num_non_matching = fact_rows - num_matching
    
    with open(filename, "w") as f:
        # Matching keys (0 to dim_rows-1)
        for _ in range(num_matching):
            key = random.randint(0, dim_rows - 1)
            f.write(f"{key},{get_random_string()}\n")
        # Non-matching keys (dim_rows to 2*dim_rows + ...)
        for _ in range(num_non_matching):
            key = random.randint(dim_rows, dim_rows * 2 + num_non_matching)
            f.write(f"{key},{get_random_string()}\n")
            
    try:
        run_psql(f"CREATE TABLE {table_name} (int_value INTEGER, str_value CHAR(8));")
        run_psql(f"\\copy {table_name} FROM '{filename}' WITH CSV")
        run_psql(f"ANALYZE {table_name};")
    finally:
        if os.path.exists(filename): os.remove(filename)

def main():
    # Check connection
    if subprocess.run(["pg_isready", "-q", "-d", DB_NAME], stdout=subprocess.DEVNULL).returncode != 0:
        print("Error: Postgres server is not running. Please start it first.")
        sys.exit(1)

    # 1. Generate Dimensions
    print("--- Generating Dimensions ---")
    for dim_rows in DIM_SIZES:
        generate_dim_table(dim_rows)

    # 2. Generate Facts
    print("--- Generating Facts ---")
    for fact_rows in FACT_SIZES:
        for dim_rows in DIM_SIZES:
            if dim_rows > fact_rows: continue
            
            for sel in SELECTIVITIES:
                generate_fact_table(fact_rows, dim_rows, sel)

    print("Done. All tables generated and analyzed.")

if __name__ == "__main__":
    main()
