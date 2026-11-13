/* Limited diagnostic trial: fresh successful configuration readback is required.
 * No solver initialization, solver iterations, cell removal or quality relaxation.
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.*;
import star.common.*;
import star.meshing.*;

public class GenerateAuditedLocalMesh extends StarMacro {
    @Override public void execute() {
        Simulation sim=getActiveSimulation();File dir=sim.getSessionDirFile();
        try {
            File audit=new File(dir,"local_mesh_readback.tsv");
            if(!audit.isFile() || !Files.readString(audit.toPath(),StandardCharsets.UTF_8).contains("LOCAL_MESH_READBACK_COMPLETE"))
                throw new IllegalStateException("Fresh complete readback required before meshing");
            File output=new File(dir,"local_mesh_generated.sim");
            if(output.exists())throw new IllegalStateException("Refusing to overwrite output");
            Properties p=new Properties();
            try(Reader in=Files.newBufferedReader(new File(dir,"local_mesh.properties").toPath(),StandardCharsets.UTF_8)){p.load(in);}
            Set<String> pairs=new HashSet<>();
            for(int i=0;i<Integer.parseInt(p.getProperty("surface_count"));i++)
                if("interface".equals(p.getProperty("surface."+i+".role")))pairs.add(p.getProperty("surface."+i+".pair"));
            if(pairs.contains(null) || pairs.isEmpty() || sim.getInterfaceManager().getObjects().size()!=pairs.size() || sim.getRegionManager().getRegions().size()!=2)
                throw new IllegalStateException("Mapped fluid region/interface inventory changed");
            AutoMeshOperation op=(AutoMeshOperation)sim.get(MeshOperationManager.class).getObject("AUDITED_LOCAL_MESH");
            if(op==null)throw new IllegalStateException("Operation missing");
            op.execute();
            FvRepresentation mesh=(FvRepresentation)sim.getRepresentationManager().getObject("Volume Mesh");
            if(mesh==null || mesh.getCellCount()<=0)throw new IllegalStateException("No volume cells");
            for(Region region:sim.getRegionManager().getRegions())
                if(!mesh.describesRegion(region))throw new IllegalStateException("Region lacks volume cells: "+region.getPresentationName());
            sim.saveState(output.getAbsolutePath());
            try(Writer out=Files.newBufferedWriter(new File(dir,"local_mesh_generated.json").toPath(),StandardCharsets.UTF_8)) {
                out.write("{\"schema_version\":1,\"status\":\"GENERATED_PENDING_QUALITY\",\"volume_cells\":"+mesh.getCellCount()+
                          ",\"regions\":2,\"interfaces\":"+pairs.size()+",\"engineering_verified\":false,\"initialized\":false}\n");
            }
            System.out.println("AUDITED_LOCAL_MESH_GENERATED");
        }catch(Exception e){throw new RuntimeException("Audited local mesh generation failed",e);}
    }
}
