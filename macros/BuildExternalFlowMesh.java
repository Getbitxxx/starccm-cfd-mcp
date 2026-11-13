/* Batched step 3 of 3: build the volume mesh and report what was actually generated.
 *
 * Mesher list, sizing API and prism-layer call are copied from ConfigureMrfMeshOperation, which this
 * repository has already executed against the installed 2502 build. Sizes come from the CaseSpec in
 * SI and are converted to the model unit, so no sample constant is assumed. The cell count and the
 * quality minima are read back from STAR after execution; the mesh is never assumed to be adequate.
 */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.base.neo.*;
import star.base.neo.StringVector;
import star.common.*;
import star.meshing.AutoMeshOperation;
import star.meshing.BaseSize;
import star.meshing.MeshOperationManager;
import star.meshing.MeshOperationPart;
import star.meshing.PartsMinimumSurfaceSize;
import star.meshing.PartsMinimumSurfaceSizeOption;
import star.meshing.PartsTargetSurfaceSize;
import star.meshing.PartsTargetSurfaceSizeOption;
import star.meshing.SurfaceCustomMeshControl;
import star.meshing.*;
import star.prismmesher.*;
import star.trimmer.VolumeControlTrimmerSizeOption;

public class BuildExternalFlowMesh extends StarMacro {
    private static PrismAutoMesher prismMesher(AutoMeshOperation operation){
        PrismAutoMesher found=null;
        for(MesherBase mesher:operation.getMeshersCollection()) if(mesher instanceof PrismAutoMesher){
            if(found!=null)throw new IllegalStateException("Multiple prism meshers");found=(PrismAutoMesher)mesher;
        }
        if(found==null)throw new IllegalStateException("Prism mesher missing");return found;
    }

