"""Embed reviewed support so each STAR macro is self-contained at execution."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MACROS = ROOT / 'macros'


def sync():
    support = '\n'.join((MACROS / 'lib' / name).read_text() for name in
                        ['SurfaceOwnershipGeometry.inc', 'SurfaceOwnershipSupport.inc'])
    region = (MACROS / 'BuildExternalFlowRegion.java').read_text()
    contract = region[region.index('    static String verifyGroups('):
                      region.index('    // BEGIN GENERATED SURFACE OWNERSHIP SUPPORT')]
    for name in ['BuildExternalFlowRegion.java', 'AuditBoundaryRoles.java',
                 'AuditBoundaryPhysics.java', 'BuildExternalDomain.java']:
        path = MACROS / name
        text = path.read_text()
        if 'BEGIN GENERATED REGION CONTRACT' in text:
            a = text.index('    // BEGIN GENERATED REGION CONTRACT\n') + len('    // BEGIN GENERATED REGION CONTRACT\n')
            b = text.index('    // END GENERATED REGION CONTRACT', a)
            text = text[:a] + contract + text[b:]
        a = text.index('    // BEGIN GENERATED SURFACE OWNERSHIP SUPPORT\n') + len('    // BEGIN GENERATED SURFACE OWNERSHIP SUPPORT\n')
        b = text.index('    // END GENERATED SURFACE OWNERSHIP SUPPORT', a)
        text = text[:a] + support + text[b:]
        path.write_text(text)


if __name__ == '__main__':
    sync()
