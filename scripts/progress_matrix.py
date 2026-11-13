# -*- coding: utf-8 -*-
"""AGENTS.md 的可执行部分：场景 x 阶段进度矩阵与偏离检查。

为什么存在这个脚本（写在代码里，因为写进文档的散文没有约束力）：

本项目实际发生过一次目标偏离——把单个样本（三叶桨）的工程验证当成了整个项目的进度基准，
而"防止目标漂移"只写在 AGENTS.md 里，没有任何东西检查它。真正拦住工作的是哈希校验、审查门和
单作业限制这些可执行的门。所以 AGENTS.md 里每一条要求检查的规则，都必须在这里有对应实现，
否则它就只是一句话。

用法：
    python scripts/progress_matrix.py            # 打印矩阵与检查结果
    python scripts/progress_matrix.py --json     # 机器可读
退出码 1 表示存在 BLOCKING 项（偏离），0 表示无。

检查项对应 AGENTS.md 的条款：
    §1   两个计数器必须与矩阵一致，且禁止合并成一个数字
    §2-O1 矩阵字段齐全、取值合法
    §2-O4 单算例连续推进不得超过 limit 轮
    §2-O3 证据可引用性：来自 FAILED 阶段的记录不得被引用为 ok；PASSED 必须指名可引用的 record
    §3   场景 ① 未通过轨道 P 时，②③ 不得有新写的样本专属工作
    §4   不得把依赖固定名的操作升级为"通用操作"
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROGRESS = ROOT / 'progress.json'

# The single decision function for "may this record certify a success?". Imported rather than
# reimplemented: a second copy of the rule is how scattered checks drift apart.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_record_gate import (CITABLE, NOT_CITABLE_FAILED_RUN, UNPARSEABLE,  # noqa: E402
                             citation_status, scan_run)

PIPELINE_STATES = {'NOT_STARTED', 'PARTIAL', 'PASSED', 'BLOCKED'}
ENGINEERING_STATES = {'NOT_STARTED', 'PARTIAL', 'PASSED', 'BLOCKED'}
SCENARIO_KEYS = ('1_static_external_flow', '2_steady_mrf_propeller', '3_transient_rotation')

# 样本专属常量名：这些不得出现在宏的选择逻辑里（AGENTS.md §4）。
SAMPLE_SPECIFIC_NAME = re.compile(r'\bAIR3B_[A-Z0-9_]+\b')
MACRO_DIR = ROOT / 'macros'


def load(path: Path = DEFAULT_PROGRESS) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def validate(doc: dict) -> list[str]:
    """字段与取值的结构性校验（§2-O1）。返回问题列表。"""
    problems: list[str] = []
    if 'scenarios' not in doc or 'counters' not in doc:
        problems.append('缺少 scenarios 或 counters 段')
        return problems
    for key in SCENARIO_KEYS:
        scenario = doc['scenarios'].get(key)
        if scenario is None:
            problems.append('缺少场景 ' + key)
            continue
        if scenario.get('pipeline') not in PIPELINE_STATES:
            problems.append('%s pipeline 取值非法: %r' % (key, scenario.get('pipeline')))
        if scenario.get('engineering') not in ENGINEERING_STATES:
            problems.append('%s engineering 取值非法: %r' % (key, scenario.get('engineering')))
        stages = scenario.get('stages') or {}
        for name in doc.get('stage_names', []):
            entry = stages.get(name)
            if entry is None:
                problems.append('%s 缺少阶段 %s' % (key, name))
            elif entry.get('status') not in PIPELINE_STATES:
                problems.append('%s.%s 取值非法: %r' % (key, name, entry.get('status')))
    return problems


def counters(doc: dict) -> dict:
    """从矩阵重算两个计数器（§1）。计数器的唯一真相来源是矩阵，不是手写字符串。"""
    passed_p = sum(1 for k in SCENARIO_KEYS
                   if doc['scenarios'].get(k, {}).get('pipeline') == 'PASSED')
    passed_e = sum(1 for k in SCENARIO_KEYS
                   if doc['scenarios'].get(k, {}).get('engineering') == 'PASSED')
    return {'pipeline_verified_P': '%d/%d' % (passed_p, len(SCENARIO_KEYS)),
            'engineering_validated_E': '%d/%d' % (passed_e, len(SCENARIO_KEYS))}


def check_evidence(doc: dict, root: Path = ROOT) -> dict:
    """§2-O3 证据可引用性：来自 FAILED 阶段的记录不得被引用为 ok。

    第 6 轮的实测教训：`starccm+ -batch A,B,C` 在中间宏抛错后仍继续执行后续宏，于是同一次 FAILED
    阶段里留下了 status ok 的网格记录。分散检查（"每个后继宏核验前驱产物"）正是会漏的做法，所以判据
    只有一处实现（scripts/run_record_gate.py），这里只负责把 progress.json 的引用拿给它判。

    规则（可执行）：
    R1  stage.record 指名的"唯一证书记录"必须可引用：存在、能 json.loads、status ok、其所属阶段没有
        失败、且含 requires_record_keys 里声明的实测数值字段。任一不满足即 BLOCKING——因为 PASSED
        的含义就是"有记录背书"。
    R2  活动算例里标为 PASSED 的 stage 必须指名 record；否则 BLOCKING（非活动算例只报警告：②③ 在 ①
        通过轨道 P 之前不许推进，不能借这条规则去改动冻结场景的结论，但缺口必须可见）。
    R3  其余被引用的运行记录若来自失败阶段，只报警告并逐条列名——它们是佐证而非证书。这条缺口是
        **已知且有意的**：目录级引用（例如整个试跑目录）无法在不回溯第 6 轮结论的前提下变成硬门，
        所以写在 AGENTS.md §2-O3 里，而不是靠"没人会这么读"。
    """
    blocking: list[str] = []
    warnings: list[str] = []
    budget_cases = (doc.get('budget') or {}).get('per_case') or {}
    active_cases = {name for name, entry in budget_cases.items() if entry.get('active') is not False}

    for key in SCENARIO_KEYS:
        scenario = doc['scenarios'].get(key) or {}
        case = scenario.get('case') or scenario.get('sample')
        # Independent review round 7, concern C1: `is_active = case in active_cases` meant a TYPO in
        # the scenario's case name silently downgraded a §2-O3 BLOCKING item to a warning - i.e. a
        # typo could switch the gate off. A declared but unknown case name is now a blocking defect in
        # its own right; an absent one stays a warning, since not every historical scenario records it.
        if case is None:
            warnings.append('§2-O1 %s 没有 case/sample 名，无法与 budget.per_case 绑定：'
                            '本阶段的 §2-O3 判定只能按非活动算例的宽档处理' % key)
        elif case not in budget_cases:
            blocking.append('§2-O1 %s 声明的 case/sample %r 不是 budget.per_case 里的键；'
                            '算例绑定失效会让 §2-O3 的阻断项降级为警告' % (key, case))
        # Fail-closed on an unknown name: treat it as active so a typo cannot switch §2-O3 off. An
        # ABSENT name stays non-active (historical scenarios without a binding must not be forced to
        # name records retroactively), which is why the two cases are separated here.
        is_active = True if (case is not None and case not in budget_cases) else case in active_cases
        for stage_name, entry in (scenario.get('stages') or {}).items():
            status = entry.get('status')
            label = '%s.%s' % (key, stage_name)
            record = entry.get('record')
            if record:
                path = root / record
                verdict = citation_status(path)
                if verdict['state'] != CITABLE:
                    blocking.append('§2-O3 %s 指名的证据记录不可引用（%s）: %s'
                                    % (label, verdict['state'], verdict.get('reason')))
                else:
                    payload = json.loads(path.read_text(encoding='utf-8-sig'))
                    for field in entry.get('requires_record_keys') or []:
                        value = payload.get(field)
                        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                                or not math.isfinite(value):
                            blocking.append('§2-O3 %s 声明记录 %s 必须含实测数值字段 %s，实际是 %r'
                                            % (label, record, field, value))
            elif status == 'PASSED':
                message = ('§2-O3 %s 标为 PASSED 却没有 record：成功主张没有记录级背书'
                           % label)
                if is_active:
                    blocking.append(message)
                else:
                    warnings.append(message + '（非活动算例，未阻断）')

            # Independent review round 7, second pass: this used to require `run_state_file`, which
            # the stage-precedence and no-claim branches do not set - so once the runners started
            # writing stage records, R3's "from a failed stage, name it" warning silently emitted
            # nothing for file citations, i.e. exactly for the records the stage records attribute.
            # The warning now depends only on the verdict. Serious refusals (failed stage, unparseable)
            # are named individually; the provenance-only classes are summarised so they cannot bury them.
            serious, provenance = [], []
            for item in entry.get('evidence') or []:
                if not isinstance(item, str):
                    continue
                path = root / item
                if item.endswith('.json') and path.is_file():
                    verdict = citation_status(path)
                    if verdict['state'] in (NOT_CITABLE_FAILED_RUN, UNPARSEABLE):
                        serious.append('%s (%s)' % (item, verdict['state']))
                    elif verdict['state'] != CITABLE and verdict.get('record_claims_ok'):
                        # Only records that CLAIM success are in scope: a CaseSpec or a pipeline JSON is
                        # cited as a definition, makes no success claim, and warning about it would bury
                        # the records that do.
                        provenance.append('%s (%s)' % (item, verdict['state']))
                elif path.is_dir():
                    report = scan_run(path)
                    bad = [entry2['name'] for entry2 in report['unparseable']] + \
                          [entry2['name'] for entry2 in report['not_citable']]
                    if bad:
                        warnings.append('§2-O3 %s 引用的目录 %s 含有不可引用的记录: %s'
                                        % (label, item, ', '.join(sorted(set(bad)))))
            if serious:
                warnings.append('§2-O3 %s 引用的记录不可引用，逐条列名: %s' % (label, '; '.join(serious)))
            if provenance:
                warnings.append('§2-O3 %s 引用的记录本判据不予背书（%s）: %s'
                                % (label, '、'.join(sorted({item.split(' (')[-1].rstrip(')')
                                                            for item in provenance})),
                                   ', '.join(provenance[:4])
                                   + ('' if len(provenance) <= 4 else ' 等 %d 条' % len(provenance))))
    return {'blocking': blocking, 'warnings': warnings}


def check(doc: dict) -> dict:
    """返回 {'blocking': [...], 'warnings': [...], 'info': [...]}。"""
    blocking: list[str] = []
    warnings: list[str] = []
    info: list[str] = []

    problems = validate(doc)
    blocking.extend('§2-O1 ' + p for p in problems)

    # §1 计数器必须与矩阵一致，且两个都要在。
    declared = doc.get('counters') or {}
    actual = counters(doc)
    for name, value in actual.items():
        stated = declared.get(name)
        if stated is None:
            blocking.append('§1 计数器缺失: ' + name)
        elif stated != value:
            blocking.append('§1 计数器 %s 声明为 %s，但矩阵算出 %s' % (name, stated, value))
    if not declared.get('engineering_validated_E'):
        warnings.append('§1 两个计数器必须分别给出，不得只报一个进度数字')

    # §2-O4 阶段推进预算（第 6 轮起口径改）。计的是【距上一次任何阶段推进已过多少轮】，
    # 不管那几轮做的是什么——通用性、验证、审计、文档都算。advance 即重置。
    # 旧口径只数"样本专属深化"，结果三轮高质量但零阶段产出的工作完全不被记账：这是规则漏洞。
    budget = doc.get('budget') or {}
    per_case = budget.get('per_case') or {}
    if not per_case:
        warnings.append('§2-O4 budget.per_case 为空，阶段推进预算无法执行')
    for case, entry in per_case.items():
        # 声明与校验的区别必须一直说清楚，且这条警告必须放在任何 continue 之前。
        if entry.get('sample_specific_work_active') is not None:
            warnings.append('§2-O4 %s 的 sample_specific_work_active 与 last_round 都是**声明**，'
                            '不是校验：没有任何代码能证明它们；把它们当成硬门会高估这道门' % case)
        if entry.get('active') is False:
            # Only the case the project is actually working on is budgeted. An inactive case must not
            # force work onto it: §3 forbids advancing scenarios 2 and 3 before ① passes track P.
            continue
        used = entry.get('rounds_since_stage_advance')
        limit = entry.get('limit')
        if used is None or limit is None:
            warnings.append('§2-O4 %s 缺 rounds_since_stage_advance/limit，预算无法执行' % case)
            continue
        last = entry.get('last_round')
        if isinstance(last, dict):
            previous = last.get('previous_rounds_since_stage_advance')
            advanced = last.get('advanced_stage')
            if isinstance(previous, int) and isinstance(advanced, bool):
                expected = 0 if advanced else previous + 1
                if expected != used:
                    blocking.append('§2-O4 算例 %s 的记账不自洽：上一轮 advanced_stage=%s、previous=%s '
                                    '推出 %s，但声明为 %s' % (case, advanced, previous, expected, used))
            else:
                warnings.append('§2-O4 %s 的 last_round 字段不完整，记账无法校验' % case)
        else:
            warnings.append('§2-O4 %s 缺 last_round，本轮的计数无法由上一轮推出，只能是声明' % case)
        if used > limit:
            # 2026-10-14（独立审查裁定）：例外不得成为万能放行。三条硬约束缺一即仍然 blocking：
            # ① 授权人**署名并带日期**（不能是作者自诺）；② 有**到期轮次**（不得无限续期）；
            # ③ 生效期间该算例必须声明为不活跃，且显式声明在例外解决前不得提交该算例的新求解。
            exception = entry.get('o4_exception') or {}
            reasons = []
            authorizer = str(exception.get('authorized_by') or '')
            if not re.search(r'\d{4}-\d{2}-\d{2}', authorizer) or len(authorizer.strip()) < 8:
                reasons.append('exception 缺"授权人署名+日期"')
            expires = exception.get('expires_after_round')
            if not isinstance(expires, int) or expires <= 0:
                reasons.append('exception 缺到期轮次')
            elif isinstance(entry.get('rounds_on_case'), int) and entry['rounds_on_case'] > expires:
                reasons.append('exception 已过期')
            if entry.get('sample_specific_work_active') is not False:
                reasons.append('例外生效期间该算例必须声明为不活跃')
            if exception.get('no_new_solve_until_resolved') is not True:
                reasons.append('exception 未声明"解决前不得提交该算例新求解"')
            if reasons:
                blocking.append('§2-O4 算例 %s 已连续 %s 轮没有任何阶段推进，超过上限 %s；'
                                '例外不成立（%s）；本轮必须是阶段推进轮'
                                % (case, used, limit, '；'.join(reasons)))
            else:
                warnings.append('§2-O4 %s 已连续 %s 轮无阶段推进（超过上限 %s），'
                                '但有满足三条硬约束的例外：%s' % (case, used, limit, authorizer))
        elif used == limit:
            warnings.append('§2-O4 %s 已连续 %s 轮没有任何阶段推进；下一轮必须是阶段推进轮' % (case, used))
        elif used == limit - 1:
            warnings.append('§2-O4 %s 已连续 %s 轮没有任何阶段推进（报警线）' % (case, used))

    # §3 顺序准入：场景 ① 未通过轨道 P 时，②③ 不得有新写的样本专属工作。
    first = doc['scenarios'].get(SCENARIO_KEYS[0], {})
    if first.get('pipeline') != 'PASSED':
        for key in SCENARIO_KEYS[1:]:
            if (doc['scenarios'].get(key) or {}).get('sample_specific_work_active'):
                blocking.append('§3 场景 ① 尚未通过轨道 P，但 %s 上仍有新写的样本专属工作' % key)
        for case, entry in (budget.get('per_case') or {}).items():
            if entry.get('sample_specific_work_active'):
                blocking.append('§3 场景 ① 尚未通过轨道 P，但算例 %s 仍在推进样本专属工作' % case)
        info.append('§3 场景 ① 尚未通过轨道 P（当前 %s），是最便宜的端到端验证，按规定必须先闭环'
                    % first.get('pipeline'))

    # §4 通用性：依赖固定名的操作不得标为通用操作。
    for op in (doc.get('generality') or {}).get('operations') or []:
        if op.get('depends_on_fixed_names') and op.get('status') == 'GENERAL_OPERATION':
            blocking.append('§4 操作 %s 依赖固定名却被标为通用操作' % op.get('name'))
        if op.get('status') == 'GENERAL_OPERATION' and not (op.get('positive_case') and op.get('negative_case')):
            blocking.append('§4 操作 %s 缺正例或反例，不得升为通用操作' % op.get('name'))

    matrix = (doc.get('generality') or {}).get('matrix_status')
    if matrix not in (None, 'PASSED'):
        info.append('§4 泛化矩阵状态: %s（未通过即不得宣称通用操作）' % matrix)

    # §2-O3 证据可引用性
    evidence = check_evidence(doc)
    blocking.extend(evidence['blocking'])
    warnings.extend(evidence['warnings'])

    return {'blocking': blocking, 'warnings': warnings, 'info': info}


def lint_macros() -> list[str]:
    """宏的选择逻辑中不得硬编码样本专属名（§4 的启发式版本）。

    只报为 warning：可靠地区分"选择逻辑"与"日志/环境变量名"需要解析 Java，本函数是纯文本扫描，
    会把刻意的样本专属宏也算进来。它的用途是让通用性缺口可见，不是当门用。
    """
    findings: list[str] = []
    if not MACRO_DIR.is_dir():
        return findings
    for path in sorted(MACRO_DIR.glob('*.java')):
        hits = sorted(set(SAMPLE_SPECIFIC_NAME.findall(path.read_text(encoding='utf-8', errors='replace'))))
        if hits:
            findings.append('%s 出现样本专属名 %d 种: %s' % (path.name, len(hits), ', '.join(hits[:6])))
    return findings


def render(doc: dict) -> str:
    lines: list[str] = []
    actual = counters(doc)
    lines.append('场景 x 阶段矩阵  (更新: %s)' % doc.get('updated', '?'))
    lines.append('  流程验收 P = %s     工程验证 E = %s' %
                 (actual['pipeline_verified_P'], actual['engineering_validated_E']))
    lines.append('')
    header = '  %-26s %-12s %-12s %s' % ('场景', '轨道P', '轨道E', '阶段')
    lines.append(header)
    lines.append('  ' + '-' * (len(header) + 10))
    for key in SCENARIO_KEYS:
        scenario = doc['scenarios'].get(key, {})
        stages = scenario.get('stages') or {}
        marks = ' '.join('%s=%s' % (n, stages.get(n, {}).get('status', '?'))
                         for n in doc.get('stage_names', []))
        lines.append('  %-26s %-12s %-12s %s' % (key, scenario.get('pipeline', '?'),
                                                 scenario.get('engineering', '?'), marks))
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='AGENTS.md 场景 x 阶段进度检查')
    parser.add_argument('--progress', type=Path, default=DEFAULT_PROGRESS)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--no-lint', action='store_true', help='跳过宏的样本专属名扫描')
    args = parser.parse_args(argv)

    doc = load(args.progress)
    report = check(doc)
    report['macros'] = [] if args.no_lint else lint_macros()
    report['counters'] = counters(doc)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(doc))
        print('')
        for label, items in (('BLOCKING', report['blocking']), ('WARNING', report['warnings']),
                             ('INFO', report['info'])):
            for item in items:
                print('%s: %s' % (label, item))
        if report['macros']:
            print('')
            print('宏的样本专属名扫描（启发式，仅供参考）:')
            for item in report['macros']:
                print('  - ' + item)
        print('')
        print('结论: ' + ('存在 BLOCKING 项，偏离已发生' if report['blocking'] else '无 BLOCKING 项'))
    return 1 if report['blocking'] else 0


if __name__ == '__main__':
    sys.exit(main())
