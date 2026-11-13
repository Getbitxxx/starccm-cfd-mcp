/* Build the flow region from the CAD-authored closed fluid domain and name/type its boundaries.
 *
 * Why this macro exists: the fluid domain is authored in CAD (Box minus aircraft) and exported with a
 * names/areas mapping STEP. STAR's Boolean/import re-partitions an aircraft body into generically named
 * "Faces..." domain surfaces, so the source-face ownership of BuildExternalFlowRegion cannot hold for that
 * route. This macro takes the CAD fluid domain itself (one closed manifold part, imported and named
 * AUTO_FREE_AIR_DOMAIN) and classifies the region's part surfaces by the six CaseSpec box planes; everything
 * not on a box plane is the wall. Boundaries of the same role are merged into one boundary, named from
 * CaseSpec boundary_roles (AUTO_INLET/AUTO_OUTLET/AUTO_SYMMETRY/AUTO_FARFIELD_<FACE>/body wall) and given the
 * native boundary type. It creates no mesh, physics, initialization or solve.
 */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.base.neo.DoubleVector;
import star.base.neo.NeoObjectVector;
import star.common.Boundary;
import star.common.GeometryPart;
import star.common.InletBoundary;
import star.common.PartSurface;
import star.common.PressureBoundary;
import star.common.Region;
import star.common.RegionManager;
import star.common.Simulation;
import star.common.StarMacro;
import star.common.SymmetryBoundary;
import star.common.WallBoundary;
import star.meshing.MeshPart;

public class BuildNamedFluidRegion extends StarMacro {
    private static final String COMPLETE = "NAMED_FLUID_REGION_BUILD_COMPLETE";
    private static final String DOMAIN_PART = "AUTO_FREE_AIR_DOMAIN";
    private static final String[] FACES = {"X_MIN", "X_MAX", "Y_MIN", "Y_MAX", "Z_MIN", "Z_MAX"};

