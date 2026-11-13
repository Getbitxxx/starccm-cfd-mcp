"""Evaluate the exported smoke-test evidence, without declaring CFD validation."""
import csv
import json
import math
import statistics
import sys
from pathlib import Path

root = Path(sys.argv[1])
state = json.loads((root / 'smoke_status.json').read_text())

def read_series(path):
    with path.open(encoding='utf-8-sig', newline='') as file:
        rows = list(csv.reader(file))
    return [(float(row[0]), float(row[1])) for row in rows[1:] if len(row) >= 2]

def describe(samples):
    values = [v for _, v in samples]
    if not values or not all(math.isfinite(v) for v in values):
        raise ValueError('Empty or non-finite monitor data')
    tail = values[-30:]
    avg = statistics.mean(tail)
    previous = values[-60:-30]
    return {
        'samples': len(values), 'last': values[-1], 'mean_last_30': avg,
        'min_last_30': min(tail), 'max_last_30': max(tail),
        'relative_range_last_30': (max(tail)-min(tail))/abs(avg) if avg else None,
        'relative_mean_drift_last_two_30_windows': abs(avg-statistics.mean(previous))/abs(avg) if previous and avg else None,
    }

series = {p.stem: read_series(p) for p in (root / 'monitor_csv').glob('*.csv')}
metrics = {name: describe(values) for name, values in series.items() if name.startswith('CFD_')}
raw = {p.stem: describe(read_series(p)) for p in (root / 'residual_raw_csv').glob('*.csv')}
checks = {
    'smoke_completed': state['state'] == 'SMOKE_COMPLETED',
    'advanced_exactly_100': state['end_iteration']-state['start_iteration'] == 100,
    'all_new_monitors_have_100_samples': len(metrics) == 14 and all(m['samples'] == 100 for m in metrics.values()),
    'all_seven_raw_residuals_exported': len(raw) == 7 and all(m['samples'] == 100 for m in raw.values()),
}

def paired_error(left, right, factor):
    a, b = series[left], series[right]
    if len(a) != len(b) or any(x[0] != y[0] for x, y in zip(a, b)):
        raise ValueError('Monitor iteration alignment mismatch')
    return max(abs(x[1]-factor*y[1])/max(abs(x[1]), 1e-20) for x, y in zip(a, b))

formula_errors = {
    'periodic_thrust_equals_three_sectors': paired_error('CFD_PeriodicThrust_Monitor', 'CFD_SectorThrust_Monitor', 3.0),
    'periodic_torque_equals_three_sectors': paired_error('CFD_PeriodicTorque_Monitor', 'CFD_SectorTorque_Monitor', 3.0),
    'power_equals_torque_times_omega': paired_error('CFD_SectorPower_Monitor', 'CFD_SectorTorque_Monitor', 2*math.pi*10000/60),
}
checks['formula_consistency'] = all(v < 1e-10 for v in formula_errors.values())

# These are explicit provisional screening criteria, not universal CFD standards.
screening = {
    'criteria': {'window_samples': 30, 'load_relative_range_limit': 0.005, 'mass_imbalance_limit': 0.001},
    'thrust_stable': metrics['CFD_SectorThrust_Monitor']['relative_range_last_30'] < 0.005,
    'torque_stable': metrics['CFD_SectorTorque_Monitor']['relative_range_last_30'] < 0.005,
    'mass_balance_small': metrics['CFD_MassImbalance_Monitor']['max_last_30'] < 0.001,
    'raw_residual_acceptance': 'not_automatically_classified_without_equation_specific_scaling',
}

summary = {
    'execution_checks': checks, 'execution_passed': all(checks.values()),
    'state': state, 'metrics': metrics, 'raw_residuals': raw,
    'formula_relative_errors': formula_errors, 'provisional_screening': screening,
    'physical_validation': False, 'mesh_independence_verified': False,
    'free_air_applicability': 'unconfirmed: outer cylinder is fixed no-slip wall',
    'interpretation': '100-step continuation of saved fields, not a fresh initialization or validation study',
}
(root/'evaluation.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False)+'\n')
print(json.dumps({'execution_checks': checks, 'provisional_screening': screening}, indent=2))
if not all(checks.values()):
    raise SystemExit(1)
