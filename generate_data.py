import random
import string
import subprocess
import os
import time

# Configuration
SELECTIVITY = 0.5  # 50% of fact table rows will have keys present in dim table
FACT_ROWS = 100_000_000
DIM_ROWS = 10_000_000
BATCH_SIZE = 1_000_000

DB_NAME = "postgres"
FACT_TABLE = "fact_gemini"
DIM_TABLE = "dim_gemini"

def get_random_string():
    # random.randbytes(4).hex() gives 8 hex characters, which is an 8-byte string.
    # This is much faster than random.choices
    return random.randbytes(4).hex()

def run_sql(sql):
    subprocess.run(["psql", "-d", DB_NAME, "-c", sql], check=True)

def generate_dim_file(filename):
    print(f"Generating {DIM_ROWS} rows for {DIM_TABLE}...")
    with open(filename, "w") as f:
        for i in range(DIM_ROWS):
            # Keys 0 to DIM_ROWS - 1
            s_val = get_random_string()
            f.write(f"{i},{s_val}\n")
            if i % 1_000_000 == 0:
                print(f"  Generated {i} rows...", end='\r')
    print(f"  Finished generating {DIM_TABLE} data.")

def generate_fact_file(filename):
    print(f"Generating {FACT_ROWS} rows for {FACT_TABLE} with selectivity {SELECTIVITY}...")
    
    num_matching = int(FACT_ROWS * SELECTIVITY)
    num_non_matching = FACT_ROWS - num_matching
    
    # We'll generate in batches to keep memory usage low but write efficiently
    with open(filename, "w") as f:
        rows_written = 0
        
        # 1. Generate matching rows
        # Keys chosen uniformly from 0 to DIM_ROWS - 1
        for _ in range(num_matching):
            key = random.randint(0, DIM_ROWS - 1)
            s_val = get_random_string()
            f.write(f"{key},{s_val}\n")
            rows_written += 1
            if rows_written % 1_000_000 == 0:
                print(f"  Generated {rows_written} rows...", end='\r')
                
        # 2. Generate non-matching rows
        # Keys chosen uniformly from DIM_ROWS to 2*DIM_ROWS (guaranteed no overlap)
        for _ in range(num_non_matching):
            key = random.randint(DIM_ROWS, DIM_ROWS * 2 + num_non_matching) 
            s_val = get_random_string()
            f.write(f"{key},{s_val}\n")
            rows_written += 1
            if rows_written % 1_000_000 == 0:
                print(f"  Generated {rows_written} rows...", end='\r')

    print(f"  Finished generating {FACT_TABLE} data.")

def main():
    start_time = time.time()
    
    # 1. Drop old tables
    print("Dropping old tables...")
    run_sql("DROP TABLE IF EXISTS t1, t2, fact_gemini, dim_gemini CASCADE;")
    
    # 2. Create new tables
    print("Creating new tables...")
    create_sql = f"""
    CREATE TABLE {FACT_TABLE} (
        int_value INTEGER,
        str_value CHAR(8)
    );
    CREATE TABLE {DIM_TABLE} (
        int_value INTEGER,
        str_value CHAR(8)
    );
    """
    run_sql(create_sql)
    
    # 3. Generate CSVs
    dim_csv = "dim_gemini.csv"
    fact_csv = "fact_gemini.csv"
    
    generate_dim_file(dim_csv)
    generate_fact_file(fact_csv)
    
    # 4. Load Data
    print("Loading data into Postgres...")
    
    print(f"Loading {DIM_TABLE}...")
    subprocess.run(["psql", "-d", DB_NAME, "-c", f"\\copy {DIM_TABLE} FROM '{dim_csv}' WITH CSV"], check=True)
    
    print(f"Loading {FACT_TABLE}...")
    subprocess.run(["psql", "-d", DB_NAME, "-c", f"\\copy {FACT_TABLE} FROM '{fact_csv}' WITH CSV"], check=True)
    
    # 5. Cleanup
    print("Cleaning up CSV files...")
    os.remove(dim_csv)
    os.remove(fact_csv)
    
    print(f"Done! Total time: {time.time() - start_time:.2f} seconds")

if __name__ == "__main__":
    main()
