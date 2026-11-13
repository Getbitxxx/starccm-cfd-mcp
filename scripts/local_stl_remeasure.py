"""Independent re-measurement of a binary STL on the Mac side.

Deliberately a second implementation of the same geometry reduction the Java macro performs, so the
macro's measured_* values are not the only witness to their own numbers. Read-only.
"""
import argparse, json, math, struct
ap=argparse.ArgumentParser(); ap.add_argument('stl'); ap.add_argument('--radius', type=float, required=True)
ap.add_argument('--centre', type=float, nargs=3, default=[0.0,0.0,0.0])
a=ap.parse_args()
raw=open(a.stl,'rb').read(); n=struct.unpack_from('<I',raw,80)[0]
pts=[]
assert len(raw)==84+n*50,(len(raw),n)
lo=[float('inf')]*3; hi=[float('-inf')]*3; rmin=float('inf'); rmax=0.0; vol=0.0
for i in range(n):
    off=84+i*50+12
    v=[struct.unpack_from('<fff',raw,off+k*12) for k in range(3)]
    pts.extend(v)
    for p in v:
        for ax in range(3):
            lo[ax]=min(lo[ax],p[ax]); hi[ax]=max(hi[ax],p[ax])
        r=math.dist(p,a.centre); rmin=min(rmin,r); rmax=max(rmax,r)
    (x1,y1,z1),(x2,y2,z2),(x3,y3,z3)=v
    vol+=(x1*(y2*z3-z2*y3)-y1*(x2*z3-z2*x3)+z1*(x2*y3-y2*x3))/6.0
vol=abs(vol); analytic=4/3*math.pi*a.radius**3
# The same tolerance arithmetic the Java macros use, so this second implementation can be compared
# against their recorded evidence rather than re-deriving a formula in a test.
storage_ulp=struct.unpack('<f',struct.pack('<I',struct.unpack('<I',struct.pack('<f',max(abs(v) for v in lo+hi)))[0]+1))[0]-struct.unpack('<f',struct.pack('<I',struct.unpack('<I',struct.pack('<f',max(abs(v) for v in lo+hi)))[0]))[0]
max_abs=max(abs(v) for v in lo+hi)
radius_tolerance=max(1e-6*a.radius, 8.0*storage_ulp)
centre_tolerance=max(1e-12, 8.0*storage_ulp)
centre=[sum(p[i] for p in pts)/len(pts) for i in range(3)]
centre_offset=max(abs(centre[i]-a.centre[i]) for i in range(3))
radii=[math.dist(p,centre) for p in pts]
vertex_abs_error=max(abs(min(radii)-a.radius), abs(max(radii)-a.radius))
print(json.dumps({'triangles':n,'bbox_m':lo+hi,'half_extent_m':[(hi[ax]-lo[ax])/2 for ax in range(3)],
 'min_vertex_radius_m':rmin,'max_vertex_radius_m':rmax,
 'vertex_radius_relative_error':max(abs(rmin-a.radius),abs(rmax-a.radius))/a.radius,
 'bbox_half_extent_relative_deviation':max(abs((hi[ax]-lo[ax])/2-a.radius)/a.radius for ax in range(3)),
 'enclosed_volume_m3':vol,'analytic_volume_m3':analytic,
 'volume_relative_deviation':abs(vol-analytic)/analytic,
 # the fields that must agree with the Java macros' recorded evidence
 'measured_centroid_si_m':centre,
 'measured_max_abs_coordinate_m':max_abs,
 'measured_storage_ulp_m':storage_ulp,
 'measured_vertex_radius_tolerance_m':radius_tolerance,
 'measured_centre_tolerance_m':centre_tolerance,
 'measured_vertex_radius_abs_error_m':vertex_abs_error,
 'measured_vertex_radius_error_ratio':vertex_abs_error/radius_tolerance,
 'measured_centre_offset_m':centre_offset,
 'measured_centre_offset_ratio':centre_offset/centre_tolerance,
 # the macro's own key names for the remaining shared quantities, so a comparison is mechanical
 'measured_bbox_si_m':lo+hi,
 'measured_min_vertex_radius_si_m':min(radii),
 'measured_max_vertex_radius_si_m':max(radii),
 'measured_bbox_half_extent_relative_deviation':max(abs((hi[ax]-lo[ax])/2-a.radius)/a.radius for ax in range(3)),
 'measured_enclosed_volume_si_m3':vol,
 'measured_volume_relative_deviation':abs(vol-analytic)/analytic,
 'analytic_volume_si_m3':analytic},indent=2))
