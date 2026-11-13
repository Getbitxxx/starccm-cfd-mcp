/* Reopen-only actual boundary group/type/area audit. Never saves or sets boundaries. */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.*;
import java.util.regex.*;
import star.common.*;
import star.meshing.*;

public class AuditBoundaryRoles extends StarMacro {
    @Override public void execute() {
        Simulation sim=getActiveSimulation();File dir=sim.getSessionDirFile();
        try {
            SurfaceMeasure.geometryOnly(sim);
            String spec=Files.readString(new File(dir,"case_spec.json").toPath());
            String bodyName=optional(spec,"body_part_name","BODY"),regionName=optional(spec,"region_name","AUTO_EXTERNAL_FLOW");
            String wallName=optional(spec,"body_wall_boundary_name","AUTO_"+bodyName+"_WALL");
            validateAliases(spec,regionName,wallName);
            String[] declared=stringArray(spec,"box_face_roles"),roles=resolveRoles(declared);
            MeshPart body=SurfaceMeasure.singleBody(sim,bodyName);
            MeshPart domain=SurfaceMeasure.singleBody(sim,"AUTO_FREE_AIR_DOMAIN");
            List<PartSurface> ss=new ArrayList<PartSurface>(domain.getPartSurfaces());
            List<Geo.Data> ds=SurfaceMeasure.captureAll(sim,ss,dir);
            Geo.Partition plan=Geo.partition(SurfaceMeasure.captureAll(sim,new ArrayList<PartSurface>(body.getPartSurfaces()),dir),ds,array(spec,"outer_bounds_m",6));
            if(sim.getRegionManager().getObjects().size()!=1)throw new IllegalStateException("One region required");
            Region region=sim.getRegionManager().getObjects().iterator().next();
            if(!regionName.equals(region.getPresentationName()))throw new IllegalStateException("Region name mismatch");
            List<Boundary> actual=new ArrayList<Boundary>();
            for(int r=0;r<7;r++) {
                String name=r==6?wallName:boundaryName(roles[r],r,declared==null);Boundary found=null;
                for(Boundary b:region.getBoundaryManager().getObjects())if(name.equals(b.getPresentationName())){if(found!=null)throw new IllegalStateException("Duplicate boundary name");found=b;}
                if(found==null)throw new IllegalStateException("Missing actual boundary "+name);actual.add(found);
            }
            String readback=verifyGroups(region,actual,plan,ss,ds,roles,declared==null);
            SurfaceMeasure.geometryOnly(sim);
            write(new File(dir,"boundaries.json"),"{\"schema_version\":1,\"status\":\"ok\",\"record_scope\":\"ad_hoc_readonly_roles\",\"actual_boundaries\":"+readback+","+partitionJson(plan,ds)+"}");
            System.out.println("BOUNDARY_ROLES_AUDITED");
        }catch(Throwable e){try{write(new File(dir,"boundaries.json"),"{\"status\":\"error\",\"error\":"+SurfaceMeasure.quote(e.toString())+"}");}catch(Exception x){e.addSuppressed(x);}throw new RuntimeException("Read-only boundary audit failed",e);}
    }
    // BEGIN GENERATED REGION CONTRACT
    static String verifyGroups(Region region,List<Boundary> kept,Geo.Partition plan,List<PartSurface> ss,List<Geo.Data> ds,String[] roles,boolean legacy) {
        if(region.getBoundaryManager().getObjects().size()!=7)throw new IllegalStateException("Seven boundaries required");
        Set<PartSurface> all=new HashSet<PartSurface>();Set<String> names=new HashSet<String>();StringBuilder json=new StringBuilder("[");
        for(int r=0;r<7;r++) {
            Boundary b=kept.get(r);Set<PartSurface> expected=new HashSet<PartSurface>();for(int i:plan.groups.get(r))expected.add(ss.get(i));
            Collection<PartSurface> actual=b.getPartSurfaceGroup().getObjects();
            if(actual.size()!=expected.size()||!expected.equals(new HashSet<PartSurface>(actual))||!names.add(b.getPresentationName()))throw new IllegalStateException("Boundary identity/group mismatch");
            String type=r==6?"WallBoundary":"inlet".equals(roles[r])?"InletBoundary":"outlet".equals(roles[r])||(!legacy&&"farfield".equals(roles[r]))?"PressureBoundary":"SymmetryBoundary";
            if(!type.equals(b.getBoundaryType().getClass().getSimpleName()))throw new IllegalStateException("Actual boundary type mismatch");
            double area=0;StringBuilder members=new StringBuilder("[");
            for(PartSurface s:actual){if(!all.add(s))throw new IllegalStateException("Overlapping boundary membership");int i=ss.indexOf(s);if(i<0)throw new IllegalStateException("Foreign surface");area+=ds.get(i).area;if(members.length()>1)members.append(',');members.append(SurfaceMeasure.json(ds.get(i)));}members.append(']');
            if(!Geo.near(area,plan.areas[r],Geo.areaEps(area)))throw new IllegalStateException("Boundary area readback mismatch");
            if(r>0)json.append(',');json.append("{\"name\":").append(SurfaceMeasure.quote(b.getPresentationName())).append(",\"actual_type\":").append(SurfaceMeasure.quote(type))
                .append(",\"surface_count\":").append(actual.size()).append(",\"area_m2\":").append(area).append(",\"surfaces\":").append(members).append('}');
        }
        if(all.size()!=ss.size())throw new IllegalStateException("Incomplete actual boundary ownership");return json.append(']').toString();
    }
    static String partitionJson(Geo.Partition p,List<Geo.Data> ds) {
        StringBuilder b=new StringBuilder("\"classification\":\"exhaustive_source_triangle_coverage_and_box_planes\",\"measurement_format\":\"ASCII_STL_double_vertices\",\"removed_caps\":"+p.removedCaps+",\"retained_caps\":"+p.retainedCaps+",\"groups\":[");
        for(int r=0;r<7;r++){if(r>0)b.append(',');b.append("{\"face\":").append(SurfaceMeasure.quote(r==6?"body_wall":Geo.FACES[r])).append(",\"area_m2\":").append(p.areas[r]).append(",\"surfaces\":[");int n=0;for(int i:p.groups.get(r)){if(n++>0)b.append(',');b.append(SurfaceMeasure.json(ds.get(i)));}b.append("]}");}return b.append(']').toString();
    }
    static String[] resolveRoles(String[] declared) {
        if(declared==null)return new String[]{"inlet","outlet","farfield","farfield","farfield","farfield"};
        if(declared.length!=6)throw new IllegalArgumentException("Six box roles required");
        int in=0,out=0;for(String r:declared){if("inlet".equals(r))in++;else if("outlet".equals(r))out++;else if(!"farfield".equals(r)&&!"symmetry".equals(r))throw new IllegalArgumentException("Unknown role");}
        if(in!=1||out!=1)throw new IllegalArgumentException("Exactly one inlet and outlet required");return declared;
    }
    static String boundaryName(String role,int face,boolean legacy) {
        if("inlet".equals(role)||"outlet".equals(role))return "AUTO_"+role.toUpperCase(java.util.Locale.ROOT);
        return "AUTO_"+(legacy?"FARFIELD":role.toUpperCase(java.util.Locale.ROOT))+"_"+Geo.FACES[face].toUpperCase(java.util.Locale.ROOT);
    }
    static void validateAliases(String spec,String region,String wall) {
        String[] keys={"region","inlet","outlet","body_wall"};String[] values={region,"AUTO_INLET","AUTO_OUTLET",wall};
        for(int i=0;i<keys.length;i++)if(!optional(spec,keys[i],values[i]).equals(values[i]))throw new IllegalArgumentException("Unsupported boundary_roles alias: "+keys[i]);
    }
    static String optional(String json,String key,String fallback) {
        Matcher m=Pattern.compile("\\\""+Pattern.quote(key)+"\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(json);return m.find()?m.group(1):fallback;
    }
    static String[] stringArray(String json,String key) {
        Matcher m=Pattern.compile("\\\""+Pattern.quote(key)+"\\\"\\s*:\\s*\\[([^\\]]*)\\]").matcher(json);if(!m.find())return null;
        String s=m.group(1).trim();if(s.isEmpty())return new String[0];String[] a=s.split(",");
        for(int i=0;i<a.length;i++){a[i]=a[i].trim();if(!a[i].matches("\"[^\"\\\\]+\""))throw new IllegalArgumentException("String-only array: "+key);a[i]=a[i].substring(1,a[i].length()-1);}return a;
    }
    static double[] array(String json,String key,int n) {
        Matcher m=Pattern.compile("\\\""+Pattern.quote(key)+"\\\"\\s*:\\s*\\[([^\\]]*)\\]").matcher(json);if(!m.find())throw new IllegalArgumentException("Missing "+key);String[] a=m.group(1).split(",");if(a.length!=n)throw new IllegalArgumentException("Bad "+key);double[] d=new double[n];for(int i=0;i<n;i++){d[i]=Double.parseDouble(a[i]);if(!Double.isFinite(d[i]))throw new IllegalArgumentException("Nonfinite "+key);}return d;
    }
    static void write(File file,String json)throws Exception {Files.writeString(file.toPath(),json+"\n",StandardCharsets.UTF_8);}
    // END GENERATED REGION CONTRACT
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
            star.common.GeometryPart found=null;
            for(int i=0;i<queue.size();i++) {
                star.common.GeometryPart part=queue.get(i);if(!seen.add(part))continue;
                if(name.equals(part.getPresentationName())){if(found!=null&&found!=part)throw new IllegalStateException("Duplicate body name");found=part;}
                if(part instanceof star.common.CompositePart) {
                    queue.addAll(((star.common.CompositePart)part).getLeafParts());
                    queue.addAll(((star.common.CompositePart)part).getChildParts().getParts());
                }
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
