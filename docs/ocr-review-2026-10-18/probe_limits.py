from app.services.sandbox_manager import validate_resource_limits, get_resource_limits, RESOURCE_LIMITS
print("zero   :", validate_resource_limits("L3", {"cpu_cores": 0, "memory_mb": 0, "disk_mb": 0}))
print("neg    :", validate_resource_limits("L3", {"memory_mb": -1}))
try:
    print("str    :", validate_resource_limits("L3", {"cpu_cores": "8"}))
except Exception as e:
    print("str    : RAISED", type(e).__name__, e)
before = dict(RESOURCE_LIMITS["L3"])
shared = validate_resource_limits("L3", None)
shared["cpu_cores"] = 9999
print("alias  : before=", before["cpu_cores"], "after-alias-write=", RESOURCE_LIMITS["L3"]["cpu_cores"], "same_obj=", shared is RESOURCE_LIMITS["L3"])
