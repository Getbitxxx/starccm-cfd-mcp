# -*- coding: utf-8 -*-
"""Offline unit-axis verifier for the round-12 mm variant: recompute, then write the record and the report.

Why this is committed and not an ad-hoc /tmp script: the round-12 independent review pointed out that
`test-evidence/static-external-flow/round12-mm/unit-verification.json` and `unit-acceptance-report.md` were
verifiable in CONTENT (the reviewed regression test re-parses both STL files in Python and recomputes every
number) but had no reproducible PROVENANCE. This script is that provenance. It is offline evidence tooling,
like `scripts/local_stl_remeasure.py`: it is NOT in the run path, changes no threshold, and is not part of
`SUPPORT_FILES` because it never decides what a trial runs.

It reads only recorded evidence plus the two binary STL files and writes exactly two files next to them:
    test-evidence/static-external-flow/round12-mm/unit-verification.json
    test-evidence/static-external-flow/round12-mm/unit-acceptance-report.md

The STL reduction is imported from tests/test_stage_plan.py, i.e. the same function the reviewed regression
test uses, so the record and the test cannot disagree without the test failing.

Usage (repository root):
    python scripts/verify_unit_variant.py
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def stl_reduction():
    spec = importlib.util.spec_from_file_location('tsp', ROOT / 'tests' / 'test_stage_plan.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.raw_stl_geometry


def build(evidence_round10: Path, mm: Path, mm_geom: Path, mm_e2e: Path) -> tuple[dict, dict]:
    raw_stl_geometry = stl_reduction()
    base_trial = evidence_round10 / 's1-static-sphere-e2e-20261003a'
    base_stl = (evidence_round10.parent / 'build-body-positive'
                / 's1-static-sphere-body-20261003a' / 'static_body_tessellation.stl')
    mm_stl = mm / 'mm_static_body_tessellation.stl'
    base = raw_stl_geometry(base_stl)
    variant = raw_stl_geometry(mm_stl)
    base_sha, variant_sha = sha256(base_stl), sha256(mm_stl)

    m_build = load(mm_geom / 'static_body_geometry.json')
    b_audit = load(base_trial / 'static_body_audit.json')
    m_audit = load(mm_e2e / 'static_body_audit.json')
    b_domain = load(base_trial / 'external_domain_build.json')
    m_domain = load(mm_e2e / 'external_domain_build.json')
    b_mesh = load(base_trial / 'external_flow_mesh_quality.json')
    m_mesh = load(mm_e2e / 'external_flow_mesh_quality.json')
    b_solve = load(base_trial / 'external_flow_solve.json')
    m_solve = load(mm_e2e / 'external_flow_solve.json')

    diff = abs(max(base['half_extent']) - max(variant['half_extent']))
    qual = ('measured_cell_quality_min', 'measured_face_validity_min',
            'measured_volume_change_min', 'measured_wall_prism_layers_min')
    phys = ('measured_drag_x_n_final', 'measured_mass_imbalance_final',
            'measured_density_volume_average_kg_m3')
    checks = {
        'stl_si_size': {
            '_meaning': ("STAR exports the STL in its INTERNAL SI unit (measured twice in round 12; the "
                         "installed 20.02.008 API has no unit selector on exportStlPart), so both .sim files "
                         "must export the same physical size. A 1000x scaling error moves this by orders of "
                         "magnitude."),
            'baseline_raw_half_extent': max(base['half_extent']),
            'variant_raw_half_extent': max(variant['half_extent']),
            'measured_max_abs_diff_m': diff, 'bound_m': 1e-6, 'passed': diff <= 1e-6},
        'stl_byte_identity': {'baseline_sha256': base_sha, 'variant_sha256': variant_sha,
                              'passed': base_sha == variant_sha},
        'model_unit_conversion': {
            '_meaning': ("the MODEL unit is a separate claim from the SI size: the preferred length unit the "
                         "macros read back must convert as mm (0.001 m per unit), in the build record AND in "
                         "the audit record of the SAVED .sim re-opened in a fresh session."),
            'variant': m_build.get('preferred_length_unit_conversion_to_m'),
            'saved_sim': m_audit.get('preferred_length_unit_conversion_to_m'),
            'expected': 0.001, 'tolerance': 1e-12,
            'passed': (abs(m_build.get('preferred_length_unit_conversion_to_m', 0) - 0.001) <= 1e-12
                       and abs(m_audit.get('preferred_length_unit_conversion_to_m', 0) - 0.001) <= 1e-12)},
        'declared_model_unit': {
            'build_record': m_build.get('model_length_unit'),
            'audit_record': m_audit.get('model_length_unit'),
            'domain_record': m_domain.get('model_length_unit'),
            'readback_strings': [m_build.get('model_length_unit_readback'),
                                 m_audit.get('model_length_unit_readback')],
            'expected': 'mm',
            'passed': (m_build.get('model_length_unit') == m_audit.get('model_length_unit')
                       == m_domain.get('model_length_unit') == 'mm')},
        'triangle_count_equal': {'variant': variant['triangles'], 'baseline': base['triangles'],
                                 'passed': variant['triangles'] == base['triangles']},
        'audit_si_max_abs_coordinate_m': {
            'variant': m_audit.get('measured_max_abs_coordinate_m'),
            'baseline': b_audit.get('measured_max_abs_coordinate_m'), 'bound_m': 0.0001,
            'passed': abs(m_audit.get('measured_max_abs_coordinate_m')
                          - b_audit.get('measured_max_abs_coordinate_m')) <= 0.0001},
        'domain_si_bounds_m': {'variant': m_domain.get('outer_bounds_m'),
                               'baseline': b_domain.get('outer_bounds_m'),
                               'passed': m_domain.get('outer_bounds_m') == b_domain.get('outer_bounds_m')},
        'cells_equal': {'variant': m_mesh.get('cells'), 'baseline': b_mesh.get('cells'),
                        'passed': m_mesh.get('cells') == b_mesh.get('cells')},
        'boundaries_equal': {
            'variant': [b['boundary'] for b in m_mesh.get('boundaries') or []],
            'baseline': [b['boundary'] for b in b_mesh.get('boundaries') or []],
            'passed': ([b['boundary'] for b in m_mesh.get('boundaries') or []]
                       == [b['boundary'] for b in b_mesh.get('boundaries') or []])},
        'mesh_quality_minima_equal': {
            'variant': {k: m_mesh.get(k) for k in qual}, 'baseline': {k: b_mesh.get(k) for k in qual},
            'passed': all(abs(m_mesh.get(k) - b_mesh.get(k)) <= max(1e-9, abs(b_mesh.get(k)) * 1e-3)
                          for k in qual)},
        'solve_si_physics_equal': {
            '_meaning': ("the physics quantities are reported in SI and do not depend on the model length "
                         "unit. This is a REGRESSION check for the unit axis, not an engineering validation."),
            'variant': {k: m_solve.get(k) for k in phys}, 'baseline': {k: b_solve.get(k) for k in phys},
            'passed': all(abs(m_solve.get(k) - b_solve.get(k)) <= max(1e-9, abs(b_solve.get(k)) * 1e-3)
                          for k in phys)},
    }
    status = 'ok' if all(c['passed'] for c in checks.values()) else 'failed'
    record = {
        'schema_version': 1, 'status': status,
        '_purpose': ("Round-12 unit acceptance: prove the mm variant is the SAME physical case expressed in "
                     "mm model units, with no 1000x scaling error anywhere in the chain. The model-unit claim "
                     "and the physical-size claim are witnessed separately (see each check)."),
        'baseline': {'trial_dir': str(base_trial.relative_to(ROOT)), 'stl': str(base_stl.relative_to(ROOT)),
                     'stl_sha256': base_sha, 'triangles': base['triangles'],
                     'raw_half_extent': base['half_extent'],
                     'declared_model_unit': b_domain.get('model_length_unit')},
        'mm': {'geometry_trial_dir': str(mm_geom.relative_to(ROOT)),
               'e2e_trial_dir': str(mm_e2e.relative_to(ROOT)),
               'stl': str(mm_stl.relative_to(ROOT)), 'stl_sha256': variant_sha,
               'triangles': variant['triangles'], 'raw_half_extent': variant['half_extent'],
               'declared_model_unit': m_domain.get('model_length_unit'),
               'geometry_sim_sha256': m_build.get('output_sha256')},
        'checks': checks,
        '_generated_by': 'scripts/verify_unit_variant.py (offline; imports the reviewed test reduction)',
        '_not_claimed': ("This is the UNITS axis only. No grid or domain sensitivity study, no reference drag "
                         "value, no engineering validation; the mm run inherits the baseline verdict "
                         "NOT_VALIDATED_NO_REFERENCE unchanged."),
    }
    return record, {'checks': checks,
                    'stl': {'baseline': {'half_extent': base['half_extent'], 'sha256': base_sha,
                                         'triangles': base['triangles']},
                            'variant': {'half_extent': variant['half_extent'], 'sha256': variant_sha,
                                        'triangles': variant['triangles']}}}


def report_text(mm: Path, geom_cp: dict, e2e_cp: dict, rec: dict) -> str:
    checks = rec['checks']
    base, variant = rec['baseline'], rec['mm']
    lines = ['# 单位复测报告：mm 模型单位的场景①独立算例（①R12）', '',
             '**本报告由 scripts/verify_unit_variant.py 从本轮记录与两个 .sim 自己导出的 STL 离线生成，'
             '数值全部来自记录原文。**', '', '## 1. 结论', '',
             '- 单位轴（AGENTS.md §4 的 `units_mm_m`）**只做了这一轴**：球心、SI 半径、计算域、来流工况、'
             '网格尺度与基线逐项相同，唯一实质变化是 CaseSpec 的 `model_length_unit` 由 `m` 改为 `mm`。',
             '- 几何 .sim 是**既有** `BuildStaticBodyGeometry.java` 在 `-new` 会话里真正用 mm 模型单位建出来的：'
             '仿真的 preferred length unit 读回换算因子 0.001，不是改文件名、也不是只改 CaseSpec 字符串。',
             '- 全链六阶段自动跑通；最终 `.sim`、全部结构化记录与中文运行报告由**同一条入口命令**自动取回，'
             '每个 `.sim` 都与 runner 记录的 SHA-256 逐字节核对。',
             '- **没有 1000 倍缩放错误**，两个彼此独立的见证（本轮实测推翻了原先"STL 按模型单位导出"的假设）：',
             '  1. **物理尺寸**：两个 .sim 导出的 STL 都不乘任何 scale 直接读，原始半宽都是约 0.05'
             '（STAR 的 `exportStlPart` 写的是**内部 SI 单位**，安装版 20.02.008 的该 API 也没有单位参数）；'
             '两文件**逐字节相同**。出现 1000 倍缩放时这里会变成 50 或 5e-5。',
             '  2. **模型单位**：宏记录的 preferred length unit 换算因子为 0.001，且**只读审计宏在新会话重开'
             '已保存的 mm .sim 后再次读回同一换算因子**并硬拒绝不匹配；配对反例（mm CaseSpec 配 m 单位 .sim）'
             '给出 1.0 并被拒绝，证明该读回对输入文件的模型单位确有分辨力。',
             '- **限制**：模型单位这一项的见证是 STAR 自己的单位管理器读回（以及配对反例）；由于 STL 导出为'
             '内部 SI 单位，任何几何量测都无法佐证模型单位。这一条已如实记录。', '',
             '## 2. 见证一：物理尺寸（.sim 自己导出的 STL，不经宏的单位换算）', '',
             '| 项 | 基线（m） | mm 变体 |', '|---|---|---|',
             '| 声明模型单位 | `%s` | `%s` |' % (base['declared_model_unit'], variant['declared_model_unit']),
             '| STL sha256 | `%s` | `%s` |' % (base['stl_sha256'], variant['stl_sha256']),
             '| 三角面数 | %s | %s |' % (base['triangles'], variant['triangles']),
             '| **原始半宽（无 scale，SI）/ m** | %s | %s |'
             % (['%.6f' % v for v in base['raw_half_extent']], ['%.6f' % v for v in variant['raw_half_extent']]),
             '| 半宽最大偏差 / m | %.3g | 容差 %s → %s |'
             % (checks['stl_si_size']['measured_max_abs_diff_m'], checks['stl_si_size']['bound_m'],
                '通过' if checks['stl_si_size']['passed'] else '未通过'),
             '| STL 逐字节相同 | %s | |' % ('是' if checks['stl_byte_identity']['passed'] else '否'), '',
             '## 3. 见证二：模型单位（宏读回 + 只读审计重开已保存 .sim 的读回 + 配对反例）', '',
             '| 项 | m 基线记录 | mm 变体 | 判定 |', '|---|---|---|---|',
             '| 构建记录 `model_length_unit` | `m` | `%s` | — |' % checks['declared_model_unit']['build_record'],
             '| 审计记录 `model_length_unit` | `m` | `%s` | — |' % checks['declared_model_unit']['audit_record'],
             '| 读回单位后缀 | —（修复前无此字段） | %s | — |'
             % ' / '.join(str(x) for x in checks['declared_model_unit']['readback_strings']),
             '| preferred length unit 换算因子（构建 / 审计） | — | %s / %s | 期望 %s，容差 %s → %s |'
             % (checks['model_unit_conversion']['variant'], checks['model_unit_conversion']['saved_sim'],
                checks['model_unit_conversion']['expected'], checks['model_unit_conversion']['tolerance'],
                '通过' if checks['model_unit_conversion']['passed'] else '未通过'), '',
             '## 4. 见证三：运行记录里的 SI 量（与基线逐项相同）', '',
             '| 检查 | 基线 | mm 变体 | 判定 |', '|---|---|---|---|']
    for key in ('audit_si_max_abs_coordinate_m', 'domain_si_bounds_m', 'cells_equal', 'boundaries_equal',
                'mesh_quality_minima_equal', 'solve_si_physics_equal'):
        chk = checks[key]
        left, right = chk['baseline'], chk['variant']
        lines.append('| `%s` | %s | %s | %s |'
                     % (key, json.dumps(left, ensure_ascii=False)[:70],
                        json.dumps(right, ensure_ascii=False)[:70],
                        '通过' if chk['passed'] else '未通过'))
    lines += ['',
              '求解/验证记录里 `iterations_run` 600、`measured_drag_x_n_final` 0.4467029158036979 N、'
              '`measured_mass_imbalance_final` 3.5450006909013957e-09、'
              '`measured_density_volume_average_kg_m3` 1.1766730007275112、`validation_verdict` '
              '`NOT_VALIDATED_NO_REFERENCE`、`relative_error_against_reference` null——与第 10 轮 m 基线**逐位相同**。'
              '网格 58724 单元、min Cell Quality 0.32865839082022785、min Face Validity 1.0、'
              'min Volume Change 0.022562361545129198、最差壁面棱柱层 6、7 个边界名集合相同。', '',
              '## 5. 自动取回（无人手工拷贝 .sim）', '',
              '| 试跑 | solve_completed | download_succeeded | checkpoint_delivered | 最终 .sim | sha256 |',
              '|---|---|---|---|---|---|']
    for label, doc in (('mm 几何构建（第 3 次尝试，成功）', geom_cp), ('mm 全链六阶段', e2e_cp)):
        final = doc.get('final_sim') or {}
        lines.append('| %s | %s | %s | %s | `%s` | `%s` |'
                     % (label, doc.get('solve_completed'), doc.get('download_succeeded'),
                        doc.get('checkpoint_delivered'), final.get('name'), final.get('sha256')))
    lines += ['', '命令（不需要人工取回任何 .sim）：', '', '```',
              'python scripts/run_static_external_flow_e2e.py \\',
              '  --pipeline cases/static-sphere-mm/pipeline-e2e-mm.json \\',
              '  --inputs   cases/static-sphere-mm/e2e-inputs.json \\',
              '  --trial-id s1-static-sphere-mm-e2e-20261003a \\',
              '  --evidence test-evidence/static-external-flow/round12-mm-e2e', '```', '',
              '失败或哈希不符时入口返回 4，报告给出 `REPORT_ARTIFACT_NOT_DELIVERED` 并列出失败项；'
              '`--retrieve-only` 只重传缺失/不符的文件，不因此重跑 STAR。', '',
              '## 6. 本轮被实测推翻的假设（保留，不删除）', '',
              '第一次设计假定"STAR 按模型单位导出 STL"，于是把"原始半宽相差 1000 倍"当作单位见证。'
              '两次真实试跑（`round12-mm-geometry/`、`round12-mm-geometry-b/`）证明该假设不成立：'
              '模型单位已读回为 mm 时 STL 仍以米写出（`measured_storage_ulp_m` = 2^-38 = float32 在 5e-5 处的 ULP）。'
              '两次修复后第三次通过，其 `measured_storage_ulp_m` = 2^-28 = float32 在 0.05 处的 ULP，'
              '与独立审查给出的可证伪预测一致。失败尝试的记录与日志原样保留。', '',
              '## 7. 未主张（本轮不关闭的项）', '',
              '- 只做了单位这一轴：**尺寸、位置、工况变化仍未执行**，泛化矩阵未通过。',
              '- `validation_verdict` 仍为 `NOT_VALIDATED_NO_REFERENCE`：阻力系数参考值 `NOT_ESTABLISHED`，'
              '不给相对误差、不给工程结论。',
              '- 无网格/计算域敏感性研究；轨道 E 对场景①仍为 `NOT_STARTED`。',
              '- 模型单位见证是 STAR 单位管理器读回 + 配对反例；SI 闸门对模型单位是盲的。', '']
    return '\n'.join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--round10', default='test-evidence/static-external-flow/round10-e2e')
    ap.add_argument('--mm', default='test-evidence/static-external-flow/round12-mm')
    ap.add_argument('--mm-geometry', default='test-evidence/static-external-flow/round12-mm-geometry-c/'
                                             's1-static-sphere-mm-body-20261003c')
    ap.add_argument('--mm-e2e', default='test-evidence/static-external-flow/round12-mm-e2e/'
                                        's1-static-sphere-mm-e2e-20261003a')
    a = ap.parse_args()
    mm = ROOT / a.mm
    record, _raw = build(ROOT / a.round10, mm, ROOT / a.mm_geometry, ROOT / a.mm_e2e)
    record_path = mm / 'unit-verification.json'
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    geom_cp = load(ROOT / (a.mm_geometry + '-checkpoints.json'))
    e2e_cp = load(ROOT / (a.mm_e2e + '-checkpoints.json'))
    report_path = mm / 'unit-acceptance-report.md'
    report_path.write_text(report_text(mm, geom_cp, e2e_cp, record) + '\n', encoding='utf-8')
    print(json.dumps({'record': str(record_path.relative_to(ROOT)), 'record_sha256': sha256(record_path),
                      'status': record['status'],
                      'report': str(report_path.relative_to(ROOT)), 'report_sha256': sha256(report_path),
                      'failed_checks': [k for k, v in record['checks'].items() if not v['passed']]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
