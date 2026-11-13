# -*- coding: utf-8 -*-
"""HyperMesh 原生网格质量记录的唯一准入判据（AGENTS.md §7 尾缘条、§8 网格工具条）。

为什么存在这个模块（理由写在代码里，因为写进文档的散文没有约束力）
--------------------------------------------------------------------
用户 2026-10-14 明确要求：HyperMesh 自带网格检查与坏网格定位，**网格划定、检查与修改都尽量
在 HyperMesh 内完成**。这不是一句偏好，它有两个可检查的后果，本模块就是这两个后果的实现：

1. **检查必须定位，而不只是计数。** 官方 2023 文档写明 `*elementtestjacobian` /
   `*elementtestskew` 会把不合格单元放到指定的 output mark 上（"puts those that fail on the
   specified outputmark"），`hm_getelemcheckvalues` 可按选择返回逐单元检查值。也就是说
   "哪几个单元坏、坏在哪里" 是 HyperMesh 能给出的实测数据。
2. **定位缺失不能被读成能力缺口。** 只有定位完整时，"这套网格没过 HyperMesh 原生门" 才是
   关于**这套网格**的结论；缺少失败单元 ID/坐标时，它连"该在 HyperMesh 里修哪几个单元"
   都答不出来，因此**不得**被引用为"HyperMesh 路线能力不足、可以换 Gmsh"的依据。
   本项目实测过这个形状：修复2候选 c 的记录只有每项失败数/最坏值（6 个 Jacobian、5 个 skew），
   逐单元 ID 与坐标没有保存（`native_full_percell_check_values_saved: false`）。

单点实现原则（AGENTS.md §2-O8）
------------------------------
本模块是"一条 HyperMesh 质量记录可否准入、可否作为切换后备工具依据"的**唯一**判定处。
调用方一律用 `evaluate()` / `assert_admissible()`，不得各自重写门槛比较。两个工具的质量指标
互不替代：本模块只回答"HyperMesh 原生检查这一关"，它**永不**代表 STAR 导入、边界/接口、
近壁层或 STAR 原生 CQ/FV/VC 通过（所以 `star_admitted` / `cfd_admitted` /
`engineering_verified` 在这里恒为 False）。
"""
from __future__ import annotations

import math
import re
from pathlib import Path

# 判定结果。前两个是"记录本身不合格"，第三个是"网格实打实没过门但定位完整"。
RECORD_INCOMPLETE = 'RECORD_INCOMPLETE'
LOCALIZATION_MISSING = 'LOCALIZATION_MISSING'
FAILED_CRITERIA_WITH_LOCALIZATION = 'FAILED_CRITERIA_WITH_LOCALIZATION'
ADMITTED = 'ADMITTED'

# 记录自己声称准入了，但本门判定不过：这正是 run_record_gate 的 R2 形状（记录自身必须声称成功），
# 只是方向相反——记录不得声称比实测更强的结论。
# 记录可能用不同拼写自称准入；判据必须认得全部这些拼写，否则「自称 STAR 准入」会从一个键名的缝里
# 漏过去（生产者审查 C-9：生产者写 STAR_admitted，而闸门当时只认 star_admitted）。C-9 之后的复验又
# 证明两件事：拼写还有 STAR_ADMITTED/Star_admitted 等变体，且 `is True` 会漏掉 JSON 里的整数 1；
# 更要紧的是闸门**自己的**判据字段名 quality_admitted 原先完全不在监控列表里——一条 FAILED 记录写
# `quality_admitted: true` 不会被点名。现在按小写归一后的名字匹配，并把真值(1/1.0)也算作自称。
ADMISSION_FLAG_KEYS = ('star_admitted', 'cfd_admitted', 'engineering_verified', 'e_verified',
                       'quality_admitted')

_SHA256 = re.compile(r'^[0-9a-f]{64}$')


class AdmissionRefused(ValueError):
    """消费方在准入失败时必须中断，而不是把记录继续往下传。"""


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _text(value) -> bool:
    return isinstance(value, str) and value.strip() != ''


