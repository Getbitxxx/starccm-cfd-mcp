"""Executable local cache policy. Never deletes evidence or changes Windows runs."""
from __future__ import annotations
from pathlib import Path
import shutil
import time

DEFAULT_QUOTA_BYTES = 50 * 1024**3
MIN_FREE_BYTES = 16 * 1024**3
RETENTION_DAYS = 30


def inspect_results(root, *, quota_bytes=DEFAULT_QUOTA_BYTES, min_free_bytes=MIN_FREE_BYTES, now=None, additional_roots=()):
    root=Path(root).expanduser().resolve()
    if type(quota_bytes) is not int or quota_bytes<=0 or type(min_free_bytes) is not int or min_free_bytes<0:
        raise ValueError('Result storage limits must be integer bytes')
    roots=[root,*(Path(p).expanduser().resolve() for p in additional_roots)]
    files=sorted({p.resolve() for base in roots if base.exists() for p in base.rglob('*')
                  if p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(base)})
    used=sum(p.stat().st_size for p in files)
    parent=root
    while not parent.exists():parent=parent.parent
    free=shutil.disk_usage(parent).free
    age=(time.time() if now is None else now)-RETENTION_DAYS*86400
    return {'root':str(root),'additional_roots':[str(p) for p in roots[1:]],'used_bytes':used,'quota_bytes':quota_bytes,'free_bytes':free,'min_free_bytes':min_free_bytes,
            'admission':'REJECT_NEW_FETCH' if used>=quota_bytes or free<min_free_bytes else 'ALLOW',
            'retention_days':RETENTION_DAYS,'automatic_delete':False,
            'cleanup_review_candidates':[str(p) for p in sorted(files) if p.stat().st_mtime<age],
            'cleanup_requires':'Manual reference audit and archival before deletion; age alone never authorizes removal',
            'quota_scope':'Pre-fetch occupancy guard; transfer size unknown, not a final-size reservation'}


def require_results_capacity(root, *, additional_roots=()):
    policy=inspect_results(root, additional_roots=additional_roots)
    if policy['admission']!='ALLOW':raise ValueError('RESULTS_QUOTA_EXCEEDED: '+str(policy))
    return policy
