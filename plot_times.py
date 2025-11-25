import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv("benchmark_option_A.csv")

lip = df[df['branch'] == 'karticam_aarryas/lip']
cp1 = df[df['branch'] == 'cp1']

plt.figure(figsize=(10,6))

plt.plot(lip['run'], lip['execution_ms'], marker='o', label="LIP (base, 10 runs)")
plt.plot(cp1['run'], cp1['execution_ms'], marker='o', label="cp1 (modified, 10 runs)")

plt.xlabel("Run #")
plt.ylabel("Execution Time (ms)")
plt.title("COUNT() Benchmark — LIP vs cp1 (Warm Cache, Restart Per Branch)")
plt.legend()
plt.grid(True)

plt.savefig("benchmark_option_A.png")
print("Plot saved as benchmark_option_A.png")
