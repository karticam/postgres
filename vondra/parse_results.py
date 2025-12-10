import re
import csv
import os
from collections import defaultdict

def parse_vondra_results(file_configs, output_file):
    # Key: (filter, bloom, parallel), Value: list of execution times
    aggregated_results = defaultdict(list)
    
    # Regex patterns
    # Pattern for res1: Parallel=(True|False)
    benchmarking_pattern_res1 = re.compile(r"Benchmarking: Filter < '(?P<val>[^']+)' \| Bloom=(?P<bloom>True|False) \| Parallel=(?P<parallel>True|False)")
    # Pattern for res2: Workers=\d+ (we ignore the value and hardcode 2 as per instruction, or just match it)
    benchmarking_pattern_res2 = re.compile(r"Benchmarking: Filter < '(?P<val>[^']+)' \| Bloom=(?P<bloom>True|False) \| Workers=(?P<workers>\d+)")
    
    execution_time_pattern = re.compile(r"Execution Time: (?P<time>[\d\.]+) ms")
    
    for input_file, config_rules in file_configs.items():
        try:
            with open(input_file, 'r') as f:
                lines = f.readlines()
            
            # File-specific state
            current_filter = None
            current_bloom = None
            current_parallel = None
            
            current_pattern = benchmarking_pattern_res1 if config_rules['type'] == 'res1' else benchmarking_pattern_res2
                
            for line in lines:
                line = line.strip()
                
                # Check for configuration line
                bench_match = current_pattern.search(line)
                if bench_match:
                    current_filter = bench_match.group('val')
                    current_bloom = bench_match.group('bloom')
                    
                    if config_rules['type'] == 'res1':
                        parallel_str = bench_match.group('parallel')
                        if parallel_str == 'True':
                            current_parallel = 4
                        else:
                            current_parallel = 0
                    else:
                        # res2: Always 2
                        current_parallel = 2
                    continue
                
                # Check for execution time
                time_match = execution_time_pattern.search(line)
                if time_match and current_filter is not None:
                    execution_time = float(time_match.group('time'))
                    key = (current_filter, current_bloom, current_parallel)
                    aggregated_results[key].append(execution_time)
        
        except FileNotFoundError:
            print(f"Error: File {input_file} not found.")
            continue
        except Exception as e:
            print(f"An error occurred processing {input_file}: {e}")
            continue

    # Write to CSV
    try:
        with open(output_file, 'w', newline='') as csvfile:
            fieldnames = ['Filter', 'Bloom', 'Parallel', 'Execution Time']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            writer.writeheader()
            
            # Sort by Filter (numeric first, then string), then Bloom, then Parallel
            def sort_key(k):
                val = try_int(k[0])
                is_str = isinstance(val, str)
                return (is_str, val, k[1], k[2])
            
            sorted_keys = sorted(aggregated_results.keys(), key=sort_key)
            
            for key in sorted_keys:
                times = aggregated_results[key]
                avg_time = sum(times) / len(times)
                writer.writerow({
                    'Filter': key[0],
                    'Bloom': key[1],
                    'Parallel': key[2],
                    'Execution Time': f"{avg_time:.3f}"
                })
            
        print(f"Successfully processed {len(aggregated_results)} configurations to {output_file}")
    except Exception as e:
        print(f"An error occurred writing to CSV: {e}")

def try_int(val):
    try:
        return int(val)
    except ValueError:
        return val

if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    file_configs = {
        os.path.join(script_dir, "vondra_res1.txt"): {'type': 'res1'},
        os.path.join(script_dir, "vondra_res2.txt"): {'type': 'res2'}
    }
    
    output_path = os.path.join(script_dir, "parsed_vondra_res1.csv")
    
    parse_vondra_results(file_configs, output_path)
