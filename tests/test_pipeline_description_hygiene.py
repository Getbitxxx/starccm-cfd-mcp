# -*- coding: utf-8 -*-
"""未被历史记录 pin 的 pipeline，其阶段描述里不得出现硬编码秒数。

背景（用户当场指出的重复犯错）：缺陷源头是模板
`cases/static-sphere-external-flow/pipeline-e2e-geometry-to-validation.json` 的 solve 描述里
写死的 "EXTERNAL_FLOW_BUDGET_SECONDS = 480 s ... this stage's deadline is 540 s"。我三次只改副本
（TI 链 2、四条链 B），源头没删，于是每从模板复制一次就把同一个矛盾带出来一次。

那个模板的字节被 77 份历史审查记录 pin 住，直接改会破坏历史证据的可复现性。因此本测试用**计算出的
豁免**：凡字节被任何审查记录 pin 过的文件视为历史冻结、豁免；其余文件一律不得含硬编码秒数。
这样历史不被改写，而新文件不可能再带出这个缺陷。
"""
from __future__ import annotations

import glob
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECONDS_IN_DESCRIPTION = re.compile(r'\b\d+(?:\.\d+)?\s*(?:s|seconds?)\b')


def pinned_digests() -> set[str]:
    """所有审查记录里出现过的 sha256（历史冻结文件的标记）。"""
    digests: set[str] = set()
    for path in glob.glob(str(ROOT / 'test-evidence' / '**' / '*.json'), recursive=True):
        try:
            text = Path(path).read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        digests.update(re.findall(r'\b[0-9a-f]{64}\b', text))
    return digests


class PipelineDescriptionsCarryNoHardcodedSeconds(unittest.TestCase):
    def test_unpinned_pipelines_have_no_seconds_in_stage_descriptions(self):
        pinned = pinned_digests()
        import hashlib
        offenders = []
        checked = 0
        for path in glob.glob(str(ROOT / 'cases' / '**' / 'pipeline*.json'), recursive=True):
            if 'review' in Path(path).name:
                continue
            raw = Path(path).read_bytes()
            if hashlib.sha256(raw).hexdigest() in pinned:
                continue                     # 历史冻结文件：豁免，且不得改写
            try:
                doc = json.loads(raw.decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            checked += 1
            for stage in doc.get('stages') or []:
                text = str(stage.get('description') or '')
                if SECONDS_IN_DESCRIPTION.search(text):
                    offenders.append('%s:%s' % (Path(path).name, stage.get('name')))
        self.assertTrue(checked > 0, '没有可检查的未冻结 pipeline')
        self.assertEqual([], offenders, '阶段描述里出现硬编码秒数：%r' % offenders)

    def test_the_canonical_template_exists_and_is_clean(self):
        path = ROOT / 'cases/sphere-aedc-m0p204-20261014/pipeline-template-external-flow.json'
        doc = json.loads(path.read_text(encoding='utf-8'))
        for stage in doc.get('stages') or []:
            self.assertIsNone(SECONDS_IN_DESCRIPTION.search(str(stage.get('description') or '')),
                              '%s 的描述含硬编码秒数' % stage.get('name'))


if __name__ == '__main__':
    unittest.main()
