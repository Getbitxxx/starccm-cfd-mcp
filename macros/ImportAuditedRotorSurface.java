/* Fixed scoped candidate: verified binary STL into an empty 2502 simulation.
 * Imports geometry only, never creates regions/mesh/physics or initializes/iterates.
 */
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.*;
import star.common.*;
import star.meshing.*;

public class ImportAuditedRotorSurface extends StarMacro {
    private static String sha(File f)throws Exception {
        MessageDigest d=MessageDigest.getInstance("SHA-256");
        try(InputStream in=new FileInputStream(f)){byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1)d.update(b,0,n);}
        return HexFormat.of().formatHex(d.digest());
    }
    @Override public void execute(){
        Simulation s=getActiveSimulation();
        try{
            String path=System.getenv("AIR3B_JOB_DIR");
            if(path==null || !path.matches("C:\\\\CodexCFDRestricted\\\\[a-z0-9-]{8,64}"))throw new IllegalStateException("Invalid isolated job");
            File dir=new File(path);File input=new File(dir,"rotor_mm.stl");File output=new File(dir,"geometry.sim");
            String expected=System.getenv("AIR3B_STL_SHA256");
            if(expected==null || !expected.matches("[a-f0-9]{64}") || !sha(input).equals(expected))throw new IllegalStateException("STL hash mismatch");
            SimulationPartManager parts=s.get(SimulationPartManager.class);
            if(!parts.getParts().isEmpty() || !s.getRegionManager().getRegions().isEmpty() || s.getSimulationIterator().getCurrentIteration()!=0 || output.exists())throw new IllegalStateException("Requires new empty simulation and unused output");
            s.get(PartImportManager.class).importStlParts(Collections.singletonList(input.getAbsolutePath()),
                "OneSurfacePerPatch","OnePartPerFile",s.getUnitsManager().getObject("mm"),true,1e-6,false,false);
            if(parts.getParts().size()!=1)throw new IllegalStateException("Expected single solid surface part");
            GeometryPart p=parts.getParts().iterator().next();
            if(!(p instanceof MeshPart))throw new IllegalStateException("Imported part is not MeshPart");
            MeshPart mesh=(MeshPart)p;
            if(!mesh.isClosed() || !mesh.isManifold() || mesh.getTriangleCount()==0)throw new IllegalStateException("Imported topology failed");
            p.setPresentationName("AIR3B_ROTOR");
            if(!sha(input).equals(expected))throw new IllegalStateException("Input changed");
            s.saveState(output.getAbsolutePath());
            try(PrintWriter out=new PrintWriter(new File(dir,"geometry_import.tsv"),StandardCharsets.UTF_8)){
                out.println("PART\t"+p.getPresentationName()+"\t"+p.getClass().getName());
                out.println("CLOSED\t"+mesh.isClosed());out.println("MANIFOLD\t"+mesh.isManifold());
                out.println("TRIANGLES\t"+mesh.getTriangleCount());out.println("STL_SHA256\t"+expected);
                out.println("OUTPUT_SHA256\t"+sha(output));out.println("REGIONS\t"+s.getRegionManager().getRegions().size());
                out.println("ITERATION\t"+s.getSimulationIterator().getCurrentIteration());
                out.println("ROTOR_GEOMETRY_IMPORT_COMPLETE");
                if(out.checkError())throw new IOException("Output write failed");
            }
            System.out.println("ROTOR_GEOMETRY_IMPORT_COMPLETE");
        }catch(Exception e){throw new RuntimeException("Rotor surface import failed: "+e.getMessage(),e);}
    }
}
