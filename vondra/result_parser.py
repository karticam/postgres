#!/usr/bin/env python3
import csv
import sys
from pathlib import Path

INPUT_PATTERN = "vondra_benchmark_results_[0-9a-fA-F].csv"
OUTPUT_FILE = Path("vondra_avg_times.txt")


def read_avg_times(path: Path):
    with path.open(newline="") as csvfile:
        reader = csv.DictReader(csvfile)
        rows = [
            row
            for row in reader
            if any((value or "").strip() for value in row.values())
        ]

    if len(rows) < 4:
        raise ValueError(f"expected at least 4 data rows, found {len(rows)}")

    avg_times = []
    for idx in range(4):
        value = (rows[idx].get("avg_time_ms") or "").strip()
        if not value:
            raise ValueError(f"missing avg_time_ms in row {idx + 1}")
        avg_times.append(value)

    return avg_times


def write_grouped_times(row_groups, output_path: Path):
    with output_path.open("w") as outfile:
        for idx, group in enumerate(row_groups):
            for value in group:
                outfile.write(f"{value}\n")
            if idx != len(row_groups) - 1:
                outfile.write("\n")


def main() -> int:
    files = sorted(Path(".").glob(INPUT_PATTERN))
    if not files:
        print(f"No files matched pattern '{INPUT_PATTERN}'. Nothing to do.")
        return 0

    row_groups = [[] for _ in range(4)]

    for path in files:
        try:
            avg_times = read_avg_times(path)
        except Exception as exc:
            print(f"Failed to parse {path.name}: {exc}", file=sys.stderr)
            return 1

        for idx, value in enumerate(avg_times):
            row_groups[idx].append(value)

    write_grouped_times(row_groups, OUTPUT_FILE)
    print(
        f"Wrote {len(files)} rows for each of 4 groups "
        f"to {OUTPUT_FILE} in filename-sorted order."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())