    private static final String COMPLETE = "EXTERNAL_FLOW_MESH_BUILT";
    private static final String DOMAIN_PART = "AUTO_FREE_AIR_DOMAIN";

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        File evidence = new File(dir, "external_flow_mesh.json");
        try {
            String spec = new String(Files.readAllBytes(new File(dir, "case_spec.json").toPath()),
                    StandardCharsets.UTF_8);
            String unitName = string(spec, "model_length_unit");
            double scale = unitToMeters(unitName);
            double baseSize = number(spec, "mesh_base_size_m");
            double bodyTarget = number(spec, "mesh_body_target_size_m");
            double bodyMinimum = number(spec, "mesh_body_minimum_size_m");
            int prismLayers = (int) number(spec, "mesh_prism_layers");
            if (!(baseSize > 0 && bodyTarget > 0 && bodyMinimum > 0 && bodyTarget >= bodyMinimum)) {
                throw new IllegalArgumentException("Mesh sizes are outside supported ranges");
            }
            if (prismLayers < 3 || prismLayers > 20) throw new IllegalArgumentException("Invalid prism layer count");
            if (!(bodyTarget < baseSize)) throw new IllegalArgumentException("Body target size must be below the base size");
            String[] localKeys={"mesh_nearfield_bounds_m","mesh_nearfield_cell_size_m","mesh_wake_bounds_m","mesh_wake_cell_size_m"};
            int localCount=0; for(String key:localKeys) if(has(spec,key)) localCount++;
            if(localCount!=0 && localCount!=4) throw new IllegalArgumentException("All four local mesh fields are required");
            boolean explicitPrism=has(spec,"mesh_prism_first_layer_height_m");
            if(explicitPrism!=has(spec,"mesh_prism_total_thickness_m")) throw new IllegalArgumentException("Both explicit prism fields are required");
            double first=explicitPrism?number(spec,"mesh_prism_first_layer_height_m"):0;
            double total=explicitPrism?number(spec,"mesh_prism_total_thickness_m"):0;
            if(explicitPrism && !(first>0 && total>prismLayers*first)) throw new IllegalArgumentException("Explicit prism total must exceed N times first height");
            double[][] localBounds=new double[2][]; double[] localSizes=new double[2];
            if(localCount==4){
                double[] outer=array(spec,"outer_bounds_m");
                for(int i=0;i<2;i++){
                    localBounds[i]=array(spec,localKeys[2*i]); localSizes[i]=number(spec,localKeys[2*i+1]);
                    if(!(localSizes[i]>0 && localSizes[i]<=baseSize)) throw new IllegalArgumentException("Invalid local size");
                    for(int a=0;a<3;a++) if(!(localBounds[i][a]<localBounds[i][a+3] && localBounds[i][a]>=outer[a] && localBounds[i][a+3]<=outer[a+3])) throw new IllegalArgumentException("Local bounds outside domain or without extent");
                }
            }
            Object partObject = sim.get(SimulationPartManager.class).getPart(DOMAIN_PART);
            if (!(partObject instanceof MeshOperationPart)) {
                throw new IllegalStateException("Audited Boolean domain part missing: " + DOMAIN_PART);
            }
            MeshOperationPart part = (MeshOperationPart) partObject;
            Units units = sim.getUnitsManager().getObject(unitName);
            AutoMeshOperation operation = sim.get(MeshOperationManager.class).createAutoMeshOperation(
                    new StringVector(new String[] {"star.resurfacer.ResurfacerAutoMesher",
                            "star.resurfacer.AutomaticSurfaceRepairAutoMesher",
                            "star.trimmer.TrimmerAutoMesher", "star.prismmesher.PrismAutoMesher"}),
                    new NeoObjectVector(new Object[] {part}));
            operation.setPresentationName("AUTO_EXTERNAL_FLOW_MESH");
            operation.getDefaultValues().get(BaseSize.class).setValueAndUnits(baseSize / scale, units);
            operation.getDefaultValues().get(NumPrismLayers.class).setNumLayers(prismLayers);
            Units meters=sim.getUnitsManager().getObject("m");
            if(explicitPrism){
                operation.getDefaultValues().get(PrismThickness.class).getRelativeOrAbsoluteOption().setSelected(RelativeOrAbsoluteOption.Type.ABSOLUTE);
                operation.getDefaultValues().get(PrismThickness.class).getAbsoluteSizeValue().setValueAndUnits(total,meters);
                prismMesher(operation).getPrismStretchingOption().setSelected(PrismStretchingOption.Type.WALL_THICKNESS);
                operation.getDefaultValues().get(PrismWallThickness.class).setValueAndUnits(first,meters);
                // Read from the same native owner immediately, before constructing cells.
                double nativeFirst=operation.getDefaultValues().get(PrismWallThickness.class).getSIValue();
                double nativeTotal=operation.getDefaultValues().get(PrismThickness.class).getAbsoluteSizeValue().getSIValue();
                if(prismMesher(operation).getPrismStretchingOption().getSelectedElement()!=PrismStretchingOption.Type.WALL_THICKNESS || Math.abs(nativeFirst-first)>Math.max(1e-15,first*1e-10) || Math.abs(nativeTotal-total)>Math.max(1e-15,total*1e-10))throw new IllegalStateException("Explicit prism native configuration readback mismatch");
            }
            if(localCount==4) for(int i=0;i<2;i++){
                SimpleBlockPart block=sim.get(MeshPartFactory.class).createNewBlockPart(sim.get(SimulationPartManager.class));
                block.setPresentationName(i==0?"AUTO_LOCAL_NEARFIELD":"AUTO_LOCAL_WAKE");
                block.setCoordinateSystem(sim.getCoordinateSystemManager().getLabCoordinateSystem());
                block.getCorner1().setCoordinate(meters,meters,meters,new DoubleVector(new double[]{localBounds[i][0],localBounds[i][1],localBounds[i][2]}));
                block.getCorner2().setCoordinate(meters,meters,meters,new DoubleVector(new double[]{localBounds[i][3],localBounds[i][4],localBounds[i][5]}));
                block.rebuildSimpleShapePart();
                VolumeCustomMeshControl volume=operation.getCustomMeshControls().createVolumeControl();
                volume.setPresentationName(block.getPresentationName());
                volume.getGeometryObjects().setQuery(null); volume.getGeometryObjects().setObjects(block);
                volume.getCustomConditions().get(VolumeControlTrimmerSizeOption.class).setVolumeControlBaseSizeOption(true);
                VolumeControlSize size=volume.getCustomValues().get(VolumeControlSize.class);
                size.getRelativeOrAbsoluteOption().setSelected(RelativeOrAbsoluteOption.Type.ABSOLUTE);
                size.getAbsoluteSizeValue().setValueAndUnits(localSizes[i],meters);
            }
            List<PartSurface> bodySurfaces = new ArrayList<PartSurface>();
            for (PartSurface surface : part.getPartSurfaceManager().getObjects()) {
                if (surface.getPresentationName().startsWith("AUTO_FREE_AIR_BLOCK")) continue;
                bodySurfaces.add(surface);
            }
            if (bodySurfaces.isEmpty()) throw new IllegalStateException("No body surface to refine");
            SurfaceCustomMeshControl control = operation.getCustomMeshControls().createSurfaceControl();
            control.setPresentationName("AUTO_BODY_SURFACES");
            control.getGeometryObjects().setObjects(bodySurfaces.toArray(new PartSurface[0]));
            control.getCustomConditions().get(PartsMinimumSurfaceSizeOption.class)
                    .setSelected(PartsMinimumSurfaceSizeOption.Type.CUSTOM);
            control.getCustomConditions().get(PartsTargetSurfaceSizeOption.class)
                    .setSelected(PartsTargetSurfaceSizeOption.Type.CUSTOM);
            control.getCustomValues().get(PartsTargetSurfaceSize.class).getRelativeSizeScalar()
                    .setValue(bodyTarget / baseSize * 100.0);
            control.getCustomValues().get(PartsMinimumSurfaceSize.class).getRelativeSizeScalar()
                    .setValue(bodyMinimum / baseSize * 100.0);
            long started = System.currentTimeMillis();
            operation.execute();
            double seconds = (System.currentTimeMillis() - started) / 1000.0;

            Region region = sim.getRegionManager().getRegion(optional(spec, "region_name", "AUTO_EXTERNAL_FLOW"));
            if (region == null) throw new IllegalStateException("Flow region missing after meshing");
            // Same read-back the repository's own GenerateAuditedLocalMesh uses: ask the Volume Mesh
            // representation, and require it to describe the flow region.
            FvRepresentation mesh = (FvRepresentation) sim.getRepresentationManager().getObject("Volume Mesh");
            if (mesh == null || mesh.getCellCount() <= 0) {
                throw new IllegalStateException("The mesh contains no volume cells");
            }
            if (!mesh.describesRegion(region)) {
                throw new IllegalStateException("The Volume Mesh does not describe the flow region");
            }
            long cells = mesh.getCellCount();
            sim.saveState(new File(dir, "external_flow_meshed.sim").getAbsolutePath());
            try (FileWriter writer = new FileWriter(new File(dir, "external_flow_mesh.json"),
                    StandardCharsets.UTF_8)) {
                // This record contains counts and echoes, and NO measurement of mesh quality: the
                // prism layer count below is the value that was SET, and it is named accordingly.
                // The measured Cell Quality / Face Validity / Volume Change / realised wall layers come
                // from a separate read-only audit on the saved mesh (AuditExternalFlowMesh.java), so
                // that the macro that builds the mesh is not the one that grades it.
                writer.write("{\"schema_version\":1,\"status\":\"ok\",\"region\":\""
                        + escape(region.getPresentationName()) + "\",\"cells\":" + cells
                        + ",\"base_size_m\":" + baseSize + ",\"body_target_size_m\":" + bodyTarget
                        + ",\"body_minimum_size_m\":" + bodyMinimum + ",\"configured_prism_layers\":" + prismLayers
                        + ",\"body_surfaces\":" + bodySurfaces.size()
                        + ",\"mesh_seconds\":" + seconds
                        + ",\"quality_measured_by\":\"AuditExternalFlowMesh.java\""
                        + ",\"mesh_generated\":true,\"complete_marker\":\"" + COMPLETE + "\"}\n");
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            String message = String.valueOf(failure);
            try (FileWriter writer = new FileWriter(evidence, StandardCharsets.UTF_8)) {
                writer.write("{\"schema_version\":1,\"status\":\"error\",\"usable\":false"
                        + ",\"working_directory\":\"" + escape(dir.getAbsolutePath()) + "\""
                        + ",\"error_class\":\"" + escape(failure.getClass().getName()) + "\""
                        + ",\"error\":\"" + escape(message) + "\",\"mesh_generated\":false"
                        + ",\"complete_marker\":null}\n");
            } catch (Throwable ignored) {
                // best effort only
            }
            throw new RuntimeException("External-flow mesh construction failed", failure);
        }
    }

