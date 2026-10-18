import sys
sys.path.insert(0, r'D:/workspace/sandbox')
from app.services.rls_engine import RLSEngine, RLSPolicy, RLSContext, PolicyType, SensitivityLevel

e = RLSEngine()
def sec(msg): print('\n===== ' + msg)

sec('510 region fail-open: allowed set, user_region missing')
p = RLSPolicy('r1','region policy',PolicyType.REGION, conditions={'allowed_regions':['cn-north','cn-east'],'region_column':'region'})
print(' ctx(user_region=None) ->', repr(p.to_sql_condition({'user_region': None})))
print(' ctx(allowed empty)    ->', repr(RLSPolicy('r1b','x',PolicyType.REGION,conditions={'region_column':'region'}).to_sql_condition({'user_region':'cn-north'})))

sec('511 sensitivity ceiling never applied')
sp = RLSPolicy('s1','sens',PolicyType.SENSITIVITY, conditions={'max_allowed_level':'public','sensitivity_column':'security_level'})
print(' max=public user=secret ->', repr(sp.to_sql_condition({'user_clearance':'secret'})))
print(' max=public user=public ->', repr(sp.to_sql_condition({'user_clearance':'public'})))
print(' max=public user=None   ->', repr(sp.to_sql_condition({'user_clearance':None})))
bad = RLSPolicy('s2','sens',PolicyType.SENSITIVITY, conditions={'max_allowed_level':'Secret'})   # typo capital S
print(' max="Secret"(typo) user=public ->', repr(bad.to_sql_condition({'user_clearance':'public'})))

sec('512 identifier-position injection via policy conditions')
inj = RLSPolicy('r2','x',PolicyType.REGION, conditions={'allowed_regions':['a'],'region_column':"region = 'a' OR 1=1 --"})
print(' ->', inj.to_sql_condition({'user_region':'a'}))

sec('513 escape leaves backslash untouched')
esc = RLSPolicy._escape("\' OR 1=1 --")
print(' escaped ->', repr(esc))

sec('514 rfind WHERE: EXISTS subquery gets the RLS condition')
sql = "SELECT * FROM t WHERE EXISTS (SELECT 1 FROM u WHERE u.id = t.id)"
print(' ->', e.inject_rls_where(sql, "region = 'cn-north'"))

sec('515 newline before WHERE is invisible to the " WHERE " test')
sql2 = "SELECT * FROM t\nWHERE x = 1"
print(' ->', repr(e.inject_rls_where(sql2, "region = 'cn-north'")))

sec('516 inject_filter numeric position: unvalidated user_clearance')
print(' ->', e.inject_filter("SELECT * FROM t", {'type':'sensitivity','column':'sec'}, user_clearance="3 OR 1=1"))
print(' ->', e.inject_filter("SELECT * FROM t", {'type':'region','column':"region) OR 1=1 --"}, user_region='a'))

sec('517 row-level region check allows cross-region rows')
print(" user in cn-north, allowed=[cn-north,cn-east], row region=cn-east ->",
      e.evaluate({'type':'region','allowed_regions':['cn-north','cn-east']}, {'region':'cn-east'}, user_region='cn-north'))

sec('518 row-level sensitivity ceiling')
pol = {'type':'sensitivity','max_level':3}
print(' clearance=1 max_level=3 row_sens=1 ->', e.evaluate(pol, {'sensitivity':1}, user_clearance=1))
print(' clearance=3 max_level=3 row_sens=3 ->', e.evaluate(pol, {'sensitivity':3}, user_clearance=3))
print(' clearance=3 max_level=3 row missing sensitivity ->', e.evaluate(pol, {}, user_clearance=3))

sec('519 unknown policy_type -> allow')
print(" type='regio' (typo) ->", e.evaluate({'type':'regio','allowed_regions':['a']}, {'region':'zzz'}, user_region='a'))
print(" type=''            ->", e.evaluate({}, {'region':'zzz'}))

sec('1398 empty IN () / 1399 table_name ignored')
e2 = RLSEngine(); e2.add_policy(sp)
ctx = RLSContext(user_id='u1')      # defaults: user_region=None, user_clearance=None
print(' evaluate_sql(default ctx) ->', repr(e2.evaluate_sql(ctx, table_name='whatever')))
e2.add_policy(RLSPolicy('s3','sens2',PolicyType.SENSITIVITY, conditions={'max_allowed_level':'confidential'}))
print(' evaluate_sql(user_clearance=internal) ->', repr(e2.evaluate_sql(RLSContext(user_clearance='internal'))))

sec('1396/1397 inject placement')
print(' subquery GROUP BY ->', e.inject_rls_where("SELECT * FROM (SELECT a FROM t GROUP BY a) s ORDER BY a", "region='n'"))
print(' HAVING, no GROUP BY ->', e.inject_rls_where("SELECT count(*) FROM t HAVING count(*) > 1", "region='n'"))
print(' inject_filter no parens ->', RLSEngine._inject_where("SELECT * FROM t WHERE a = 1 OR b = 2", "sec <= 1"))

sec('1400 cross-midnight time window')
print(' window 22->6 at hour 23 ->', e.evaluate_time_policy({'start_hour':22,'end_hour':6}, current_hour=23))
print(' window 22->6 at hour 3  ->', e.evaluate_time_policy({'start_hour':22,'end_hour':6}, current_hour=3))

sec('1869 validate_policy accepts malicious column config')
bad = RLSPolicy('r3','x',PolicyType.REGION, conditions={'allowed_regions':['a'],'region_column':"region = 'a' OR 1=1 --"})
print(' validate_policy ->', e.validate_policy(bad))
