/* Recover blade material from explicitly selected fluid sectors; reject Boolean clutter. */
import java.io.*;
import java.nio.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.*;
import java.util.regex.*;
import star.base.neo.DoubleVector;
import star.base.neo.NeoObjectVector;
import star.common.*;
import star.meshing.*;

public class RecoverRotorSolidsFromSectorFluid extends StarMacro {
    private static final String COMPLETE="ROTOR_SOLID_RECOVERY_TRIAL_COMPLETE";
    @Override public void execute() {
        Simulation sim=getActiveSimulation();File dir=sim.getSessionDirFile();File record=new File(dir,"recovered_rotor_solids.json");
        try {
            String spec=Files.readString(new File(dir,"case_spec.json").toPath(),StandardCharsets.UTF_8);
            if(!"rotating_fluid_sector".equals(string(spec,"source_sector_role")))throw new IllegalArgumentException("Refusing material recovery from an undeclared fluid role");
            double radius=number(spec,"recovery_disk_radius_m"),zmin=number(spec,"recovery_disk_z_min_m"),zmax=number(spec,"recovery_disk_z_max_m");
            if(!(radius>0 && zmin<zmax))throw new IllegalArgumentException("Invalid recovery disk");
            // ②R18 localized the residue as the discrete polygon mismatch between this auxiliary
            // cylinder and the sector outer surface (78 lateral facets against 93): the faceted
            // envelope crossed outside the faceted fluid and left 6.420347208404547e-7 m3 of slivers.
            // ②R19 corrects the envelope's discrete representation instead of relaxing any gate:
            // the envelope must sit strictly inside the audited sector outer boundary in radius and
            // strictly inside the sector end caps in z, while still containing the blade material.
            double insideRadius=number(spec,"recovery_envelope_inside_fluid_radius_m"),insideZ=number(spec,"recovery_envelope_inside_fluid_z_m");
            int facetFloor=(int)number(spec,"recovery_envelope_lateral_facets_min");
            if(!(insideRadius>0 && insideZ>0 && facetFloor>=8))throw new IllegalArgumentException("Invalid envelope containment declarations");
            if(!(radius<insideRadius))throw new IllegalStateException("Recovery envelope is not strictly inside the audited sector outer boundary");
            if(!(Math.max(Math.abs(zmin),Math.abs(zmax))<insideZ))throw new IllegalStateException("Recovery envelope caps are not strictly inside the audited sector end caps");
            double containedRadius=radius*Math.cos(Math.PI/facetFloor);
            double envelopeHalfHeight=Math.min(Math.abs(zmin),Math.abs(zmax));
            // Independent-review finding C1: the containment ceiling is only as strong as the declared
            // facet floor. Make the proof's own hypotheses executable: refuse to run at all if the
            // declared material envelope is not strictly inside the ceiling the floor implies.
            double materialRadius=number(spec,"recovery_envelope_material_max_radius_m"),materialZ=number(spec,"recovery_envelope_material_max_abs_z_m");
            if(!(materialRadius>0 && materialZ>0))throw new IllegalArgumentException("Invalid declared material envelope bound");
            if(!(containedRadius>materialRadius))throw new IllegalStateException("Declared envelope facet floor puts the containment ceiling inside the declared material radius: "+containedRadius+" <= "+materialRadius);
            if(!(envelopeHalfHeight>materialZ))throw new IllegalStateException("Envelope half height does not exceed the declared material |z| bound: "+envelopeHalfHeight+" <= "+materialZ);
            double[] center=array(spec,"center_m",3),bounds=array(spec,"recovered_solid_bounds_m",6);
            double volumeMin=number(spec,"recovered_solid_volume_min_m3"),volumeMax=number(spec,"recovered_solid_volume_max_m3");
            double artifactRatioMax=number(spec,"recovery_artifact_volume_ratio_max");
            if(!(volumeMin>0 && volumeMax>volumeMin && artifactRatioMax>0 && artifactRatioMax<=1e-4))throw new IllegalArgumentException("Invalid explicit solid/recovery gates");
            String rawName=string(spec,"recovery_raw_part"),solidName=string(spec,"recovered_rotor_part");
            SimulationPartManager parts=sim.get(SimulationPartManager.class);
            List<String> actualPartNames=new ArrayList<>();
            for(GeometryPart actual:parts.getObjects())actualPartNames.add(actual.getPresentationName());
            if(outputExists(actualPartNames,rawName) || outputExists(actualPartNames,solidName))throw new IllegalStateException("Selected recovery output already exists");
            List<String> sourceNames=strings(spec,"sector_fluid_parts");
            if(sourceNames.isEmpty())throw new IllegalArgumentException("No selected fluid sectors");
            List<GeometryPart> sectorFluids=new ArrayList<>();
            for(String name:sourceNames){GeometryPart part=parts.getPart(name);if(!(part instanceof MeshPart) || !((MeshPart)part).isClosed() || !((MeshPart)part).isManifold())throw new IllegalStateException("Selected fluid-sector topology failed: "+name);sectorFluids.add(part);}
            SimpleCylinderPart disk=sim.get(MeshPartFactory.class).createNewCylinderPart(parts);
            disk.setPresentationName(string(spec,"recovery_disk_part"));
            LabCoordinateSystem lab=sim.getCoordinateSystemManager().getLabCoordinateSystem();Units meters=sim.getUnitsManager().getObject("m");
            disk.setCoordinateSystem(lab);disk.getStartCoordinate().setCoordinateSystem(lab);disk.getEndCoordinate().setCoordinateSystem(lab);
            disk.getStartCoordinate().setCoordinate(meters,meters,meters,new DoubleVector(new double[]{center[0],center[1],zmin}));
            disk.getEndCoordinate().setCoordinate(meters,meters,meters,new DoubleVector(new double[]{center[0],center[1],zmax}));
            disk.getRadius().setValueAndUnits(radius,meters);disk.rebuildSimpleShapePart();
            Set<GeometryPart> before=new HashSet<>(parts.getObjects());
            List<Object> inputs=new ArrayList<>();inputs.add(disk);inputs.addAll(sectorFluids);
            SubtractPartsOperation operation=(SubtractPartsOperation)sim.get(MeshOperationManager.class).createSubtractPartsOperation(new NeoObjectVector(inputs.toArray()));
            operation.setPresentationName("Recover rotor material from selected sector fluid");operation.setTargetPart(disk);
            // Transformed sectors are tessellated parts. The historical operation silently fell back
            // from CAD and left 72 nearly-zero-volume disk slivers, so use the discrete path explicitly.
            operation.setPerformCADBoolean(false);operation.execute();
            MeshOperationPart raw=null;
            for(GeometryPart p:parts.getObjects())if(!before.contains(p) && p instanceof MeshOperationPart){if(raw!=null)throw new IllegalStateException("Multiple Boolean recovery outputs");raw=(MeshOperationPart)p;}
            if(raw==null)throw new IllegalStateException("No recovery output");raw.setPresentationName(rawName);
            File rawStl=new File(dir,"recovered_rotor_raw.stl");raw.exportStlPart(rawStl.getAbsolutePath(),true);
            sim.saveState(new File(dir,"recovered_rotor_raw.sim").getAbsolutePath());
            byte[] bytes=Files.readAllBytes(rawStl.toPath());List<Shell> shells=shells(bytes);
            Shell selected=selectShell(shells,bounds,volumeMin,volumeMax,artifactRatioMax);
            double[] contained=requireInsideEnvelope(selected,containedRadius,envelopeHalfHeight);
            double artifactVolume=0;int discardedTriangles=0;
            for(Shell shell:shells)if(shell!=selected){artifactVolume+=Math.abs(shell.signedVolume);discardedTriangles+=shell.triangles.size();}
            double artifactRatio=artifactVolume/selected.signedVolume;
            File solidStl=new File(dir,"recovered_rotor_solids.stl");writeShell(bytes,selected,solidStl);
            Set<GeometryPart> importBefore=new HashSet<>(parts.getObjects());
            sim.get(PartImportManager.class).importStlParts(Collections.singletonList(solidStl.getAbsolutePath()),"OneSurfacePerPatch","OnePartPerFile",meters,true,1e-9,false,false);
            MeshPart solid=null;
            for(GeometryPart p:parts.getObjects())if(!importBefore.contains(p)){if(solid!=null || !(p instanceof MeshPart))throw new IllegalStateException("Recovery STL import did not yield one MeshPart");solid=(MeshPart)p;}
            if(solid==null || !solid.isClosed() || !solid.isManifold() || solid.getTriangleCount()==0)throw new IllegalStateException("STAR readback rejected the selected material-shell topology");
            solid.setPresentationName(solidName);
            File reread=new File(dir,"recovered_rotor_readback.stl");solid.exportStlPart(reread.getAbsolutePath(),true);
            List<Shell> rereadShells=shells(Files.readAllBytes(reread.toPath()));
            if(rereadShells.size()!=1 || rereadShells.get(0).boundaryEdges!=0 || rereadShells.get(0).nonmanifoldEdges!=0 || !(rereadShells.get(0).signedVolume>0))throw new IllegalStateException("Imported material topology/positive volume failed independent STL readback");
            sim.saveState(new File(dir,"recovered_rotor_solids.sim").getAbsolutePath());
            List<String> componentRecords=new ArrayList<>();
            for(Shell shell:shells)componentRecords.add(shell.json(shell==selected));
            String json="{\"schema_version\":2,\"status\":\"ok\",\"input_role\":\"rotating_fluid_sector\",\"source_parts\":"+jsonStrings(sourceNames)+
                ",\"raw_boolean_part\":\""+escape(rawName)+"\",\"raw_boolean_closed\":"+raw.isClosed()+",\"raw_boolean_manifold\":"+raw.isManifold()+
                ",\"output_part\":\""+escape(solidName)+"\",\"output_role\":\"rotor_solid\",\"selected_closed\":true,\"selected_manifold\":true,\"selected_surface_volume_m3\":"+selected.signedVolume+
                ",\"selected_bounds_m\":"+Arrays.toString(selected.bounds)+",\"selected_triangles\":"+selected.triangles.size()+",\"discarded_shells\":"+(shells.size()-1)+
                ",\"discarded_triangles\":"+discardedTriangles+",\"discarded_abs_volume_m3\":"+artifactVolume+",\"artifact_volume_ratio\":"+artifactRatio+",\"artifact_volume_ratio_max\":"+artifactRatioMax+
                ",\"raw_stl_sha256\":\""+sha(rawStl)+"\",\"selected_stl_sha256\":\""+sha(solidStl)+"\",\"readback_stl_sha256\":\""+sha(reread)+
                "\",\"envelope_containment\":{\"disk_radius_m\":"+radius+",\"disk_z_min_m\":"+zmin+",\"disk_z_max_m\":"+zmax+
                ",\"inside_fluid_radius_m\":"+insideRadius+",\"inside_fluid_z_m\":"+insideZ+",\"lateral_facets_min\":"+facetFloor+
                ",\"contained_radius_m\":"+containedRadius+",\"envelope_half_height_m\":"+envelopeHalfHeight+
                ",\"selected_max_radius_m\":"+contained[0]+",\"selected_max_abs_z_m\":"+contained[1]+
                ",\"no_envelope_facet_in_result\":true},"+
                "\"readback_surface_volume_m3\":"+rereadShells.get(0).signedVolume+",\"components\":["+String.join(",",componentRecords)+"],\"complete_marker\":\""+COMPLETE+"\"}\n";
            Files.writeString(record.toPath(),json,StandardCharsets.UTF_8);
            Files.writeString(new File(dir,"recovered_rotor_solids.txt").toPath(),"source_parts="+String.join(",",sourceNames)+"\noutput_part="+solidName+"\ntriangles="+solid.getTriangleCount()+"\nclosed="+solid.isClosed()+"\nmanifold="+solid.isManifold()+"\n"+COMPLETE+"\n",StandardCharsets.UTF_8);
            System.out.println(COMPLETE);
        } catch(Throwable failure){try{Files.writeString(record.toPath(),"{\"schema_version\":2,\"status\":\"error\",\"error\":\""+escape(failure.toString())+"\"}\n",StandardCharsets.UTF_8);}catch(Throwable ignored){}throw new RuntimeException("Rotor material recovery failed",failure);}
    }
    static boolean outputExists(Collection<String> actualPartNames,String selectedName){
        return actualPartNames.contains(selectedName);
    }
    static final class Shell {
        final List<Integer> triangles=new ArrayList<>();final List<double[]> vertices=new ArrayList<>();final double[]bounds={Double.POSITIVE_INFINITY,Double.POSITIVE_INFINITY,Double.POSITIVE_INFINITY,Double.NEGATIVE_INFINITY,Double.NEGATIVE_INFINITY,Double.NEGATIVE_INFINITY};
        double signedVolume;int boundaryEdges,nonmanifoldEdges;
        String json(boolean selected){return "{\"triangles\":"+triangles.size()+",\"signed_volume_m3\":"+signedVolume+",\"bounds_m\":"+Arrays.toString(bounds)+",\"boundary_edges\":"+boundaryEdges+",\"nonmanifold_edges\":"+nonmanifoldEdges+",\"selected\":"+selected+"}";}
    }
    // Connectivity is exact in the exported binary STL; no tolerance welding changes the geometry.
    static List<Shell> shells(byte[]bytes)throws IOException {
        if(bytes.length<84)throw new IOException("Short binary STL");ByteBuffer b=ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);int n=b.getInt(80);
        if(n<=0 || bytes.length!=84L+50L*n)throw new IOException("Invalid binary STL layout");
        int[]parent=new int[n];Map<String,List<Integer>>edges=new HashMap<>();double[][][]triangles=new double[n][3][3];String[][]vertices=new String[n][3];
        for(int i=0;i<n;i++){parent[i]=i;for(int j=0;j<3;j++){StringBuilder key=new StringBuilder();for(int a=0;a<3;a++){float f=b.getFloat(84+i*50+12+j*12+a*4);if(!Float.isFinite(f))throw new IOException("Nonfinite STL coordinate");triangles[i][j][a]=f;key.append(Float.floatToIntBits(f==0?0:f)).append('/');}vertices[i][j]=key.toString();}
            for(int j=0;j<3;j++){String a=vertices[i][j],c=vertices[i][(j+1)%3];String edge=a.compareTo(c)<0?a+":"+c:c+":"+a;edges.computeIfAbsent(edge,k->new ArrayList<>()).add(i);}}
        for(List<Integer>around:edges.values())for(int j=1;j<around.size();j++)parent[root(parent,around.get(j))]=root(parent,around.get(0));
        Map<Integer,Shell>components=new LinkedHashMap<>();
        for(int i=0;i<n;i++){Shell shell=components.computeIfAbsent(root(parent,i),k->new Shell());shell.triangles.add(i);double[][]v=triangles[i];for(double[]p:v){shell.vertices.add(p);for(int a=0;a<3;a++){shell.bounds[a]=Math.min(shell.bounds[a],p[a]);shell.bounds[a+3]=Math.max(shell.bounds[a+3],p[a]);}}double[]a=v[0],c=v[1],d=v[2];shell.signedVolume+=(a[0]*(c[1]*d[2]-c[2]*d[1])-a[1]*(c[0]*d[2]-c[2]*d[0])+a[2]*(c[0]*d[1]-c[1]*d[0]))/6;}
        for(List<Integer>around:edges.values()){Shell shell=components.get(root(parent,around.get(0)));if(around.size()==1)shell.boundaryEdges++;if(around.size()>2)shell.nonmanifoldEdges++;}
        return new ArrayList<>(components.values());
    }
    static Shell selectShell(List<Shell>shells,double[]bounds,double volumeMin,double volumeMax,double artifactRatioMax){
        Shell selected=null;double artifactVolume=0;
        for(Shell shell:shells){
            boolean inBounds=true;for(int a=0;a<3;a++)if(shell.bounds[a]<bounds[a] || shell.bounds[a+3]>bounds[a+3])inBounds=false;
            boolean candidate=inBounds && shell.signedVolume>=volumeMin && shell.signedVolume<=volumeMax && shell.boundaryEdges==0 && shell.nonmanifoldEdges==0;
            if(candidate){if(selected!=null)throw new IllegalStateException("Multiple material shells match the audited solid role");selected=shell;}
            else artifactVolume+=Math.abs(shell.signedVolume);
        }
        if(selected==null)throw new IllegalStateException("No closed, manifold material shell matches the audited solid bounds and volume");
        double ratio=artifactVolume/selected.signedVolume;
        if(!Double.isFinite(ratio) || ratio>artifactRatioMax)throw new IllegalStateException("Boolean residue exceeds explicit artifact-volume budget: "+ratio);
        return selected;
    }
    // ②R19 executable containment proof. Every point of the envelope's lateral facets lies at radius
    // >= radius*cos(pi/n) for the actual facet count n, and radius*cos(pi/n) >= radius*cos(pi/facetFloor)
    // whenever n >= facetFloor (cos(pi/n) grows with n). Every point of an envelope end cap lies at
    // |z| = envelopeHalfHeight. So a selected shell whose every vertex is strictly inside both bounds
    // contains no envelope facet at all: the material was fully contained by the envelope and its
    // surface is the sector fluid's own wall, not a surface this step fabricated.
    static double[] requireInsideEnvelope(Shell shell,double containedRadius,double envelopeHalfHeight){
        double maxRadius=0,maxAbsZ=0;
        for(double[]p:shell.vertices){double r=Math.sqrt(p[0]*p[0]+p[1]*p[1]);if(r>maxRadius)maxRadius=r;double az=Math.abs(p[2]);if(az>maxAbsZ)maxAbsZ=az;}
        if(!(maxRadius<containedRadius))throw new IllegalStateException("Selected material reaches the recovery-envelope lateral surface: max radius "+maxRadius+" >= contained radius "+containedRadius);
        if(!(maxAbsZ<envelopeHalfHeight))throw new IllegalStateException("Selected material reaches a recovery-envelope end cap: max |z| "+maxAbsZ+" >= "+envelopeHalfHeight);
        return new double[]{maxRadius,maxAbsZ};
    }
    private static int root(int[]p,int n){while(p[n]!=n){p[n]=p[p[n]];n=p[n];}return n;}
    private static void writeShell(byte[]raw,Shell shell,File file)throws IOException{ByteBuffer out=ByteBuffer.allocate(84+50*shell.triangles.size()).order(ByteOrder.LITTLE_ENDIAN);out.put(Arrays.copyOfRange(raw,0,80));out.putInt(shell.triangles.size());for(int i:shell.triangles)out.put(raw,84+50*i,50);Files.write(file.toPath(),out.array());}
    private static String sha(File f)throws Exception{return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(f.toPath())));}
    private static String string(String j,String k){Matcher m=Pattern.compile("\""+Pattern.quote(k)+"\"\\s*:\\s*\"([^\"]+)\"").matcher(j);if(!m.find())throw new IllegalArgumentException("Missing "+k);return m.group(1);}
    private static double number(String j,String k){Matcher m=Pattern.compile("\""+Pattern.quote(k)+"\"\\s*:\\s*([-+0-9.eE]+)").matcher(j);if(!m.find())throw new IllegalArgumentException("Missing "+k);double v=Double.parseDouble(m.group(1));if(!Double.isFinite(v))throw new IllegalArgumentException("Nonfinite "+k);return v;}
    private static List<String>strings(String j,String k){Matcher m=Pattern.compile("\""+Pattern.quote(k)+"\"\\s*:\\s*\\[([^\\]]*)\\]").matcher(j);if(!m.find())throw new IllegalArgumentException("Missing "+k);List<String>out=new ArrayList<>();Matcher t=Pattern.compile("\"([^\"]+)\"").matcher(m.group(1));while(t.find())out.add(t.group(1));return out;}
    private static double[]array(String j,String k,int n){Matcher m=Pattern.compile("\""+Pattern.quote(k)+"\"\\s*:\\s*\\[([^\\]]+)\\]").matcher(j);if(!m.find())throw new IllegalArgumentException("Missing "+k);String[]words=m.group(1).split(",");if(words.length!=n)throw new IllegalArgumentException("Wrong vector length "+k);double[]v=new double[n];for(int i=0;i<n;i++){v[i]=Double.parseDouble(words[i].trim());if(!Double.isFinite(v[i]))throw new IllegalArgumentException("Nonfinite "+k);}return v;}
    private static String jsonStrings(List<String>v){List<String>a=new ArrayList<>();for(String s:v)a.add("\""+escape(s)+"\"");return "["+String.join(",",a)+"]";}
    private static String escape(String s){StringBuilder out=new StringBuilder();for(char c:s.toCharArray()){if(c=='\\')out.append("\\\\");else if(c=='"')out.append("\\\"");else if(c<0x20)out.append(String.format("\\u%04x",(int)c));else out.append(c);}return out.toString();}
}
