# -*- coding: utf-8 -*-
"""L1（独立审查裁定）：supersession ledger 必须是一条**连续且当前**的链。

没有这条测试时，(a) 机制会退化成"每次字节一动就重新解释一遍"：冻结门正确地报漂移，而 ledger
里查不到放行依据。判据：段相接、终结 after = 当前磁盘、键为仓库相对路径、无空移动。
"""
from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from tests.superseded_pins import ledger_problems

ROOT = Path(__file__).resolve().parents[1]


class TheLedgerIsAContinuousCurrentChain(unittest.TestCase):
    def test_no_ledger_problems(self):
        problems = ledger_problems(ROOT / 'test-evidence')
        self.assertEqual([], problems, 'ledger 链式校验失败：%r' % problems)

    def test_the_ledger_explains_its_own_format(self):
        import json
        document = json.loads((ROOT / 'test-evidence/engineering-validation-start-20261009' /
                               'time-budget-revocation-superseded-pins.json').read_text(encoding='utf-8'))
        self.assertEqual('chain', document['ledger_format']['format'])
        # 易变文件（测试自身写入的指纹）必须显式排除并说明原因，否则每跑一次测试 ledger 就失真
        volatile = document['ledger_format']['volatile_excluded']
        self.assertTrue(volatile.get('why'))
        self.assertTrue(volatile.get('files'))
        # 每条路径只在一个 section 登记（独立审查 2026-10-14）
        self.assertIn('one_section_per_path', document['ledger_format'])

    def test_no_path_is_registered_in_two_sections(self):
        import json
        from pathlib import Path as _P
        document = json.loads((_P(__file__).resolve().parents[1] /
                               'test-evidence/engineering-validation-start-20261009' /
                               'time-budget-revocation-superseded-pins.json').read_text(encoding='utf-8'))
        seen = {}
        for section in ('macro_repaired', 'macros_repaired',
                        'macro_changes_from_round_23_also_recorded_here',
                        'shared_state_files_updated_this_round'):
            for name in (document.get(section) or {}):
                self.assertNotIn(name, seen, '%s 在 %s 与 %s 重复登记' % (name, seen.get(name), section))
                seen[name] = section


class TheKeyCriterionIsAPathNotASlash(unittest.TestCase):
    """2026-10-15：'键是仓库相对路径' 曾被实现成 `'/' not in name` 的代理检查。

    README.md 与 progress.json 是仓库根下的真实相对路径，却被该代理当成裸文件名拒绝，于是本轮无法登记它们。
    判据改为"键必须解析到仓库根下的真实文件"。这两条测试把它钉住：正例（根下的真实文件）必须通过，
    反例（裸宏文件名）必须仍然失败——代理被换成判据，适用面没有变窄。
    """

    def _problems(self, document, files=()):
        import json as _json
        import tempfile
        from tests.superseded_pins import ledger_problems
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'test-evidence').mkdir()
            for name in files:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'x')
            (root / 'test-evidence' / 'fixture-superseded-pins.json').write_text(
                _json.dumps(document), encoding='utf-8')
            return ledger_problems(root / 'test-evidence')

    @staticmethod
    def _entry(before, after):
        return {'why': 'fixture',
                'segments': [{'before': before, 'after': after, 'why': 'fixture'}],
                'after': after}

    def test_a_top_level_repository_file_is_a_valid_key(self):
        # README.md 是仓库根下的真实相对路径：链式判据必须接受它（终结 after 等于它的真实字节）
        live = hashlib.sha256(b'x').hexdigest()
        problems = self._problems({'ledger_format': {'format': 'chain'},
                                   'shared_state_files_updated_this_round':
                                       {'README.md': self._entry('a' * 64, live)}},
                                  files=['README.md'])
        self.assertEqual([], problems)

    def test_a_bare_macro_file_name_is_still_rejected(self):
        problems = self._problems({'ledger_format': {'format': 'chain'},
                                   'macro_repaired': {'RunExternalFlowSolve.java': self._entry('a' * 64, 'b' * 64)}})
        self.assertTrue(any('找不到对应文件' in problem and 'RunExternalFlowSolve.java' in problem
                            for problem in problems), problems)

    def test_an_absolute_key_is_still_rejected(self):
        problems = self._problems({'ledger_format': {'format': 'chain'},
                                   'shared_state_files_updated_this_round':
                                       {'/etc/hosts': self._entry('a' * 64, 'b' * 64)}})
        self.assertTrue(any('仓库相对路径' in problem for problem in problems), problems)

    def test_a_dot_dot_key_that_resolves_outside_the_repo_is_rejected(self):
        """独立审查 F2：'../outside.json' 会命中仓库外的真实文件，且不含绝对路径。

        判据必须先在形式上拒绝 `..`，并在解析后再确认键落在仓库根内。
        """
        import json as _json
        import tempfile
        from tests.superseded_pins import ledger_problems
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'test-evidence').mkdir()
            (root / 'outside.json').write_bytes(b'x')          # 仓库根的**外面**
            document = {'ledger_format': {'format': 'chain'},
                        'shared_state_files_updated_this_round':
                            {'../outside.json': self._entry('a' * 64, 'b' * 64)}}
            (root / 'test-evidence' / 'fixture-superseded-pins.json').write_text(
                _json.dumps(document), encoding='utf-8')
            problems = ledger_problems(root / 'test-evidence')
        self.assertTrue(any('..' in problem or '逃出仓库根' in problem for problem in problems), problems)

    def test_a_terminal_after_that_differs_from_disk_is_still_rejected(self):
        # negative control for the hash half: the new existence check must not swallow the terminal check
        problems = self._problems({'ledger_format': {'format': 'chain'},
                                   'shared_state_files_updated_this_round':
                                       {'README.md': self._entry('a' * 64, 'b' * 64)}},
                                  files=['README.md'])
        self.assertTrue(any('未终结的 after 与当前磁盘不符' in problem for problem in problems), problems)


