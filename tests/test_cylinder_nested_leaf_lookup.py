# -*- coding: utf-8 -*-
"""①R25: the round-24 real failure, pinned as an executable regression.

Round 24 delivered the cylinder geometry-only source `.sim` and then failed its fresh-session audit with
`star.base.neo.NeoException: ... Named object "INDEPENDENT_CYLINDER" does not exist in manager`. The cause was
measured, not guessed: `SimulationPartManager.getPart(String)` searches TOP-LEVEL parts only and THROWS for an
absent name, while the repaired CAD import keeps the CaseSpec body name on a NESTED solid leaf.

The fixture is that real pair of artifacts - the delivered `.sim` bytes and the real failing audit record -
rather than a hand-written stand-in, so these tests fail if the defect comes back on the real data.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIM = (ROOT / 'test-evidence' / 'static-cylinder-step' / 'r24-import' /
       'static-cylinder-step-import-20261005d' / 'static_body_geometry.sim')
R24_AUDIT = (ROOT / 'test-evidence' / 'static-cylinder-step' / 'r24-import' /
             'static-cylinder-step-import-20261005d' / 'static_body_audit.json')
SIM_SHA256 = 'b8c9a082a80ac40a5923559462c60c723a8441e0cb0b16d868b0329f905a2feb'
AUDIT_MACRO = ROOT / 'macros' / 'AuditStaticBodyGeometry.java'
DOMAIN_MACRO = ROOT / 'macros' / 'BuildExternalDomain.java'


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code_only(source: str) -> str:
    """The Java source with // and /* */ comments removed, so comment text cannot satisfy an assertion."""
    source = re.sub(r'/\*.*?\*/', ' ', source, flags=re.S)
    return re.sub(r'//[^\n]*', ' ', source)


def last_parent_before(blob: bytes, position: int, window: int = 1400):
    """The object's own 'Parent' link: the last one before its PresentationName in this serialisation."""
    chunk = blob[max(0, position - window):position].decode('latin-1')
    matches = re.findall(r"'Parent': (\d+)L", chunk)
    return matches[-1] if matches else None


class TheRealRound24FailureIsTheFixture(unittest.TestCase):
    """The delivered bytes must still prove the nested tree the defect was about."""

    def test_the_delivered_source_is_the_round24_bytes(self):
        self.assertTrue(SIM.is_file(), 'the round-24 source .sim must be kept as the fixture')
        self.assertEqual(SIM_SHA256, sha256(SIM))

    def test_the_saved_tree_keeps_the_body_name_on_a_nested_leaf(self):
        blob = SIM.read_bytes()
        self.assertIn(b'INDEPENDENT_CYLINDER_CAD_ROOT', blob,
                      'the composite root name is part of the measured tree')
        self.assertIn(b'INDEPENDENT_CYLINDER', blob)
        self.assertIn(b'star.cadmodeler.SolidModelCompositePart', blob)
        self.assertIn(b'star.cadmodeler.SolidModelPart', blob)
        root = blob.find(b"'PresentationName': 'INDEPENDENT_CYLINDER_CAD_ROOT'")
        leaf = blob.find(b"'PresentationName': 'INDEPENDENT_CYLINDER'", root + 1)
        self.assertGreater(root, 0)
        self.assertGreater(leaf, 0)
        # the ROOT is a composite that owns child parts (a non-zero ChildParts link)
        root_window = blob[max(0, root - 700):root].decode('latin-1')
        child_parts = re.findall(r"'ChildParts': (\d+)L", root_window)
        self.assertTrue(child_parts, 'the root must declare child parts')
        self.assertNotEqual('0', child_parts[-1])
        # the SOLID is nested: its own Parent link is not zero
        parent = last_parent_before(blob, leaf)
        self.assertIsNotNone(parent, 'the leaf must carry a Parent link')
        self.assertNotEqual('0', parent, 'the leaf must be nested, not a top-level part')

    def test_the_failing_record_is_preserved_and_cannot_be_cited_as_success(self):
        record = json.loads(R24_AUDIT.read_text(encoding='utf-8-sig'))
        self.assertEqual('error', record['status'])
        self.assertFalse(record['usable'])
        self.assertEqual('star.base.neo.NeoException', record['error_class'])
        self.assertIn('does not exist in manager', record['error'])
        sys.path.insert(0, str(ROOT / 'scripts'))
        from run_record_gate import citation_status
        self.assertNotEqual('CITABLE', citation_status(R24_AUDIT)['state'])


class TheLookupScansTheTreeAndSwallowsNothing(unittest.TestCase):

    def test_neither_macro_looks_a_part_up_by_getPart(self):
        for macro in (AUDIT_MACRO, DOMAIN_MACRO):
            code = code_only(macro.read_text(encoding='utf-8'))
            self.assertNotRegex(code, r'\.getPart\s*\(\s*(name|bodyName)\s*\)',
                                '%s must scan the tree, not call getPart' % macro.name)

    def test_both_macros_walk_parts_leaves_and_children(self):
        for macro in (AUDIT_MACRO, DOMAIN_MACRO):
            code = code_only(macro.read_text(encoding='utf-8'))
            self.assertIn('.getParts()', code, macro.name)
            self.assertIn('.getLeafParts()', code, macro.name)
            self.assertIn('.getChildParts().getParts()', code, macro.name)
            self.assertIn('instanceof star.common.CompositePart', code, macro.name)

    def test_the_lookup_does_not_swallow_errors(self):
        """No catch anywhere in the lookup helper: a genuine API error must surface."""
        for macro in (AUDIT_MACRO, DOMAIN_MACRO):
            code = code_only(macro.read_text(encoding='utf-8'))
            start = code.index('private static GeometryPart locatePart(')
            end = code.index('private static', start + 10)
            helper = code[start:end]
            self.assertNotIn('catch', helper, 'the lookup must not hide an API failure: ' + macro.name)
            self.assertIn('return null;', helper, macro.name)

    def test_the_missing_part_is_refused_with_the_real_tree(self):
        audit = code_only(AUDIT_MACRO.read_text(encoding='utf-8'))
        self.assertIn('has no part named', audit)
        self.assertIn('tree contains', audit)
        domain = code_only(DOMAIN_MACRO.read_text(encoding='utf-8'))
        self.assertIn('Selected CAD geometry part not found:', domain)
        self.assertIn('tree contains', domain)
        self.assertIn('found == null', domain)


class TheOtherGeometryGatesSurviveTheRevision(unittest.TestCase):

    def test_brep_solid_count_closedness_size_and_unit_checks_are_intact(self):
        audit = code_only(AUDIT_MACRO.read_text(encoding='utf-8'))
        for needle in ('body_parts',            # declared solid count
                       'isClosed()', 'isManifold()', 'getTriangleCount()',
                       'isBrepBody()', 'isSolid()', 'isMeshBody()',
                       'body_radius_m', 'body_length_m', 'model_length_unit_readback',
                       'The audited body is not where the CaseSpec says',
                       'do not match the CaseSpec'):
            self.assertIn(needle, audit, needle)

    def test_the_audit_still_refuses_a_composite_as_the_geometry(self):
        audit = code_only(AUDIT_MACRO.read_text(encoding='utf-8'))
        self.assertIn('A composite part cannot itself be the audited solid', audit)


if __name__ == '__main__':
    unittest.main()
