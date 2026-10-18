import ast, os, collections
ROOT = r'D:/workspace/sandbox'
mod2file = {}
for base, dirs, files in os.walk(os.path.join(ROOT, 'app')):
    dirs[:] = [d for d in dirs if d not in ('_stubs', '__pycache__')]
    for f in files:
        if not f.endswith('.py'): continue
        p = os.path.join(base, f)
        rel = os.path.relpath(p, ROOT).replace(os.sep, '/')
        mod = rel[:-3].replace('/', '.')
        if mod.endswith('.__init__'): mod = mod[:-9]
        mod2file[mod] = rel
edges = collections.defaultdict(set)   # importer -> imported app modules
for mod, rel in mod2file.items():
    try:
        tree = ast.parse(open(os.path.join(ROOT, rel), encoding='utf-8').read())
    except Exception as e:
        print('PARSE FAIL', rel, e); continue
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith('app.'):
            edges[mod].add(n.module)
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.startswith('app.'):
                    edges[mod].add(a.name)
# reverse graph
rev = collections.defaultdict(set)
for src, dsts in edges.items():
    for d in dsts:
        rev[d].add(src)
        # also map submodule import to the module file if it is a package/module
        if d not in mod2file and d + '.__init__' not in mod2file:
            pass
def direct(t):
    return sorted(rev.get(t, []))
def closure(t):
    seen = set(); stack = list(rev.get(t, []))
    while stack:
        s = stack.pop()
        if s in seen: continue
        seen.add(s)
        stack.extend(rev.get(s, []))
    return sorted(seen)
for t in ('app.services.rls_engine', 'app.services.pg_rls_manager', 'app.services.db_access_proxy'):
    print('=' * 60)
    print('TARGET', t)
    print('  direct importers :', direct(t) or 'NONE')
    up = closure(t)
    print('  transitive importers :', up or 'NONE')
    print('  any in app/api? :', [m for m in up if m.startswith('app.api')] or 'NO')
    print('  main.py reaches it? :', 'app.main' in up)
# also: does anything import them by string (importlib) ?
