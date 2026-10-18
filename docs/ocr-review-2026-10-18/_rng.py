
import sys
path, rng = sys.argv[1], sys.argv[2]
a, b = map(int, rng.split('-'))
lines = open(path, encoding='utf-8').read().splitlines()
for i in range(a, min(b, len(lines)) + 1):
    print(f"{i}: {lines[i-1]}")
