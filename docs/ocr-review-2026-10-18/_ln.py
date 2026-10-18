
import re, sys
path = sys.argv[1]
pats = sys.argv[2:]
lines = open(path, encoding='utf-8').read().splitlines()
for i, l in enumerate(lines, 1):
    for p in pats:
        if re.search(p, l):
            print(f"{i}: {l.rstrip()}")
            break
