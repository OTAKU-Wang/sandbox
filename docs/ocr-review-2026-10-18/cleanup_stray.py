import glob, os, sys
root = r'D:/workspace/sandbox'
# The stray artifact came from a mangled -c command; it is untracked and was created by this session only.
cands = []
for name in os.listdir(root):
    p = os.path.join(root, name)
    if os.path.isfile(p) and ('to_sql_condition' in name or 'user_region' in name):
        cands.append(p)
for p in cands:
    print('size', os.path.getsize(p), 'mtime', os.path.getmtime(p), repr(p))
if '--rm' in sys.argv:
    for p in cands:
        os.remove(p)
        print('removed', repr(os.path.basename(p)))
