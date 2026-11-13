"""Reviewed resource allocations; absent contracts retain historical admission caps."""
import math

DEFAULT_OUTER_SECONDS = 48 * 3600
MAX_OUTER_SECONDS = 31 * 86400
FIELDS = {'schema_version', 'logical_cores', 'memory_gb', 'memory_reserve_gb', 'outer_timeout_seconds'}

def integer(value, minimum, maximum):
    return not isinstance(value, bool) and isinstance(value, int) and minimum <= value <= maximum

def make_contract(limits, allocation=None):
    allocation = dict(allocation or {})
    contract = {'schema_version': 1, 'logical_cores': 64,
                'memory_gb': limits.get('memory_gb', 64), 'memory_reserve_gb': 16,
                'outer_timeout_seconds': DEFAULT_OUTER_SECONDS}
    contract.update(allocation)
    validate_contract(contract)
    return contract

def validate_contract(contract):
    if not isinstance(contract, dict) or set(contract) != FIELDS or not integer(contract['schema_version'], 1, 1):
        raise ValueError('Invalid resource_contract schema')
    if not integer(contract['logical_cores'], 1, 64) or (isinstance(contract['outer_timeout_seconds'], bool) or not isinstance(contract['outer_timeout_seconds'], int) or contract['outer_timeout_seconds'] < 0):
        raise ValueError('Invalid resource_contract cores/outer timeout')
    for key, low, high in [('memory_gb', 1, 240), ('memory_reserve_gb', 16, 240)]:
        value = contract[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError('Invalid resource_contract ' + key)
    return contract

def validate_pipeline_resources(pipeline, star_cap=None):
    contract = pipeline.get('resource_contract')
    if contract is None:
        return None
    validate_contract(contract)
    for key in ('logical_cores', 'memory_gb'):
        value = pipeline.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or (key == 'logical_cores' and not isinstance(value, int)) or value != contract[key]:
            raise ValueError('Pipeline differs from resource_contract ' + key)
    # Time declarations are observations; admission never compares their totals or caps.
    # Schema/type checks above retain finite integer observations and all resource limits.
    return contract
