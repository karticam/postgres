import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv("execution_times_small.csv")

lip = df[df['branch'] == 'karticam_aarryas/lip']
cp1 = df[df['branch'] == 'cp1']

plt.figure(figsize=(10,6))
plt.plot(lip['run'], lip['execution_ms'], marker='o', label="LIP (10 runs)")
plt.plot(cp1['run'], cp1['execution_ms'], marker='o', label="cp1 (10 runs)")

plt.xlabel("Run #")
plt.ylabel("Execution Time (ms)")
plt.title("Small Bloom Join Benchmark — LIP vs cp1 (warm cache, per-branch restart)")
plt.legend()
plt.grid(True)

plt.savefig("benchmark_small.png")
print("Plot saved as benchmark_small.png")