def _evaluate_criterion(criterion, mesh, require_coordinates: bool):
    """返回 (规范化后的准则 dict, 问题列表)。结构问题与质量失败分开报告。"""
    problems: list[str] = []
    if not isinstance(criterion, dict):
        return None, ['criterion is not an object']
    name = criterion.get('name')
    for key in ('name', 'native_command', 'short_label', 'direction'):
        if not _text(criterion.get(key)):
            problems.append('criterion %r lacks %s' % (name, key))
    direction = criterion.get('direction')
    if direction not in ('min', 'max'):
        problems.append('criterion %r direction must be min/max' % (name,))
    for key in ('threshold', 'worst'):
        if not _finite(criterion.get(key)):
            problems.append('criterion %r %s must be finite' % (name, key))
    checked, failed = criterion.get('checked'), criterion.get('failed')
    for key, value in (('checked', checked), ('failed', failed)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            problems.append('criterion %r %s must be a non-negative integer' % (name, key))
    if problems:
        return None, problems

    # 检查范围：要么整份网格，要么声明维度后与 dimension_counts 对上。少查一部分不算通过。
    if 'dimension_counts' in mesh:
        dimension = criterion.get('dimension')
        if not isinstance(dimension, int) or isinstance(dimension, bool):
            problems.append('criterion %r dimension must be the integer 2 or 3' % (name,))
        elif dimension not in mesh['dimension_counts']:
            problems.append('criterion %r must declare a dimension covered by mesh.dimension_counts' % (name,))
        elif checked != mesh['dimension_counts'][dimension]:
            problems.append('criterion %r checked=%d != dimension_counts[%r]=%d'
                            % (name, checked, dimension, mesh['dimension_counts'][dimension]))
    elif checked != mesh['element_count']:
        problems.append('criterion %r checked=%d != mesh.element_count=%d' % (name, checked, mesh['element_count']))
    if failed > checked:
        problems.append('criterion %r failed=%d > checked=%d' % (name, failed, checked))
    if problems:
        return None, problems

    direction_ok = (criterion['worst'] >= criterion['threshold'] if direction == 'min'
                    else criterion['worst'] <= criterion['threshold'])
    # A native failure count is not allowed to contradict the saved measured worst value.
    # The real failed cap can otherwise be admitted by changing only failed -> 0.
    if failed == 0 and not direction_ok:
        return None, ['criterion %r reports failed=0 while measured worst=%r violates threshold=%r'
                      % (name, criterion['worst'], criterion['threshold'])]
    normalized = {
        'name': name, 'native_command': criterion['native_command'], 'short_label': criterion['short_label'],
        'direction': direction, 'threshold': criterion['threshold'], 'checked': checked,
        'failed': failed, 'worst': criterion['worst'], 'worst_within_threshold': direction_ok,
        'dimension': criterion.get('dimension'),
    }
    if failed == 0:
        return normalized, []

    # 出现失败就必须带定位：失败单元 ID 与逐单元数值；坐标默认也要求。
    localization = criterion.get('localization')
    if not isinstance(localization, dict):
        normalized['localized'] = False
        normalized['localization_problem'] = 'no localization block for %d failing element(s)' % failed
        return normalized, []
    ids = localization.get('failed_element_ids')
    values = localization.get('per_element_values')
    coordinates = localization.get('coordinates_m')
    local_problems: list[str] = []
    if not isinstance(ids, list) or not ids:
        local_problems.append('localization.failed_element_ids missing or empty')
    elif any(not isinstance(item, int) or isinstance(item, bool) or item <= 0 for item in ids):
        local_problems.append('localization.failed_element_ids must be positive integers')
    elif len(set(ids)) != len(ids):
        local_problems.append('localization.failed_element_ids contains duplicates')
    elif len(ids) != failed:
        local_problems.append('localization.failed_element_ids has %d entries for %d failing element(s)'
                              % (len(ids), failed))
    if not isinstance(values, dict):
        local_problems.append('localization.per_element_values missing')
    elif isinstance(ids, list):
        missing = [str(item) for item in ids if not _finite(values.get(str(item)))]
        if missing:
            local_problems.append('localization.per_element_values lacks finite values for: ' + ', '.join(missing[:8]))
    if require_coordinates:
        if not isinstance(coordinates, dict):
            local_problems.append('localization.coordinates_m missing')
        elif isinstance(ids, list):
            bad = [str(item) for item in ids
                   if not (isinstance(coordinates.get(str(item)), (list, tuple))
                           and len(coordinates[str(item)]) == 3
                           and all(_finite(x) for x in coordinates[str(item)]))]
            if bad:
                local_problems.append('localization.coordinates_m lacks 3 finite coordinates for: '
                                      + ', '.join(bad[:8]))
    # C-6：网格自报的单元 ID 范围是"这些失败 ID 属于这份网格"的唯一可核对依据。
    bounds = mesh.get('element_id_bounds')
    if isinstance(bounds, (list, tuple)) and len(bounds) == 2 and isinstance(ids, list) and not local_problems:
        outside = [str(item) for item in ids if not (bounds[0] <= item <= bounds[1])]
        if outside:
            local_problems.append('failed element id(s) outside mesh.element_id_bounds %r: %s'
                                  % (list(bounds), ', '.join(outside[:8])))
    if local_problems:
        normalized['localized'] = False
        normalized['localization_problem'] = '; '.join(local_problems)
    else:
        normalized['localized'] = True
        normalized['failed_element_ids'] = list(ids)
        normalized['id_bounds_checked'] = isinstance(bounds, (list, tuple)) and len(bounds) == 2
    return normalized, []


def evaluate(record: dict, require_coordinates: bool = True) -> dict:
    """判定一条 HyperMesh 原生质量记录。永不抛异常：结构不全也返回拒绝结论。"""
    reasons: list[str] = []
    if not isinstance(record, dict):
        return _verdict(RECORD_INCOMPLETE, ['record is not an object'], [], {}, {})

    tool = record.get('tool')
    source = record.get('source')
    mesh = record.get('mesh')
    criteria = record.get('criteria')
    # 记录自己声称的准入结论：任何一条路径返回前都要过这一关（独立审查 D-2/C-2）。
    def _normalise_key(name):
        """N-21：拼写变体不止大小写——`starAdmitted` / `star-admitted` / `Star Admitted` 都要归一到
        `staradmitted`。只做 lower() 会漏掉后两种。"""
        return re.sub(r'[^a-z0-9]', '', name.lower())

    def _claims_admission(doc):
        """自称准入：名字归一（大小写与分隔符无关）且是真值（1/1.0/"true"/"yes" 同样是自称）。

        N-19：闸门**自己的**输出字段 quality_admitted 只有在"这不像是闸门判定结果"时才算自称，
        否则 evaluate(record) 与 evaluate(evaluate(record)) 会不一致（判定必须幂等，消费者才能安全
        地把结论继续往下传）。真正的判定结果带 decision 字段，据此区分。
        """
        watched = {_normalise_key(key) for key in ADMISSION_FLAG_KEYS}
        verdict_shaped = isinstance(doc.get('decision'), str)
        found = []
        for key, value in doc.items():
            if not isinstance(key, str):
                continue
            normalised = _normalise_key(key)
            if normalised not in watched:
                continue
            if normalised == 'qualityadmitted':
                # 闸门自己的输出：只有当**本次判定不是 ADMITTED**时，这个字段才算"记录在自称准入"
                # （N-13b 的本意）。判定为 ADMITTED 时它是一致的重复，不构成矛盾，否则判定就不幂等
                # （N-19：消费者把上一轮结论注回记录就会被无理由拒绝）。
                continue
            truthy = value is True
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                truthy = truthy or value == 1
            if isinstance(value, str):
                truthy = truthy or value.strip().lower() in ('true', 'yes', '1')
            if truthy:
                found.append(key)
        return sorted(found)

    claimed = _claims_admission(record)

    def finish(decision, reasons_in, normalized_in, targets, mesh_in, citable=True):
        """唯一的收尾处：把"记录自称准入"和"定位是否可信"两件事应用到结论上。"""
        out_reasons = list(reasons_in)
        self_claim = record.get('quality_admitted')
        if self_claim is not None and not isinstance(self_claim, str) and self_claim is not False \
                and (self_claim is True or self_claim == 1):
            if decision != ADMITTED:
                out_reasons.append('record claims quality_admitted=%r while the native quality gate '
                                   'refuses it (decision=%s)' % (self_claim, decision))
        if claimed:
            out_reasons.append('record claims admission flags %s but the native quality gate is not satisfied'
                               % ', '.join(claimed))
            if decision == ADMITTED:
                decision = RECORD_INCOMPLETE
        verdict = _verdict(decision, out_reasons, normalized_in, targets, mesh_in)
        # 同一记录不得既自称 STAR/CFD 准入、又被引用为 HyperMesh 能力缺口——后者正是 §5/§8 切换
        # Gmsh 的书面依据（独立审查 C-2：这两个字段同时为真自相矛盾）。
        suppressed: list = []
        if claimed and verdict['can_claim_hypermesh_capability_gap']:
            suppressed.extend(claimed)
        # 定位若没有落在网格自报的单元 ID 范围内，修复清单不足以作为能力缺口依据（独立审查 C-6）。
        if verdict['decision'] == FAILED_CRITERIA_WITH_LOCALIZATION and not citable:
            suppressed.append('unbounded_failed_element_ids')
        if suppressed:
            # 累积，不覆盖：两个原因同时成立时两个都要在记录里（审查 R2-4）。
            verdict['can_claim_hypermesh_capability_gap'] = False
            verdict['capability_gap_suppressed_by'] = suppressed
        return verdict

    for key, value in (('tool', tool), ('source', source), ('mesh', mesh)):
        if not isinstance(value, dict):
            reasons.append('%s block missing' % key)
    if not isinstance(criteria, list) or not criteria:
        reasons.append('criteria missing or empty')
    if reasons:
        return finish(RECORD_INCOMPLETE, reasons, [], {}, {})

    for label, block, keys in (('tool', tool, ('name', 'version')), ('source', source, ('file', 'sha256')),
                               ('mesh', mesh, ('file', 'sha256', 'units'))):
        for key in keys:
            if not _text(block.get(key)):
                reasons.append('%s.%s missing' % (label, key))
    for label, block in (('source', source), ('mesh', mesh)):
        if _text(block.get('sha256')) and not _SHA256.match(block['sha256']):
            reasons.append('%s.sha256 is not a lowercase sha256' % label)
    if mesh.get('dimension') not in (2, 3):
        reasons.append('mesh.dimension must be 2 or 3')
    if not isinstance(mesh.get('element_count'), int) or isinstance(mesh.get('element_count'), bool) \
            or mesh.get('element_count', 0) <= 0:
        reasons.append('mesh.element_count must be a positive integer')
    if 'dimension_counts' in mesh:
        counts = mesh['dimension_counts']
        if (not isinstance(counts, dict) or not counts
                or any(key not in (2, 3) or not isinstance(counts[key], int) or isinstance(counts[key], bool)
                       or counts[key] <= 0 for key in counts)):
            reasons.append('mesh.dimension_counts must map dimension 2/3 to positive integers')
        elif sum(counts.values()) != mesh.get('element_count'):
            reasons.append('mesh.dimension_counts does not sum to mesh.element_count')
    if 'element_id_bounds' in mesh:
        bounds = mesh['element_id_bounds']
        if (not isinstance(bounds, (list, tuple)) or len(bounds) != 2
                or any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in bounds)
                or bounds[0] > bounds[1]
                or (isinstance(mesh.get('element_count'), int) and not isinstance(mesh.get('element_count'), bool)
                    and bounds[1] - bounds[0] + 1 < mesh['element_count'])):
            reasons.append('mesh.element_id_bounds must be [lo, hi]: 1 <= lo <= hi and hi-lo+1 >= element_count')
    if reasons:
        return finish(RECORD_INCOMPLETE, reasons, [], {}, mesh)

    normalized: list[dict] = []
    for criterion in criteria:
        item, problems = _evaluate_criterion(criterion, mesh, require_coordinates)
        if problems:
            reasons.extend(problems)
            continue
        normalized.append(item)
    if reasons:
        return finish(RECORD_INCOMPLETE, reasons, normalized, {}, mesh)
    if len({item['name'] for item in normalized}) != len(normalized):
        return finish(RECORD_INCOMPLETE, ['criterion names must be unique'], normalized, {}, mesh)

    # 独立审查 D-1（blocking）：声明了 dimension_counts 的网格，每个声明维度都必须**至少**有一条准则
    # 覆盖它；只查其中一部分同样是"漏项或未知范围"（AGENTS.md §7），不得准入。
    if 'dimension_counts' in mesh:
        covered = {item['dimension'] for item in normalized if item.get('dimension') in mesh['dimension_counts']}
        uncovered = sorted(dimension for dimension in mesh['dimension_counts'] if dimension not in covered)
        if uncovered:
            return finish(RECORD_INCOMPLETE,
                          ['declared dimension(s) %s are covered by no criterion: a partial check of a '
                           'declared split mesh is an unknown scope, not a pass' % uncovered],
                          normalized, {}, mesh)

    failed = [item for item in normalized if item['failed'] > 0]
    localization_complete = (record.get('localization_complete') is True
                             and record.get('per_cell_values_saved') is True)
    if not failed and not localization_complete:
        # 全过但没保存逐单元检查值：一次"没查全"的通过不算通过（AGENTS.md §8 漏项即拒绝）。
        return finish(RECORD_INCOMPLETE,
                      ['every criterion passed but localization_complete/per_cell_values_saved are not true'],
                      normalized, {}, mesh)

    decision = ADMITTED if not failed else (
        FAILED_CRITERIA_WITH_LOCALIZATION
        if all(item.get('localized') for item in failed) else LOCALIZATION_MISSING)
    if failed:
        unlocalized = [item['name'] for item in failed if not item.get('localized')]
        if unlocalized:
            reasons.append('failing criterion(s) without failed-element localization: ' + ', '.join(unlocalized))
            reasons.append('a record without localization cannot be cited as a HyperMesh capability gap')
        reasons.append('failed criterion(s): ' + ', '.join(
            '%s failed=%d/%d worst=%r threshold=%r' % (item['name'], item['failed'], item['checked'],
                                                       item['worst'], item['threshold']) for item in failed))
    if record.get('status') == 'ok' and failed:
        reasons.append('record claims status=ok while criteria report failures')
    # 独立审查 C-6：失败单元 ID 必须落在网格自报的单元 ID 范围内（mesh.element_id_bounds）；否则这份
    # "定点修复清单"无从核对，不能作为切换工具的书面依据。
    unbounded = [item['name'] for item in failed
                 if item.get('localized') and not item.get('id_bounds_checked')]
    if unbounded:
        reasons.append('failed element id(s) of %s are not bounded by mesh.element_id_bounds, so the repair '
                       'list cannot be verified against this mesh' % ', '.join(unbounded))
    return finish(decision, reasons, normalized, _repair_targets(normalized, criteria), mesh,
                  citable=not unbounded)