class BareMacroKeysAreAlsoReadAtTheReadEnd(unittest.TestCase):
    """2026-10-15：L1 把**写入端**统一成 `macros/X.java`，并在 allowed_pins 的读取端做了裸名归一化，
    但同一读取端的 documented_after 没有做——于是 round12/r24/r25/r26/remediation-20261008 那些**用裸宏名**
    记录的宏移动，对 `is_recorded_change('macros/X.java', …)` 不可见（②R17 的冻结门正好 pin 的是 `macros/…`
    写法，于是只报漂移、无从放行）。判据本身不变：仍然只认"某条记录声明过这个 sha256"。
    """

    def test_a_record_using_the_bare_macro_name_is_visible_under_macros_slash(self):
        import json as _json
        import tempfile
        from tests.superseded_pins import is_recorded_change
        recorded, other = 'c' * 64, 'd' * 64
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'test-evidence').mkdir()
            (root / 'test-evidence' / 'old-superseded-pins.json').write_text(_json.dumps({
                'macros_repaired': {'SomeMacro.java': {'before': 'a' * 64, 'after': recorded}}}),
                encoding='utf-8')
            self.assertTrue(is_recorded_change('macros/SomeMacro.java', recorded, root / 'test-evidence'))
            # negative control: an unrelated digest is still unrecorded
            self.assertFalse(is_recorded_change('macros/SomeMacro.java', other, root / 'test-evidence'))


