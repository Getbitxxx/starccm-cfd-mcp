/* Test fixture only: rename objects without changing geometry or physics. */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.*;
import star.base.neo.*;
import star.common.*;
import star.meshing.*;

public class RenameLocalMeshFixture extends StarMacro {
    @Override public void execute() {
        Simulation s=getActiveSimulation();File dir=s.getSessionDirFile();
        try {
            if(new File(dir,"local_mesh_renamed.sim").exists())throw new IllegalStateException("Fixture output exists");
            for(Object op:s.get(MeshOperationManager.class).getObjects())if(op instanceof AutoMeshOperation)throw new IllegalStateException("Geometry-only fixture required");
            Properties p=new Properties();
            try(Reader r=Files.newBufferedReader(new File(dir,"local_mesh.properties").toPath(),StandardCharsets.UTF_8)){p.load(r);}
            Map<String,ClientServerObject> objects=new HashMap<>();
            List<GeometryPart> parts=new ArrayList<>();
            for(int i=0;i<Integer.parseInt(p.getProperty("part_count"));i++) {
                GeometryPart part=s.get(SimulationPartManager.class).getPart(p.getProperty("part."+i));
                if(part==null)throw new IllegalStateException("Fixture part missing");
                objects.put(part.getQualifiedName(),part);parts.add(part);
                for(PartSurface face:part.getPartSurfaces())objects.put(face.getQualifiedName(),face);
                for(PartCurve curve:part.getPartCurves())objects.put(curve.getQualifiedName(),curve);
            }
            for(int i=0;i<parts.size();i++) {
                GeometryPart part=parts.get(i);int j=0;
                for(PartSurface face:part.getPartSurfaces())face.setPresentationName("Patch_"+i+"_"+(j++));
                j=0;for(PartCurve curve:part.getPartCurves())curve.setPresentationName("Edge_"+i+"_"+(j++));
                part.setPresentationName("Domain_"+i);
            }
            int i=0;
            for(Region r:s.getRegionManager().getRegions()) {
                int j=0;for(Boundary b:r.getBoundaryManager().getBoundaries())b.setPresentationName("Boundary_"+i+"_"+(j++));
                r.setPresentationName("Region_"+(i++));
            }
            i=0;for(Interface inter:s.getInterfaceManager().getObjects())inter.setPresentationName("Connection_"+(i++));
            try(PrintWriter out=new PrintWriter(new File(dir,"local_mesh_rename_map.tsv"),StandardCharsets.UTF_8)) {
                for(String original:new TreeSet<>(objects.keySet()))out.println(original+"\t"+objects.get(original).getQualifiedName());
            }
            s.saveState(new File(dir,"local_mesh_renamed.sim").getAbsolutePath());
            System.out.println("LOCAL_MESH_FIXTURE_RENAMED");
        }catch(Exception e){throw new RuntimeException("Rename fixture failed: "+e.getMessage(),e);}
    }
}
