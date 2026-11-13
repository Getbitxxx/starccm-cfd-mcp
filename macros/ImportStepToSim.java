/* Geometry-only STEP import diagnostic. Measures the imported part, native CAD topology and the
 * PartSurface/patch map BEFORE gating; writes the complete diagnostic record even when the gate
 * fails; saves a diagnostic .sim always and the import .sim only on a passing gate.
 * No domain, region, physics, mesh or initialization. */
import java.io.*;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import java.util.regex.*;
import star.common.*;
import star.meshing.*;
import star.cadmodeler.*;

public class ImportStepToSim extends StarMacro {
    @Override public void execute() {
        Simulation sim=getActiveSimulation();File dir=new File(System.getenv("ROLE_JOB_DIR"));
        try {
            String bodyName="";
            File specFile=new File(dir,"case_spec.json");String spec=Files.readString(specFile.toPath());
            Matcher m=Pattern.compile("\"body_part_name\"\\s*:\\s*\"([A-Za-z0-9_-]+)\"").matcher(spec);
            if(!m.find())throw new IllegalArgumentException("Safe body_part_name required");bodyName=m.group(1);
            if(sim.isInitialized()||sim.getSimulationIterator().getCurrentIteration()!=0||!sim.getRegionManager().getObjects().isEmpty()
                    ||!sim.getContinuumManager().getObjects().isEmpty()||!sim.get(SimulationPartManager.class).getParts().isEmpty())
                throw new IllegalStateException("New empty session required");
            File step=new File(dir,bodyName+".step"),diagnostic=new File(dir,bodyName+"_diagnostic.sim"),imported=new File(dir,bodyName+"_import.sim");
            if(diagnostic.exists()||imported.exists())throw new IllegalStateException("Refuse checkpoint replacement");
            String stepSha=sha(step),specSha=sha(specFile);
            if(!stepSha.equals(System.getenv("ROLE_STEP_SHA256"))||!specSha.equals(System.getenv("ROLE_SPEC_SHA256")))throw new IllegalStateException("Reviewed input hash mismatch");
            String stepText=Files.readString(step.toPath());int expectedFaces=0;
            Matcher faces=Pattern.compile("=ADVANCED_FACE\\(").matcher(stepText);while(faces.find())expectedFaces++;
            if(expectedFaces<=0)throw new IllegalStateException("STEP has no ADVANCED_FACE");
            sim.get(PartImportManager.class).importCadParts(Collections.singletonList(step.getAbsolutePath()),"default",30.0,1,false,1.0e-5,false,false,false,false);
            Collection<GeometryPart> leaves=sim.get(SimulationPartManager.class).getLeafParts();
            if(leaves.size()!=1||!(leaves.iterator().next() instanceof MeshPart))throw new IllegalStateException("One imported solid leaf required");
            MeshPart part=(MeshPart)leaves.iterator().next();
            if(!bodyName.equals(part.getPresentationName()))
                throw new IllegalStateException("Imported name differs: "+part.getPresentationName()+", expected "+bodyName);
            String partClass=part.getClass().getName();
            int partSurfacesBeforeSplit=part.getPartSurfaces().size();
            part.getPartSurfaceManager().splitPartSurfacesByAngle(new star.base.neo.NeoObjectVector(part.getPartSurfaces().toArray()), 89.0);
            int partSurfaces=part.getPartSurfaces().size();
            int patchCount=0;StringBuilder names=new StringBuilder("["),patchIds=new StringBuilder("["),patchCounts=new StringBuilder("[");
            for(PartSurface s: part.getPartSurfaces()) {
                List<Integer> patches=part.getPartSurfacePatches(s);
                int n=patches==null?0:patches.size();
                patchCount+=n;
                if(names.length()>1){names.append(',');patchIds.append(',');patchCounts.append(',');}
                names.append(quote(s.getPresentationName()));
                patchIds.append('[');
                if(patches!=null)for(int k=0;k<patches.size();k++){if(k>0)patchIds.append(',');patchIds.append(patches.get(k));}
                patchIds.append(']');
                patchCounts.append(n);
            }
            names.append(']');patchIds.append(']');patchCounts.append(']');
            int triangles=part.getTriangleCount();
            String mapping=mappingClass(partSurfaces,patchCount,expectedFaces);
            int nativeModelCount=0,nativeBodies=0,nativeFaces=0;boolean nativeAvailable=false;String nativeError=null;
            try {
                SolidModelManager cadManager=sim.get(SolidModelManager.class);
                for(CadModelBase base: cadManager.getObjects()) {
                    nativeModelCount++;
                    if(!(base instanceof CadModel))throw new IllegalStateException("Unexpected CAD model type: "+base.getClass().getName());
                    CadModel model=(CadModel)base;
                    cadManager.editCadModel(model);
                    try {
                        for(Body body: model.getBodyManager().getBodies()) {
                            if(!body.isBrepBody()||!body.isSolid()||body.isMeshBody())
                                throw new IllegalStateException("Saved CAD body is not solid BREP");
                            nativeBodies++;
                            nativeFaces+=body.getFaceManager().getObjects().size();
                        }
                    } finally { cadManager.endEditCadModel(model); }
                }
                nativeAvailable=nativeModelCount>0;
            } catch(Throwable nativeFailure) {
                nativeError=nativeFailure.toString();
                nativeAvailable=false;
            }
            String gateReason="";
            String mappingBasis="";
            boolean gate=part.isClosed()&&part.isManifold();
            if(!gate)gateReason="part is not closed or not manifold";
            if(gate&&nativeAvailable&&nativeModelCount!=1){gate=false;gateReason="native model count="+nativeModelCount+", expected 1";}
            if(gate) {
                try { mappingBasis=requireImportMapping(partClass,nativeAvailable,nativeBodies,nativeFaces,expectedFaces,partSurfaces,patchCount); }
                catch(Throwable gateFailure) { gate=false;gateReason=String.valueOf(gateFailure.getMessage()); }
            }
            Matcher u=Pattern.compile("\"model_length_unit\"\\s*:\\s*\"([^\"]+)\"").matcher(spec);
            if(!u.find())throw new IllegalArgumentException("model_length_unit required");
            Units units=sim.getUnitsManager().getObject(u.group(1));
            if(units==null)throw new IllegalArgumentException("Unknown length unit: "+u.group(1));
            Units old=sim.getUnitsManager().getPreferredUnits(Dimensions.Builder().length(1).build());
            if(old!=null&&!old.equals(units))old.setPreferred(false);units.setPreferred(true);
            // The diagnostic .sim is written before the gate decision is used by any consumer; it
            // lets the next stage inspect the imported part even when the mapping gate rejects it.
            sim.saveState(diagnostic.getAbsolutePath());
            String importSha="null";
            if(gate) { sim.saveState(imported.getAbsolutePath()); importSha=quote(sha(imported)); }
            String status=gate?"ok":"gate_failed";
            String marker=gate?"STEP_IMPORT_COMPLETE":"STEP_IMPORT_DIAGNOSTIC_COMPLETE";
            write(new File(dir,"import.json"),"{\"status\":"+quote(status)+",\"gate\":"+gate+",\"gate_reason\":"+quote(gateReason)
                    +",\"mapping_class\":"+quote(mapping)+",\"step_sha256\":"+quote(stepSha)+",\"casespec_sha256\":"+quote(specSha)
                    +",\"part\":"+quote(part.getPresentationName())+",\"part_class\":"+quote(partClass)+",\"solid_count\":1"
                    +",\"is_closed\":"+part.isClosed()+",\"is_manifold\":"+part.isManifold()
                    +",\"part_surface_count\":"+partSurfaces+",\"part_surfaces_before_split\":"+partSurfacesBeforeSplit
                    +",\"mapping_confidence\":\"SURFACE_SPLIT_COUNT_LEVEL\",\"patch_count\":"+patchCount
                    +",\"native_model_count\":"+nativeModelCount+",\"native_body_count\":"+nativeBodies
                    +",\"native_face_count\":"+nativeFaces+",\"native_count_route_available\":"+nativeAvailable
                    +",\"native_count_error\":"+(nativeError==null?"null":quote(nativeError))
                    +",\"native_mapping_basis\":"+quote(mappingBasis)
                    +",\"expected_step_faces\":"+expectedFaces+",\"triangle_count\":"+triangles
                    +",\"surface_names\":"+names+",\"surface_patch_ids\":"+patchIds+",\"surface_patch_counts\":"+patchCounts
                    +",\"diagnostic_sim_sha256\":"+quote(sha(diagnostic))+",\"output_sim_sha256\":"+importSha
                    +",\"complete_marker\":"+quote(marker)+"}");
            System.out.println(marker);
        }catch(Throwable e){try{write(new File(dir,"import.json"),"{\"status\":\"error\",\"error\":"+quote(e.toString())+"}");}catch(Exception x){e.addSuppressed(x);}throw new RuntimeException("STEP import failed",e);}
    }
    static String mappingClass(int partSurfaces,int patchCount,int expectedFaces) {
        if(partSurfaces==1&&patchCount==expectedFaces)return "one_group_expected_faces";
        if(partSurfaces==1&&patchCount==1)return "one_group_one_patch";
        if(partSurfaces>1&&patchCount==expectedFaces)return "multi_group_expected_faces";
        if(partSurfaces>1&&patchCount>expectedFaces)return "multi_group_split_patches";
        return "unexpected_grouping";
    }
    /* Native CAD counting is a cross-check, not a replacement for the patch map: the E387 import
     * uses PartImportManager.importCadParts, and the SolidModelManager route is not yet proven for
     * that path. When the native route is unavailable, the patch map must still cover every STEP
     * face. When it is available, native bodies/faces must equal one/expectedFaces. */
    static String requireImportMapping(String partClass,boolean nativeAvailable,int nativeBodies,int nativeFaces,int expectedFaces,int partSurfaces,int patchCount) {
        String basis;
        if(nativeAvailable) {
            if(nativeBodies!=1)throw new IllegalStateException("Native CAD body count="+nativeBodies+", expected 1");
            if(nativeFaces!=expectedFaces)throw new IllegalStateException("Native CAD face count="+nativeFaces+", expected "+expectedFaces);
            basis="NATIVE_AND_PATCH_MAP";
        } else if(partClass!=null && partClass.endsWith("CadPart")) {
            basis="PATCH_MAP_ONLY_CADPART";
        } else {
            throw new IllegalStateException("Native CAD count route unavailable and part class is not CadPart: "+partClass);
        }
        if(partSurfaces<1)throw new IllegalStateException("Imported part has no PartSurface");
        if(patchCount<expectedFaces)throw new IllegalStateException("Patch count="+patchCount+", expected at least "+expectedFaces);
        return basis;
    }
    static String sha(File f)throws Exception {byte[] digest=MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(f.toPath()));StringBuilder s=new StringBuilder();for(byte b:digest)s.append(String.format("%02x",b&255));return s.toString();}
    static String quote(String s){StringBuilder b=new StringBuilder("\"");for(char c:s.toCharArray()){if(c=='"'||c=='\\')b.append('\\').append(c);else if(c<32)b.append(String.format("\\u%04x",(int)c));else b.append(c);}return b.append('"').toString();}
    static void write(File f,String s)throws Exception{Files.writeString(f.toPath(),s+"\n",StandardCharsets.UTF_8);}
}