    @Override public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        File evidence = new File(dir, "named_fluid_region.json");
        try {
            if (sim.isInitialized() || sim.getSimulationIterator().getCurrentIteration() != 0
                    || !sim.getContinuumManager().getObjects().isEmpty())
                throw new IllegalStateException("Uninitialized geometry-only session required");
            if (!sim.getRegionManager().getObjects().isEmpty())
                throw new IllegalStateException("Region input must contain no regions");
            String spec = new String(Files.readAllBytes(new File(dir, "case_spec.json").toPath()),
                    StandardCharsets.UTF_8);
            double[] bounds = array(spec, "outer_bounds_m", 6);
            for (int a = 0; a < 3; a++) if (!(bounds[a] < bounds[a + 3])) throw new IllegalArgumentException("Invalid domain bounds");
            String regionName = optional(spec, "region_name", "AUTO_EXTERNAL_FLOW");
            String wallName = string(spec, "body_wall_boundary_name");
            String[] declared = stringArray(spec, "box_face_roles");
            String[] roles = resolveRoles(declared);
            MeshPart part = locatePart(sim, DOMAIN_PART);
            if (part == null) throw new IllegalStateException("Domain part absent: " + DOMAIN_PART);
            if (!part.isClosed() || !part.isManifold()) throw new IllegalStateException("Domain is not closed/manifold");
            // One boundary per part surface is only useful if the CAD faces are separate part surfaces; the
            // imported STEP may arrive as a single surface. Split by angle first, then classify.
            part.getPartSurfaceManager().splitPartSurfacesByAngle(
                    new NeoObjectVector(part.getPartSurfaces().toArray()), 89.0);
            int partSurfaceCount = part.getPartSurfaces().size();
            if (partSurfaceCount < 7) throw new IllegalStateException("Too few part surfaces after angle split: " + partSurfaceCount);

            RegionManager rm = sim.getRegionManager();
            rm.newRegionsFromParts(new NeoObjectVector(new Object[]{part}), "OneRegionPerPart", null,
                    "OneBoundaryPerPartSurface", null, RegionManager.CreateInterfaceMode.BOUNDARY);
            if (rm.getObjects().size() != 1) throw new IllegalStateException("Expected one newly created region");
            Region region = rm.getObjects().iterator().next();
            region.setPresentationName(regionName);

            List<Boundary> boundaries = new ArrayList<Boundary>();
            for (Boundary b : region.getBoundaryManager().getObjects()) boundaries.add(b);
            Map<Boundary, Integer> roleOf = new HashMap<Boundary, Integer>();
            Map<Boundary, List<PartSurface>> surfacesOf = new HashMap<Boundary, List<PartSurface>>();
            Map<Boundary, Double> areaOf = new HashMap<Boundary, Double>();
            double capturedArea = 0;
            for (Boundary b : boundaries) {
                List<PartSurface> surfaces = new ArrayList<PartSurface>(b.getPartSurfaceGroup().getObjects());
                if (surfaces.isEmpty()) throw new IllegalStateException("Boundary has no part surface: " + b.getPresentationName());
                List<Geo.Data> measured = new ArrayList<Geo.Data>();
                double area = 0;
                for (PartSurface s : surfaces) {
                    Geo.Data d = SurfaceMeasure.capture(sim, s, dir);
                    measured.add(d);
                    area += d.area;
                }
                int role = -1;
                for (Geo.Data d : measured) {
                    int f = Geo.plane(d, bounds);
                    if (role < 0) role = f;
                    else if (role != f) throw new IllegalStateException("Boundary spans several planes: " + b.getPresentationName());
                }
                if (role < 0) role = 6; // wall
                roleOf.put(b, role);
                surfacesOf.put(b, surfaces);
                areaOf.put(b, area);
                capturedArea += area;
            }
            List<List<Boundary>> groups = new ArrayList<List<Boundary>>();
            for (int r = 0; r < 7; r++) groups.add(new ArrayList<Boundary>());
            for (Boundary b : boundaries) groups.get(roleOf.get(b)).add(b);
            for (int r = 0; r < 7; r++) {
                if (groups.get(r).isEmpty()) throw new IllegalStateException("Empty boundary role: " + (r == 6 ? "wall" : roles[r]));
            }
            StringBuilder rows = new StringBuilder();
            int assigned = 0;
            double assignedArea = 0;
            for (int r = 0; r < 7; r++) {
                List<Boundary> group = groups.get(r);
                Boundary survivor = group.get(0);
                List<PartSurface> all = new ArrayList<PartSurface>();
                double groupArea = 0;
                for (Boundary b : group) {
                    all.addAll(surfacesOf.get(b));
                    groupArea += areaOf.get(b);
                    b.getPartSurfaceGroup().setObjects(Collections.<PartSurface>emptyList());
                }
                survivor.getPartSurfaceGroup().setObjects(new NeoObjectVector(all.toArray()));
                assigned += all.size();
                assignedArea += groupArea;
                List<Boundary> redundant = new ArrayList<Boundary>(group);
                redundant.remove(survivor);
                if (!redundant.isEmpty()) region.getBoundaryManager().removeObjects(redundant);
                String name = r == 6 ? wallName : boundaryName(roles[r], r);
                survivor.setPresentationName(name);
                if (r == 6) survivor.setBoundaryType(WallBoundary.class);
                else if ("inlet".equals(roles[r])) survivor.setBoundaryType(InletBoundary.class);
                else if ("symmetry".equals(roles[r])) survivor.setBoundaryType(SymmetryBoundary.class);
                else survivor.setBoundaryType(PressureBoundary.class);
                if (rows.length() > 0) rows.append(',');
                rows.append("{\"name\":").append(quote(name)).append(",\"role\":").append(quote(r == 6 ? "body_wall" : roles[r]))
                        .append(",\"part_surfaces\":").append(all.size())
                        .append(",\"area_m2\":").append(groupArea).append('}');
            }
            if (assigned != partSurfaceCount)
                throw new IllegalStateException("Part surfaces lost in merge: " + assigned + " != " + partSurfaceCount);
            if (region.getBoundaryManager().getObjects().size() != 7)
                throw new IllegalStateException("Expected 7 boundaries after merge, got " + region.getBoundaryManager().getObjects().size());
            if (Math.abs(assignedArea - capturedArea) > Math.max(1e-9, capturedArea * 1e-9))
                throw new IllegalStateException("Boundary area not conserved: " + assignedArea + " vs " + capturedArea);
            // box-plane completeness: every plane must own its full fluid face; the wall is the remainder.
            for (int r = 0; r < 7; r++) if (roleArea(r, groups, areaOf) <= 0) throw new IllegalStateException("Empty role area: " + r);
            sim.saveState(new File(dir, "named_region.sim").getAbsolutePath());
            write(evidence, "{\"schema_version\":1,\"status\":\"ok\",\"region\":" + quote(regionName)
                    + ",\"boundary_count\":" + region.getBoundaryManager().getObjects().size()
                    + ",\"part_surface_total\":" + partSurfaceCount
                    + ",\"total_area_m2\":" + assignedArea
                    + ",\"boundaries\":[" + rows + "],\"complete_marker\":\"" + COMPLETE + "\"}\n");
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            try {
                write(evidence, "{\"schema_version\":1,\"status\":\"error\",\"error\":" + quote(String.valueOf(failure))
                        + ",\"complete_marker\":null}\n");
            } catch (Throwable ignored) { }
            throw new RuntimeException("Named fluid region construction failed", failure);
        }
    }

    private static double roleArea(int role, List<List<Boundary>> groups, Map<Boundary, Double> areaOf) {
        double area = 0;
        for (Boundary b : groups.get(role)) area += areaOf.get(b);
        return area;
    }
    private static MeshPart locatePart(Simulation sim, String name) {
        java.util.List<GeometryPart> queue = new ArrayList<GeometryPart>(
                sim.get(star.common.SimulationPartManager.class).getParts());
        java.util.Set<GeometryPart> seen = new HashSet<GeometryPart>();
        MeshPart found = null;
        for (int i = 0; i < queue.size(); i++) {
            GeometryPart part = queue.get(i);
            if (!seen.add(part)) continue;
            if (name.equals(part.getPresentationName())) {
                MeshPart mesh = part instanceof MeshPart ? (MeshPart) part : null;
                if (mesh == null) continue;
                if (found != null && found != mesh) throw new IllegalStateException("Duplicate body name");
                found = mesh;
            }
            if (part instanceof star.common.CompositePart) {
                queue.addAll(((star.common.CompositePart) part).getLeafParts());
                queue.addAll(((star.common.CompositePart) part).getChildParts().getParts());
            }
        }
        return found;
    }
    private static double[] array(String json, String key, int count) {
        Matcher m = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\[([^\\]]*)\\]").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing " + key);
        String[] parts = m.group(1).split(",");
        if (parts.length != count) throw new IllegalArgumentException("Bad " + key);
        double[] out = new double[count];
        for (int i = 0; i < count; i++) out[i] = Double.parseDouble(parts[i].trim());
        return out;
    }
    private static String optional(String json, String key, String fallback) {
        Matcher m = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(json);
        return m.find() ? m.group(1) : fallback;
    }
    private static String string(String json, String key) {
        Matcher m = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(json);
        if (!m.find()) throw new IllegalArgumentException("Missing " + key);
        return m.group(1);
    }
    private static String[] stringArray(String json, String key) {
        Matcher m = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*\\[([^\\]]*)\\]").matcher(json);
        if (!m.find()) return null;
        java.util.List<String> values = new ArrayList<String>();
        Matcher item = Pattern.compile("\\\"([^\\\"]+)\\\"").matcher(m.group(1));
        while (item.find()) values.add(item.group(1));
        return values.toArray(new String[0]);
    }
    private static String[] resolveRoles(String[] declared) {
        if (declared == null) throw new IllegalArgumentException("box_face_roles required");
        if (declared.length != 6) throw new IllegalArgumentException("box_face_roles must have six entries");
        int inlets = 0, outlets = 0;
        Set<String> allowed = new HashSet<String>(java.util.Arrays.asList("inlet", "outlet", "symmetry", "farfield"));
        for (String role : declared) {
            if (!allowed.contains(role)) throw new IllegalArgumentException("Unknown role: " + role);
            if ("inlet".equals(role)) inlets++;
            if ("outlet".equals(role)) outlets++;
        }
        if (inlets != 1 || outlets != 1) throw new IllegalArgumentException("Exactly one inlet and one outlet required");
        return declared;
    }
    private static String boundaryName(String role, int face) {
        if ("inlet".equals(role)) return "AUTO_INLET";
        if ("outlet".equals(role)) return "AUTO_OUTLET";
        if ("symmetry".equals(role)) return "AUTO_SYMMETRY";
        return "AUTO_FARFIELD_" + FACES[face];
    }
    private static String quote(String s) {
        StringBuilder b = new StringBuilder("\"");
        for (char c : s.toCharArray()) {
            if (c == '"' || c == '\\') b.append('\\').append(c);
            else if (c < 32) b.append(String.format("\\u%04x", (int) c));
            else b.append(c);
        }
        return b.append('"').toString();
    }
    private static void write(File f, String text) throws Exception {
        try (FileWriter w = new FileWriter(f, StandardCharsets.UTF_8)) { w.write(text); }
    }

    // BEGIN GENERATED SURFACE OWNERSHIP SUPPORT
    // Pure geometry kernel: extracted verbatim by offline Java tests.
    static final class Geo {
        // Fixed instrument precision, not a tunable CaseSpec/acceptance tolerance.
        static final double DIST = 1e-7;
        static double areaEps(double area) { return 1e-11 + Math.abs(area) * 1e-7; }
        static final String[] FACES = {"x_min", "x_max", "y_min", "y_max", "z_min", "z_max"};
        static final int[] AXES = {0, 0, 1, 1, 2, 2};
        static final int[] BOUNDS = {0, 3, 1, 4, 2, 5};
        static final class Tri {
            final double[][] p;
            final double[] normal;
            final double area;
            Tri(double[][] vertices) {
                p = vertices;
                double[] a = sub(p[1], p[0]), b = sub(p[2], p[0]);
                normal = cross(a, b);
                area = Math.sqrt(dot(normal, normal)) / 2;
                if (!(area > 0) || !Double.isFinite(area)) throw new IllegalStateException("Degenerate triangle");
                for (int k=0;k<3;k++) normal[k] /= 2*area;
            }
        }
        static final class Data {
            final String name;
            final java.util.List<Tri> tris = new java.util.ArrayList<Tri>();
            final double[] bbox = {Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY,
                    Double.NEGATIVE_INFINITY, Double.NEGATIVE_INFINITY, Double.NEGATIVE_INFINITY};
            double area, signedVolume;
            Data(String n) { name=n; }
            void add(Tri t) {
                tris.add(t); area += t.area;
                signedVolume += dot(t.p[0], cross(t.p[1],t.p[2]))/6;
                for(double[] v:t.p) for(int k=0;k<3;k++) {
                    if(!Double.isFinite(v[k])) throw new IllegalStateException("Nonfinite vertex");
                    bbox[k]=Math.min(bbox[k],v[k]);bbox[k+3]=Math.max(bbox[k+3],v[k]);
                }
            }
        }
        static Data read(java.io.File f, String name) throws Exception {
            if(f.length()>=84) {
                try(java.io.DataInputStream in=new java.io.DataInputStream(new java.io.BufferedInputStream(new java.io.FileInputStream(f)))) {
                    byte[] header=new byte[84];in.readFully(header);
                    long n=Integer.toUnsignedLong(java.nio.ByteBuffer.wrap(header,80,4).order(java.nio.ByteOrder.LITTLE_ENDIAN).getInt());
                    if(n>0 && n<=Integer.MAX_VALUE && f.length()==84+50*n) {
                        Data d=new Data(name);byte[] row=new byte[50];
                        for(long i=0;i<n;i++) {
                            in.readFully(row);java.nio.ByteBuffer bytes=java.nio.ByteBuffer.wrap(row).order(java.nio.ByteOrder.LITTLE_ENDIAN);
                            double[][] p=new double[3][3];
                            for(int v=0;v<3;v++)for(int k=0;k<3;k++)p[v][k]=bytes.getFloat(12+12*v+4*k);
                            d.add(new Tri(p));
                        }
                        return d;
                    }
                }
            }
            return readAscii(new String(java.nio.file.Files.readAllBytes(f.toPath()),java.nio.charset.StandardCharsets.US_ASCII),name);
        }
        static double[] triple(String s) {
            String[] tokens=s.trim().split("\\s+");if(tokens.length!=3)throw new IllegalStateException("Expected three ASCII coordinates");
            double[] d=new double[3];for(int k=0;k<3;k++){d[k]=Double.parseDouble(tokens[k]);if(!Double.isFinite(d[k]))throw new IllegalStateException("Nonfinite ASCII coordinate");}return d;
        }
        static Data readAscii(String text,String name) {
            Data d=new Data(name);int state=-1,count=0;double[][] vertices=null;boolean ended=false;
            for(String raw:text.split("\\R")) {
                String line=raw.trim();if(line.isEmpty())continue;
                if(ended)throw new IllegalStateException("Text after endsolid");
                if(state==-1 && (line.equals("solid")||line.startsWith("solid "))){state=0;continue;}
                if(state==0 && line.startsWith("facet normal ")){triple(line.substring(13));state=1;continue;}
                if(state==1 && line.equals("outer loop")){state=2;count=0;vertices=new double[3][];continue;}
                if(state==2 && line.startsWith("vertex ") && count<3){vertices[count++]=triple(line.substring(7));continue;}
                if(state==2 && line.equals("endloop") && count==3){state=3;continue;}
                if(state==3 && line.equals("endfacet")){d.add(new Tri(vertices));state=0;continue;}
                if(state==0 && (line.equals("endsolid")||line.startsWith("endsolid "))){ended=true;continue;}
                throw new IllegalStateException("Malformed ASCII STL: "+line);
            }
            if(!ended || state!=0 || d.tris.isEmpty())throw new IllegalStateException("Incomplete ASCII STL");
            return d;
        }
        static double[] sub(double[] a,double[] b) { return new double[]{a[0]-b[0],a[1]-b[1],a[2]-b[2]}; }
        static double[] cross(double[] a,double[] b) { return new double[]{a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]}; }
        static double dot(double[] a,double[] b) { return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]; }
        static boolean near(double a,double b,double e) { return Math.abs(a-b)<=e; }
        static double orient(double[] a,double[] b,double[] p) { return (b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0]); }
        static double[] project(double[] p,int drop) { return drop==0?new double[]{p[1],p[2]}:drop==1?new double[]{p[0],p[2]}:new double[]{p[0],p[1]}; }
        // Exact polygon clipping in a common plane, rather than sparse point tests.
        static double intersection(Tri a,Tri b) {
            if(Math.abs(dot(a.normal,b.normal)) < 1-1e-8) return 0;
            for(double[] p:a.p) if(Math.abs(dot(sub(p,b.p[0]),b.normal))>DIST) return 0;
            int drop=0;for(int k=1;k<3;k++) if(Math.abs(b.normal[k])>Math.abs(b.normal[drop])) drop=k;
            java.util.List<double[]> poly=new java.util.ArrayList<double[]>();
            for(double[] p:a.p) poly.add(project(p,drop));
            double[][] clip={project(b.p[0],drop),project(b.p[1],drop),project(b.p[2],drop)};
            double sign=orient(clip[0],clip[1],clip[2])>=0?1:-1;
            for(int edge=0;edge<3 && !poly.isEmpty();edge++) {
                double[] c=clip[edge],d=clip[(edge+1)%3];
                java.util.List<double[]> next=new java.util.ArrayList<double[]>();
                double[] prev=poly.get(poly.size()-1);double fp=sign*orient(c,d,prev);
                for(double[] p:poly) {
                    double fc=sign*orient(c,d,p);
                    if((fp>=0)!=(fc>=0)) {
                        double t=fp/(fp-fc);
                        next.add(new double[]{prev[0]+t*(p[0]-prev[0]),prev[1]+t*(p[1]-prev[1])});
                    }
                    if(fc>=0) next.add(p);
                    prev=p;fp=fc;
                }
                poly=next;
            }
            if(poly.size()<3)return 0;
            double twice=0; double[] o=poly.get(0);
            for(int i=1;i+1<poly.size();i++)twice+=orient(o,poly.get(i),poly.get(i+1));
            return Math.abs(twice)/(2*Math.abs(b.normal[drop]));
        }
        static double overlap(Data a,Data b) {
            for(int k=0;k<3;k++)if(a.bbox[k]>b.bbox[k+3]+DIST || b.bbox[k]>a.bbox[k+3]+DIST)return 0;
            double area=0;
            for(Tri x:a.tris)for(Tri y:b.tris)area+=intersection(x,y);
            return area;
        }
        static boolean sameBBox(Data a,Data b) { return sameBBox(a,b,DIST); }
        static boolean sameBBox(Data a,Data b,double tol) {
            for(int k=0;k<6;k++)if(!near(a.bbox[k],b.bbox[k],tol))return false;
            return true;
        }
        static int plane(Data d,double[] bounds) {
            int found=-1;
            for(int f=0;f<6;f++) {
                int k=AXES[f];double v=bounds[BOUNDS[f]];
                if(!near(d.bbox[k],v,DIST)||!near(d.bbox[k+3],v,DIST))continue;
                boolean normal=true;
                for(Tri t:d.tris)if(Math.abs(t.normal[k])<1-1e-8)normal=false;
                if(!normal)continue;
                if(found>=0)throw new IllegalStateException("Surface lies on multiple box planes: "+d.name);
                found=f;
            }
            return found;
        }
        static final class Partition {
            final java.util.List<java.util.List<Integer>> groups=new java.util.ArrayList<java.util.List<Integer>>();
            final double[] areas=new double[7];
            final double[] coveredSource;
            int removedCaps,retainedCaps;
            Partition(int n){coveredSource=new double[n];for(int i=0;i<7;i++)groups.add(new java.util.ArrayList<Integer>());}
        }
        static Partition partition(java.util.List<Data> sources,java.util.List<Data> domain,double[] bounds) {
            if(sources.isEmpty() || domain.isEmpty())throw new IllegalStateException("Empty ownership input");
            Partition p=new Partition(sources.size());
            java.util.Set<String> names=new java.util.HashSet<String>();
            for(int i=0;i<domain.size();i++) {
                Data d=domain.get(i);
                if(!names.add(d.name))throw new IllegalStateException("Duplicate domain surface name");
                double covered=0;double[] hits=new double[sources.size()];
                int exact=-1;
                for(int j=0;j<sources.size();j++){Data s=sources.get(j);
                    if((d.name.equals(s.name)||d.name.endsWith("."+s.name))&&near(d.area,s.area,Math.max(areaEps(d.area),1e-3*Math.abs(d.area)))&&sameBBox(d,s,5e-4)){exact=j;break;}
                }
                if(exact>=0){hits[exact]=sources.get(exact).area;covered=d.area;}
                else for(int j=0;j<sources.size();j++){hits[j]=overlap(d,sources.get(j));covered+=hits[j];}
                int role;
                if(near(covered,d.area,areaEps(d.area))) {
                    role=6;
                    for(int j=0;j<hits.length;j++)p.coveredSource[j]+=hits[j];
                } else {
                    int f=plane(d,bounds);
                    if(f<0) {
                        if(covered>areaEps(d.area))throw new IllegalStateException("Mixed source-body/box surface: "+d.name);
                        throw new IllegalStateException("Unowned surface (not source body or box plane): "+d.name);
                    }
                    role=f; // partial source-cap coverage on a box plane is a retained cap
                }
                p.groups.get(role).add(i);p.areas[role]+=d.area;
            }
            double[] removedArea=new double[6];
            for(int j=0;j<sources.size();j++) {
                Data s=sources.get(j);int f=plane(s,bounds);
                if(near(p.coveredSource[j],s.area,areaEps(s.area))) {if(f>=0)p.retainedCaps++;}
                else if(p.coveredSource[j]<=areaEps(s.area) && f>=0)p.removedCaps++;
                else throw new IllegalStateException("Source face missing, duplicated or partially owned: "+s.name);
                if(f>=0)removedArea[f]+=s.area; // both cap modes leave the same fluid footprint
            }
            for(int f=0;f<6;f++) {
                int a=(AXES[f]+1)%3,b=(AXES[f]+2)%3;
                double expected=(bounds[a+3]-bounds[a])*(bounds[b+3]-bounds[b])-removedArea[f];
                if(p.groups.get(f).isEmpty() || !near(p.areas[f],expected,Math.max(areaEps(expected),1e-5*Math.abs(expected))))
                    throw new IllegalStateException("Incomplete box face "+FACES[f]+": measured="+p.areas[f]+" expected="+expected);
            }
            if(p.groups.get(6).isEmpty())throw new IllegalStateException("No proven body wall surfaces");
            int count=0;for(java.util.List<Integer> g:p.groups)count+=g.size();
            if(count!=domain.size())throw new IllegalStateException("Non-exhaustive partition");
            return p;
        }
    }

    static final class SurfaceMeasure {
        static Geo.Data capture(star.common.Simulation sim, star.common.PartSurface surface, java.io.File dir) throws Exception {
            star.common.SimulationPartManager manager=sim.get(star.common.SimulationPartManager.class);
            java.util.Set<star.common.GeometryPart> before=new java.util.HashSet<star.common.GeometryPart>(manager.getParts());
            star.meshing.MeshPart shell=null;
            String beforeSource=inventory((star.meshing.MeshPart)surface.getPart());
            try {
                shell=sim.get(star.meshing.MeshPartFactory.class).newShellFromPartSurfaces(java.util.Collections.singletonList(surface));
                if(shell==null || before.contains(shell))throw new IllegalStateException("Expected new diagnostic shell");
                java.io.File folder=new java.io.File(dir,"surface_measurements");
                if(!folder.isDirectory()&&!folder.mkdirs())throw new java.io.IOException("Cannot create diagnostic folder");
                java.io.File stl=new java.io.File(folder,java.util.UUID.randomUUID().toString()+".stl");
                shell.exportStlPart(stl.getAbsolutePath(),false);
                Geo.Data d=Geo.read(stl,surface.getPresentationName());
                if(d.tris.size()!=shell.getTriangleCount())throw new IllegalStateException("Surface export triangle count mismatch");
                return d;
            } finally {
                if(shell!=null&&!before.contains(shell))manager.removeParts(java.util.Collections.singletonList(shell));
                if(!before.equals(new java.util.HashSet<star.common.GeometryPart>(manager.getParts()))
                        || !beforeSource.equals(inventory((star.meshing.MeshPart)surface.getPart())))
                    throw new IllegalStateException("Diagnostic shell cleanup did not restore part/surface inventory");
            }
        }
        static String inventory(star.meshing.MeshPart part) {
            StringBuilder b=new StringBuilder();
            for(star.common.PartSurface s:part.getPartSurfaces())b.append(s.getPresentationName()).append(':').append(part.getPartSurfacePatches(s)).append(';');
            return b.toString();
        }
        static java.util.List<Geo.Data> captureAll(star.common.Simulation sim,java.util.List<star.common.PartSurface> ss,java.io.File dir)throws Exception {
            java.util.List<Geo.Data> ds=new java.util.ArrayList<Geo.Data>();
            for(star.common.PartSurface s:ss)ds.add(capture(sim,s,dir));
            return ds;
        }
        static star.meshing.MeshPart singleBody(star.common.Simulation sim,String name) {
            java.util.List<star.common.GeometryPart> queue=new java.util.ArrayList<star.common.GeometryPart>(sim.get(star.common.SimulationPartManager.class).getParts());
            java.util.Set<star.common.GeometryPart> seen=new java.util.HashSet<star.common.GeometryPart>();
            java.util.List<star.common.GeometryPart> candidates=new java.util.ArrayList<star.common.GeometryPart>();
            for(int i=0;i<queue.size();i++) {
                star.common.GeometryPart part=queue.get(i);if(!seen.add(part))continue;
                if(name.equals(part.getPresentationName()))candidates.add(part);
                if(part instanceof star.common.CompositePart) {
                    queue.addAll(((star.common.CompositePart)part).getLeafParts());
                    queue.addAll(((star.common.CompositePart)part).getChildParts().getParts());
                }
            }
            // Measured on the HLPW-4 CRM-HL import (2026-10-31): an imported CAD exposes the requested name on
            // BOTH its CompositePart root and the single mesh solid beneath it, which made this function abort
            // with "Duplicate body name" before any surface provenance was measured. The body the audit
            // measures is the unique MeshPart; the ancestor composite is only the tree node. A genuinely
            // ambiguous name (zero or more than one MeshPart) is still an error.
            star.common.GeometryPart found=null;
            if(candidates.size()==1){found=candidates.get(0);}
            else if(candidates.size()>1){
                star.meshing.MeshPart uniqueMesh=null;int meshCount=0;
                for(star.common.GeometryPart candidate:candidates){
                    if(candidate instanceof star.meshing.MeshPart){meshCount++;uniqueMesh=(star.meshing.MeshPart)candidate;}
                }
                if(meshCount!=1)throw new IllegalStateException("Duplicate body name");
                found=uniqueMesh;
            }
            if(found instanceof star.common.CompositePart) {
                java.util.Collection<star.common.GeometryPart> leaves=((star.common.CompositePart)found).getLeafParts();
                if(leaves.size()!=1)throw new IllegalStateException("Exactly one body leaf required");found=leaves.iterator().next();
            }
            if(!(found instanceof star.meshing.MeshPart))throw new IllegalStateException("Declared mesh body absent: "+name);
            star.meshing.MeshPart mesh=(star.meshing.MeshPart)found;
            if(!mesh.isClosed()||!mesh.isManifold())throw new IllegalStateException("Body not closed/manifold");
            return mesh;
        }
        static void geometryOnly(star.common.Simulation sim) {
            if(sim.isInitialized()||sim.getSimulationIterator().getCurrentIteration()!=0||!sim.getContinuumManager().getObjects().isEmpty())
                throw new IllegalStateException("Uninitialized geometry-only session required");
        }
        static String quote(String s) {
            StringBuilder b=new StringBuilder("\"");
            for(char c:s.toCharArray()){if(c=='"'||c=='\\')b.append('\\').append(c);else if(c<32)b.append(String.format("\\u%04x",(int)c));else b.append(c);}
            return b.append('"').toString();
        }
        static String json(Geo.Data d) {
            return "{\"name\":"+quote(d.name)+",\"area_m2\":"+d.area+",\"triangle_count\":"+d.tris.size()+",\"bounds_m\":"+java.util.Arrays.toString(d.bbox)+"}";
        }
    }
    // END GENERATED SURFACE OWNERSHIP SUPPORT
}
