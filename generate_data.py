import os
import random
import subprocess
import sys

# --- Configuration ---
FACT_SIZES = [100_000, 1_000_000, 10_000_000, 100_000_000]
DIM_SIZES = [10_000, 100_000, 1_000_000, 10_000_000]
SELECTIVITIES = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]

# FACT_SIZES = [1_000_000]
# DIM_SIZES = [100_000]
# SELECTIVITIES = [0.5]

# FACT_SIZES = [1000]
# DIM_SIZES = [100]
# SELECTIVITIES = [0.5]

DB_NAME = "postgres"

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
    
    # Use SQL generation for speed
    # int_value: 0..dim_rows-1
    # str_value: random 8 chars
    sql = f"""
    CREATE UNLOGGED TABLE {table_name} (int_value INTEGER PRIMARY KEY, str_value CHAR(8));
    INSERT INTO {table_name} (int_value, str_value)
    SELECT i, substr(md5(random()::text), 1, 8)
    FROM generate_series(0, {dim_rows} - 1) AS i;
    ANALYZE {table_name};
    """
    run_psql(sql)

def generate_fact_table(fact_rows, dim_rows, selectivity):
    sel_str = str(selectivity).replace('.', '_')
    table_name = f"fact_gemini_{fact_rows}_{dim_rows}_{sel_str}"
    
    if table_exists(table_name):
        print(f"Table {table_name} already exists. Skipping.")
        return

    print(f"Generating {table_name}...")
    
    num_matching = int(fact_rows * selectivity)
    num_non_matching = fact_rows - num_matching
    
    # Use SQL generation
    # Matching: random(0, dim_rows-1)
    # Non-Matching: random(dim_rows, 2*dim_rows + ...)
    
    sql = f"""
    CREATE UNLOGGED TABLE {table_name} (int_value INTEGER, str_value CHAR(8));
    
    -- Matching rows
    INSERT INTO {table_name} (int_value, str_value)
    SELECT floor(random() * {dim_rows})::int, substr(md5(random()::text), 1, 8)
    FROM generate_series(1, {num_matching});
    
    -- Non-matching rows
    INSERT INTO {table_name} (int_value, str_value)
    SELECT floor(random() * ({dim_rows} + {num_non_matching}) + {dim_rows})::int, substr(md5(random()::text), 1, 8)
    FROM generate_series(1, {num_non_matching});
    
    ANALYZE {table_name};
    """
    run_psql(sql)

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
