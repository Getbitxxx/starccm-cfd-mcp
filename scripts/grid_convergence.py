"""Equal-ratio three-grid diagnostic; never certifies CFD engineering validity.

Ordering: coarse, medium, fine. NASA/NPARC spatial-convergence tutorial:
https://www.grc.nasa.gov/www/wind/valid/tutorial/spatconv.html
Iterations, mesh-family similarity and independent domain studies must be checked
outside this numerical calculation. Increasing same-sign increments are divergence.
"""
from __future__ import annotations
import math


def three_grid_gci(coarse, medium, fine, ratio, *, safety_factor=1.25,
                   normalization_scale=None, limit_pct=5.0):
    values = (coarse, medium, fine, ratio, safety_factor, limit_pct)
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in values):
        raise ValueError('Grid values and parameters must be finite real numbers')
    if ratio <= 1 or safety_factor < 1 or limit_pct <= 0:
        raise ValueError('Require ratio>1, safety_factor>=1 and limit_pct>0')
    if normalization_scale is not None and (isinstance(normalization_scale, bool)
            or not isinstance(normalization_scale, (int, float))
            or not math.isfinite(normalization_scale) or normalization_scale <= 0):
        raise ValueError('Explicit normalization scale must be finite and positive')
    e21, e32 = medium-coarse, fine-medium
    out = {'eps_21': e21, 'eps_32': e32, 'r': ratio, 'p': None,
           'gci_fine_pct': None, 'converged': False, 'valid': False,
           'engineering_verified': False}
    def reject(reason):
        out['note'] = reason
        return out
    if not math.isfinite(e21) or not math.isfinite(e32):
        return reject('差值超出有限数值范围')
    if e21 == 0 or e32 == 0:
        return reject('相邻差值为零，三点法无法确定观测阶；不能据此宣称网格独立')
    if (e21 > 0) != (e32 > 0):
        return reject('差值振荡，单调三点法不可用')
    # Do not abs(log): that would turn divergent refinement into positive order.
    p = (math.log(abs(e21))-math.log(abs(e32))) / math.log(ratio)
    out['p'] = p
    if p <= 0:
        return reject('观测阶非正：差值未随细化缩小，拒绝GCI外推')
    power = p*math.log(ratio)
    if power > 709:
        return reject('外推分母超出有限数值范围')
    denom = math.expm1(power)
    if not math.isfinite(denom) or denom < 1e-14:
        return reject('外推分母无效或过小')
    scale = abs(fine) if normalization_scale is None else normalization_scale
    if scale == 0:
        return reject('最细目标量为零，必须显式提供有物理依据的误差归一化尺度')
    absolute = safety_factor * (abs(e32) / denom)
    extrapolated = fine + e32 / denom
    pct = 100 * absolute / scale
    if not all(math.isfinite(x) for x in (absolute, extrapolated, pct)):
        return reject('外推或误差界超出有限数值范围')
    out.update(valid=True, gci_fine_pct=pct, phi_extrapolated=extrapolated,
               absolute_error_band=absolute, normalization_scale=scale,
               converged=pct < limit_pct,
               note='仅数值诊断；还需迭代收敛、网格族及渐近适用性和计算域检查')
    return out