class SnapshotFreezePinsAreABoundedGap(unittest.TestCase):
    """独立审查 F3：test-evidence 下有大量 `*freeze*.json` / `*frozen*.json` 记录，其中一部分 pin 的文件
    已被后续轮次移动，而**没有任何测试把那些 pin 与活树比较**——它们是各轮当时状态的快照，不是活门。

    这里不做"全部登记"：那会要求为 60 余份快照记录里的数百个 pin 各写一条 supersession，而它们描述的是
    各轮当时的请求状态（与 §6「历史记录里登记的时间值只描述当时的请求」同一性质）。判据改为**棘轮**：
    活树与这些快照 pin 不一致的 (记录, 文件) 对**集合**被固定下来，只能减少，不能悄悄变化。

    独立审查第二轮 C2 指出：只钉两个计数时，把一对换成另一对（删一个陈旧 pin、再加一个只 pin 活树文件的
    冻结记录）计数不变、判据看不见。因此这里钉的是**排序后的 (记录::文件) 列表的 sha256**（身份，不是计数），
    计数只作为可读性附注一起断言。
    """

    #: 2026-10-16：原554对保留，另12对均为经独审且连续ledger登记的MRF墙钟策略修复。
    #: 差集逐条见 engineering-solve-focus-20261016/SNAPSHOT-PIN-DELTA.json，历史快照不改。
    IDENTITY = '5495cda06f5524b3184c695d1e20fd26166b0f09b1311b664e96455c7d1b0f46'
    RECORDS = 62
    PAIRS = 630

    def test_the_twelve_new_snapshot_differences_are_the_registered_mrf_move(self):
        import json
        from scripts.supersession_ledger import is_recorded_move
        path = ROOT / 'test-evidence/engineering-solve-focus-20261016/SNAPSHOT-PIN-DELTA.json'
        delta = json.loads(path.read_text())
        self.assertEqual('ba98d3a5ad538aca69908ebf640aff9c319236d1bcc6b5dd6dd118a47e7aca96',
                         delta['baseline_using_known_prechange_hashes']['identity'])
        self.assertEqual(554, delta['baseline_using_known_prechange_hashes']['pairs'])
        self.assertEqual('2a35145f425804a880e7af9c4acf04d93f452eabf07d84443497cf5efc5a0841', delta['current']['identity'])
        self.assertEqual([], delta['removed'])
        self.assertEqual(12, len(set(delta['added'])))
        self.assertTrue(all(item.endswith('::macros/RunAir3bMrfSegment.java') for item in delta['added']))
        self.assertFalse(delta['historical_snapshot_bytes_changed'])
        live = hashlib.sha256((ROOT / 'macros/RunAir3bMrfSegment.java').read_bytes()).hexdigest()
        self.assertTrue(is_recorded_move('macros/RunAir3bMrfSegment.java', live, ROOT / 'test-evidence'))
        self.assertEqual([], ledger_problems(ROOT / 'test-evidence'))

    def test_product_report_move_is_recorded_without_rewriting_snapshots(self):
        import json
        from scripts.supersession_ledger import is_recorded_move
        delta=json.loads((ROOT/'test-evidence/product-acceptance/completion-20261018/SNAPSHOT-PIN-DELTA.json').read_text())
        self.assertEqual('2a35145f425804a880e7af9c4acf04d93f452eabf07d84443497cf5efc5a0841',delta['baseline_using_prechange_bytes']['identity'])
        self.assertEqual('4b67d85b0246ec72aca7e85af0e368cd34801f3a3e77fcf0ce7ff0c204e20bf2',delta['current']['identity'])
        self.assertEqual(599,delta['current']['pairs'])
        self.assertEqual([],delta['removed'])
        self.assertEqual(33,len(set(delta['added'])))
        self.assertTrue(all(item.endswith('::scripts/generate_run_report.py') for item in delta['added']))
        self.assertFalse(delta['historical_snapshot_bytes_changed'])
        live=hashlib.sha256((ROOT/'scripts/generate_run_report.py').read_bytes()).hexdigest()
        self.assertTrue(is_recorded_move('scripts/generate_run_report.py',live,ROOT/'test-evidence'))

    def test_deployment_bootstrap_move_is_explicit_and_historical_snapshots_stay_frozen(self):
        import json
        from scripts.supersession_ledger import is_recorded_move
        delta=json.loads((ROOT/'test-evidence/product-acceptance/deployed-20261018/SNAPSHOT-PIN-DELTA.json').read_text())
        self.assertEqual('4b67d85b0246ec72aca7e85af0e368cd34801f3a3e77fcf0ce7ff0c204e20bf2',delta['baseline_using_prechange_bytes']['identity'])
        self.assertEqual(599,delta['baseline_using_prechange_bytes']['pairs'])
        self.assertEqual(self.IDENTITY,delta['current']['identity'])
        self.assertEqual(self.PAIRS,delta['current']['pairs'])
        self.assertEqual([],delta['removed'])
        self.assertEqual(31,len(delta['added']))
        self.assertTrue(all(row.endswith('::scripts/security/grant_batch_logon.ps1') for row in delta['added']))
        self.assertFalse(delta['historical_snapshot_bytes_changed'])
        live=hashlib.sha256((ROOT/'scripts/security/grant_batch_logon.ps1').read_bytes()).hexdigest()
        self.assertTrue(is_recorded_move('scripts/security/grant_batch_logon.ps1',live,ROOT/'test-evidence'))

    def test_the_snapshot_pin_gap_cannot_grow_silently(self):
        import hashlib
        import json as _json
        import re

        def pins(document):
            found = {}
            for key in ('files', 'window2_owned', 'frozen_files', 'shared_execution_files'):
                node = document.get(key) if isinstance(document, dict) else None
                if isinstance(node, dict):
                    for name, digest in node.items():
                        if isinstance(digest, str) and len(digest) == 64:
                            found[name] = digest
            return found

        pairs = []
        for path in sorted((ROOT / 'test-evidence').rglob('*.json')):
            if not re.search(r'(frozen|freeze)', path.name):
                continue
            try:
                document = _json.loads(path.read_text(encoding='utf-8'))
            except Exception:                                # noqa: BLE001
                continue
            rel = path.relative_to(ROOT).as_posix()
            for name, digest in pins(document).items():
                target = ROOT / name
                if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                    pairs.append('%s::%s' % (rel, name))
        pairs.sort()
        identity = hashlib.sha256('\n'.join(pairs).encode('utf-8')).hexdigest()
        delta=_json.loads((ROOT/'test-evidence/quality-contract-20261020/SNAPSHOT-PIN-DELTA.json').read_text())
        self.assertEqual(self.IDENTITY,delta['baseline_using_registered_before']['identity'])
        self.assertEqual([],delta['removed'])
        self.assertEqual(6,len(delta['added']))
        self.assertTrue(all(x.endswith('::scripts/server_v2.py') for x in delta['added']))
        from scripts.supersession_ledger import is_recorded_move
        self.assertTrue(is_recorded_move('scripts/server_v2.py',hashlib.sha256((ROOT/'scripts/server_v2.py').read_bytes()).hexdigest(),ROOT/'test-evidence'))
        self.assertFalse(delta['historical_snapshot_bytes_changed'])
        self.assertEqual(delta['current']['identity'], identity,
                         '快照式冻结记录的陈旧 pin **集合**变了（计数 %d/%d）：减少时请下调本测试的常量，'
                         '增加或替换则说明有新的被 pin 文件移动、或新增了 pin 活树文件的冻结记录，'
                         '必须先按用户 2026-10-14 裁定 (a) 决定是登记、重钉还是声明为缺口。'
                         % (self.RECORDS, self.PAIRS))
        self.assertEqual((delta['current']['records'],delta['current']['pairs']),
                         (len({pair.split('::')[0] for pair in pairs}), len(pairs)))

    def test_the_files_this_round_changed_are_registered_in_the_ledger(self):
        """F3 的具体实例：本轮改了 tests/test_registered_workflow_mcp.py（预算准入按裁决 A 改写），
        它被 ②R22 的**未被读取**的冻结记录 pin 着旧字节，移动因此没有进入 ledger。现在必须有记录，
        而且记录必须终结于当前字节。"""
        import hashlib
        from tests.superseded_pins import documented_files
        documented = documented_files(ROOT)
        for name in ('tests/test_registered_workflow_mcp.py',
                     'tests/test_original_geometry_r19.py',
                     'tests/test_original_r18_review.py',
                     'scripts/registered_workflow_mcp.py',
                     'scripts/registered_workflow_mcp_v2.py'):
            live = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            self.assertIn(live, documented.get(name, set()),
                          '被 pin 的文件发生了未登记的移动：' + name)