def _repair_targets(normalized: list[dict], raw_criteria: list) -> dict:
    """把失败单元连同逐单元数值/坐标整理成"在 HyperMesh 内定点修复"的清单。"""
    targets: dict[str, dict] = {}
    by_name = {item['name']: item for item in raw_criteria if isinstance(item, dict)}
    for item in normalized:
        if item['failed'] == 0 or not item.get('localized'):
            continue
        localization = by_name[item['name']]['localization']
        targets[item['name']] = {
            'native_command': item['native_command'], 'threshold': item['threshold'],
            'direction': item['direction'], 'output_mark': localization.get('output_mark'),
            'failed_element_ids': list(localization['failed_element_ids']),
            'per_element_values': dict(localization.get('per_element_values') or {}),
            'coordinates_m': dict(localization.get('coordinates_m') or {}),
        }
    return targets


def _verdict(decision: str, reasons: list, normalized: list, repair_targets: dict, mesh: dict) -> dict:
    admitted = decision == ADMITTED
    failed = [item for item in normalized if item['failed'] > 0]
    return {
        'decision': decision,
        'reasons': reasons,
        # quality_admitted 只覆盖"HyperMesh 原生检查这一关"；它不给 STAR 或 CFD 背书。
        'quality_admitted': admitted,
        'star_admitted': False,
        'cfd_admitted': False,
        'engineering_verified': False,
        'criteria': normalized,
        'failed_criteria': [item['name'] for item in failed],
        'repair_targets': repair_targets,
        # 只有"检查本身完整、失败也被定位"的记录，才有资格被读成关于这套网格/这条路线能力的事实。
        'can_claim_hypermesh_capability_gap': decision == FAILED_CRITERIA_WITH_LOCALIZATION,
    }


def assert_admissible(record: dict, require_coordinates: bool = True) -> dict:
    """准入断言：消费方（STAR 导入前的准备器、报告器）必须过这一关。"""
    verdict = evaluate(record, require_coordinates=require_coordinates)
    if not verdict['quality_admitted']:
        raise AdmissionRefused('%s: %s' % (verdict['decision'], '; '.join(verdict['reasons']) or 'refused'))
    return verdict


def load(path) -> dict:
    import json
    return json.loads(Path(path).read_text(encoding='utf-8'))


def main(argv: list[str]) -> int:
    import json
    import sys
    if len(argv) != 2:
        print('usage: hypermesh_quality_gate.py <quality-record.json>', file=sys.stderr)
        return 2
    verdict = evaluate(load(argv[1]))
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0 if verdict['quality_admitted'] else 1


if __name__ == '__main__':
    import sys
    raise SystemExit(main(sys.argv))