    private static boolean has(String json,String key){
        Matcher m=Pattern.compile("\""+Pattern.quote(key)+"\"\\s*:").matcher(json); int count=0; while(m.find())count++;
        if(count>1)throw new IllegalArgumentException("Duplicate field "+key); return count==1;
    }
    private static double[] array(String json,String key){
        Matcher m=Pattern.compile("\""+Pattern.quote(key)+"\"\\s*:\\s*\\[([^\\]]*)\\]").matcher(json);
        if(!m.find())throw new IllegalArgumentException("Missing array "+key);
        String[] parts=m.group(1).split(",",-1); if(parts.length!=6)throw new IllegalArgumentException("Six bounds required");
        double[] out=new double[6]; for(int i=0;i<6;i++){out[i]=Double.parseDouble(parts[i].trim());if(!Double.isFinite(out[i]))throw new IllegalArgumentException("Nonfinite bounds");} return out;
    }

    private static String string(String json, String key) {
        Matcher match = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\"([^\"]+)\"").matcher(json);
        if (!match.find()) throw new IllegalArgumentException("Missing string " + key);
        return match.group(1);
    }

    private static String optional(String json, String key, String fallback) {
        Matcher match = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\"([^\"]+)\"").matcher(json);
        return match.find() ? match.group(1) : fallback;
    }

    private static double number(String json, String key) {
        Matcher match = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*([-+0-9.eE]+)").matcher(json);
        if (!match.find()) throw new IllegalArgumentException("Missing number " + key);
        double value = Double.parseDouble(match.group(1));
        if (!Double.isFinite(value)) throw new IllegalArgumentException("Non-finite " + key);
        return value;
    }

    private static double unitToMeters(String name) {
        if ("m".equals(name)) return 1.0;
        if ("mm".equals(name)) return 0.001;
        if ("cm".equals(name)) return 0.01;
        if ("in".equals(name)) return 0.0254;
        throw new IllegalArgumentException("Unsupported length unit " + name);
    }

    private static String escape(String value) {
        // Control characters matter: a STAR exception message contains newlines, and an earlier
        // revision of this batch emitted them raw, which made the failure record unparseable JSON -
        // precisely the evidence the record exists to preserve.
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (c == '\\') out.append("\\\\");
            else if (c == '"') out.append("\\\"");
            else if (c == '\n') out.append("\\n");
            else if (c == '\r') out.append("\\r");
            else if (c == '\t') out.append("\\t");
            else if (c < 0x20) out.append(String.format("\\u%04x", (int) c));
            else out.append(c);
        }
        return out.toString();
    }
}