class RecordHashesAreCurrent(unittest.TestCase):
    """E1（独立审查第五轮）：合规记录引用的一份活动记录的 sha256 是更新前的字节，引用静默过期。

    判据：本轮两份活动记录里，凡是"指向仓库文件的 sha256 字段"都必须等于磁盘当前值。
    实现只认**显式声明**的路径→哈希对：键名以 `_sha256` 结尾，且同一个字典里有对应的路径键
    （键名去掉后缀，或 path/file/declared_file/record/record_path 之一）。猜不到就不检查，不猜。
    `*_previous*` 字段按约定保存旧值，不在检查范围。
    """

    RECORDS = ('test-evidence/engineering-validation-start-20261009/'
               'REGRESSION-ROUND-RULING-COMPLIANCE-20261015.json',
               'test-evidence/engineering-validation-start-20261009/GATE-RUNS-20261015.json')
    PATH_KEYS = ('path', 'file', 'declared_file', 'record', 'record_path', 'gate_runs',
                 'this_round_ruling', 'previous_ruling', 'previous_compliance')

    def _pairs(self, node, trail=''):
        if isinstance(node, dict):
            for key, value in node.items():
                is_hash = key.endswith('_sha256') or key == 'sha256'
                if is_hash and isinstance(value, str) and len(value) == 64:
                    stem = key[:-len('_sha256')] if key.endswith('_sha256') else ''
                    for candidate in (stem,) + self.PATH_KEYS:
                        target = node.get(candidate)
                        if isinstance(target, str) and (ROOT / target).is_file():
                            yield trail + '/' + key, target, value
                            break
                yield from self._pairs(value, trail + '/' + str(key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                yield from self._pairs(value, '%s[%d]' % (trail, index))

    def test_every_declared_record_hash_matches_disk(self):
        import hashlib
        import json as _json
        checked = 0
        for relative in self.RECORDS:
            document = _json.loads((ROOT / relative).read_text(encoding='utf-8'))
            for key, target, digest in self._pairs(document):
                with self.subTest(record=relative, field=key):
                    live = hashlib.sha256((ROOT / target).read_bytes()).hexdigest()
                    self.assertEqual(digest, live,
                                     '%s%s 引用的 %s 已过期' % (relative, key, target))
                    checked += 1
        self.assertGreaterEqual(checked, 3, '判据至少应命中三处显式引用（gate_runs、裁定文件、声明的外部文件）')


if __name__ == '__main__':
    unittest.main()
