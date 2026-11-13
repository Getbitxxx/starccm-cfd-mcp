"""Strict binary-STL topology/volume audit; self-intersection is not inferred."""
from collections import Counter
import hashlib
import math
from pathlib import Path
import struct


def audit_binary_stl(path, *, cad_volume=None, max_volume_error=0.001):
    data=Path(path).read_bytes()
    if len(data)<84:raise ValueError('Truncated binary STL')
    count=struct.unpack_from('<I',data,80)[0]
    if count==0 or len(data)!=84+50*count:raise ValueError('STL triangle count/byte layout mismatch')
    edges=Counter();directions=Counter();triangles=Counter();degenerate=0;volume=0.0
    low=[math.inf]*3;high=[-math.inf]*3
    for n in range(count):
        values=struct.unpack_from('<12fH',data,84+n*50)
        if not all(math.isfinite(x) for x in values[:12]):raise ValueError('Nonfinite STL coordinate/normal')
        a,b,c=[tuple(values[i:i+3]) for i in (3,6,9)]
        triangles[tuple(sorted((a,b,c)))]+=1
        u=[b[i]-a[i] for i in range(3)];v=[c[i]-a[i] for i in range(3)]
        cross=(u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0])
        if sum(x*x for x in cross)==0:degenerate+=1
        volume+=sum(a[i]*cross[i] for i in range(3))/6
        for point in (a,b,c):
            for i in range(3):low[i]=min(low[i],point[i]);high[i]=max(high[i],point[i])
        for start,end in ((a,b),(b,c),(c,a)):
            edge=tuple(sorted((start,end)));edges[edge]+=1
            directions[edge]+=1 if (start,end)==edge else -1
    boundary=sum(v==1 for v in edges.values());nonmanifold=sum(v!=2 and v!=1 for v in edges.values())
    orientation=sum(v!=0 for edge,v in directions.items() if edges[edge]==2)
    duplicate=sum(v-1 for v in triangles.values() if v>1)
    error=None
    if cad_volume is not None:
        if not math.isfinite(cad_volume) or cad_volume<=0:raise ValueError('Invalid CAD volume')
        error=abs(volume-cad_volume)/cad_volume
    passed=(boundary==nonmanifold==orientation==duplicate==degenerate==0 and volume>0 and
            (error is None or error<=max_volume_error))
    return dict(sha256=hashlib.sha256(data).hexdigest(),triangle_count=count,bounds=low+high,
                signed_volume=volume,boundary_edges=boundary,nonmanifold_edges=nonmanifold,
                inconsistent_orientation_edges=orientation,duplicate_triangles=duplicate,
                degenerate_triangles=degenerate,cad_volume_relative_error=error,
                max_volume_relative_error=max_volume_error,topology_and_volume_passed=passed,
                self_intersection_checked=False,engineering_verified=False)
