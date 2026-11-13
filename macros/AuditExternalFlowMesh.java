/* Read-only audit of the saved external-flow volume mesh.
 *
 * WHY THIS IS A SEPARATE MACRO AND A SEPARATE STAGE
 * The mesh builder measures nothing: it writes the cell count and an echo of the settings it applied.
 * "prism_layers: 6" in that record is a configuration echo, not a measurement of the realised near-wall
 * mesh (AGENTS.md section 8). This macro re-opens the SAVED .sim in a fresh STAR session and measures
 * the four quantities a mesh sign-off needs, so the builder is not the one grading its own homework -
 * the same structural reason the geometry audit macro exists.
 *
 * WHAT IT MEASURES (every call pattern below has already been executed in this repository)
 *  - min CellQuality / min FaceValidity / min VolumeChange of the flow Region, via MinReport over the
 *    Region with the native CellQualityFunction / FaceValidityFunction / VolumeChangeFunction field
 *    functions (macros/AuditMrfVolumeMeshQuality.java).
 *  - the realised prism-layer count at the wall, via MinReport/MaxReport of the native
 *    PrismLayerNumber field function on the wall Boundary, and the prism-free area fraction via an
 *    AreaAverageReport of "(${PrismLayerNumber} < 0.5) ? 1 : 0"
 *    (macros/AuditAir3bPrismCoverage.java, macros/InitializeAuditedLocalMesh.java).
 *    Measured evidence settles the semantics of that report: on a saved 3-layer mesh the Shaft wall
 *    returns min = max = 3.0 with 100 percent of its area in the layer-3 bin, and the propeller walls
 *    return min 2.0 / max 3.0; the value a wall face contributes is its own column's realised layer
 *    count (0 where the prism stack is suppressed, N where it is complete). This is the project's own
 *    reading of the field: "实际层号回读：叶片2-3、轮毂1-3、轴3；不能声称全壁面3层".
 *  - the worst (minimum) realised wall layer count, and the first prism layer height in metres.
 *
 * TWO TRAPS THIS MACRO HANDLES EXPLICITLY
 *  1. A boundary with no faces returns MinReport = 1.7976931348623157E308, which IS finite, so a
 *     finite-value guard does not drop it (measured on BLADE 6/12/18 in the AIR-3B evidence). Such a
 *     boundary is flagged and excluded; if the declared wall set is empty the audit fails rather than
 *     producing a vacuous pass.
 *  2. The configured layer count and the measured layer count are different quantities and are named
 *     differently here: `configured_prism_layers` vs `measured_wall_prism_layers_min`.
 *
 * It never initializes, iterates, meshes or saves. The gate values come from the CaseSpec, so no
 * threshold is invented here.
 */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import star.base.neo.ClientServerObject;
import star.base.neo.NamedObject;
import star.base.report.AreaAverageReport;
import star.base.report.ScalarReport;
import star.common.Boundary;
import star.common.CellQualityFunction;
import star.common.FaceValidityFunction;
import star.common.FieldFunctionTypeOption;
import star.common.FieldFunction;
import star.common.FvRepresentation;
import star.common.Region;
import star.common.Simulation;
import star.common.StarMacro;
import star.common.Units;
import star.common.UserFieldFunction;
import star.common.VolumeChangeFunction;

import star.meshing.*;
import star.prismmesher.*;
import star.trimmer.VolumeControlTrimmerSizeOption;

public class AuditExternalFlowMesh extends StarMacro {
    private static PrismAutoMesher prismMesher(AutoMeshOperation operation){
        PrismAutoMesher found=null;
        for(MesherBase mesher:operation.getMeshersCollection()) if(mesher instanceof PrismAutoMesher){
            if(found!=null)throw new IllegalStateException("Multiple prism meshers");found=(PrismAutoMesher)mesher;
        }
        if(found==null)throw new IllegalStateException("Prism mesher missing");return found;
    }

    private static final String COMPLETE = "EXTERNAL_FLOW_MESH_QUALITY_AUDITED";
    // Measured sentinel for "this boundary has no faces"; see the class comment.
    private static final double NO_FACES = 1.0E300;

    @Override
    public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        File evidence = new File(dir, "external_flow_mesh_quality.json");
        List<ClientServerObject> temporary = new ArrayList<ClientServerObject>();
        try {
            String spec = new String(Files.readAllBytes(new File(dir, "case_spec.json").toPath()),
                    StandardCharsets.UTF_8);
            String explicitConfig=explicitConfiguration(sim,spec);
            String regionName = optional(spec, "region_name", "AUTO_EXTERNAL_FLOW");
            String wallName = string(spec, "body_wall_boundary_name");
            int configuredLayers = (int) number(spec, "mesh_prism_layers");
            int minWallLayers = (int) number(spec, "mesh_min_wall_prism_layers");
            double minCellQuality = number(spec, "mesh_min_cell_quality");
            double minFaceValidity = number(spec, "mesh_min_face_validity");
            double minVolumeChange = number(spec, "mesh_min_volume_change");
            if (configuredLayers < 1 || configuredLayers > 40) {
                throw new IllegalArgumentException("Configured prism layer count is out of range");
            }
            if (minWallLayers < 1 || minWallLayers > configuredLayers) {
                throw new IllegalArgumentException("Casespec wall prism layer requirement is out of range");
            }
            if (!(minCellQuality > 0 && minFaceValidity > 0 && minVolumeChange > 0)) {
                throw new IllegalArgumentException("CaseSpec mesh quality thresholds are not positive");
            }
            if (sim.getSimulationIterator().getCurrentIteration() != 0) {
                throw new IllegalStateException("Mesh audit requires an unsolved saved mesh");
            }
            Region region = sim.getRegionManager().getRegion(regionName);
            if (region == null) throw new IllegalStateException("Flow region missing: " + regionName);
            FvRepresentation mesh = (FvRepresentation) sim.getRepresentationManager().getObject("Volume Mesh");
            if (mesh == null || mesh.getCellCount() <= 0) {
                throw new IllegalStateException("No volume mesh to audit");
            }
            if (!mesh.describesRegion(region)) {
                throw new IllegalStateException("The Volume Mesh does not describe the flow region");
            }
            long cells = mesh.getCellCount();

            double cellQuality = minOverRegion(sim, mesh, region, temporary,
                    selectField(sim, mesh, CellQualityFunction.class, "CellQuality"));
            double faceValidity = minOverRegion(sim, mesh, region, temporary,
                    selectField(sim, mesh, FaceValidityFunction.class, "FaceValidity"));
            double volumeChange = minOverRegion(sim, mesh, region, temporary,
                    selectField(sim, mesh, VolumeChangeFunction.class, "VolumeChange"));

            FieldFunction layerNumber = nativeField(sim, mesh, "PrismLayerNumber");
            FieldFunction firstHeight = nativeField(sim, mesh, "FirstPrismLayerHeight");
            Units metres = sim.getUnitsManager().getObject("m");

            StringBuilder boundaries = new StringBuilder();
            int wallCount = 0;
            int zeroFaceCount = 0;
            double worstWallLayers = Double.MAX_VALUE;
            double worstWallFreeFraction = 0.0;
            double smallestFirstHeight = Double.MAX_VALUE;
            for (Boundary boundary : region.getBoundaryManager().getBoundaries()) {
                if (boundary.getBoundaryType() instanceof star.common.InternalBoundary) continue;
                String name = boundary.getPresentationName();
                boolean isDeclaredWall = name.equals(wallName);
                double layerMin = report(sim, temporary, mesh, layerNumber, boundary,
                        "star.base.report.MinReport", null);
                double layerMax = report(sim, temporary, mesh, layerNumber, boundary,
                        "star.base.report.MaxReport", null);
                double freeFraction = report(sim, temporary, mesh,
                        prismFreeIndicator(sim, temporary), boundary,
                        "star.base.report.AreaAverageReport", null);
                double heightMin = report(sim, temporary, mesh, firstHeight, boundary,
                        "star.base.report.MinReport", metres);
                boolean noFaces = !(layerMin < NO_FACES) || !(layerMax < NO_FACES);
                if (noFaces) {
                    zeroFaceCount++;
                } else if (!(layerMin >= 0.0) || !(layerMax >= layerMin)) {
                    throw new IllegalStateException("Invalid prism layer range on " + name
                            + ": " + layerMin + " .. " + layerMax);
                }
                if (!(freeFraction >= 0.0 && freeFraction <= 1.0)) {
                    throw new IllegalStateException("Invalid prism-free fraction on " + name + ": " + freeFraction);
                }
                boundaries.append(boundaries.length() == 0 ? "" : ",")
                        .append("{\"boundary\":\"").append(escape(name))
                        .append("\",\"declared_wall\":").append(isDeclaredWall)
                        .append(",\"zero_face_sentinel\":").append(noFaces)
                        .append(",\"measured_prism_layers_min\":").append(json(layerMin))
                        .append(",\"measured_prism_layers_max\":").append(json(layerMax))
                        .append(",\"measured_prism_free_area_fraction\":").append(json(freeFraction))
                        .append(",\"measured_first_layer_height_m_min\":").append(json(heightMin))
                        .append("}");
                if (isDeclaredWall) {
                    wallCount++;
                    if (noFaces) {
                        throw new IllegalStateException("Declared wall boundary has no faces: " + name
                                + "; the worst-wall layer gate would be vacuous");
                    }
                    worstWallLayers = Math.min(worstWallLayers, layerMin);
                    worstWallFreeFraction = Math.max(worstWallFreeFraction, freeFraction);
                    smallestFirstHeight = Math.min(smallestFirstHeight, heightMin);
                }
            }
            if (wallCount < 1) {
                throw new IllegalStateException("Declared wall boundary " + wallName
                        + " was not found in region " + regionName + "; the gate would be vacuous");
            }
            if (worstWallLayers == Double.MAX_VALUE || !(smallestFirstHeight < Double.MAX_VALUE)) {
                throw new IllegalStateException("No measurable wall prism layer count or first layer height");
            }
            boolean cellQualityPass = cellQuality >= minCellQuality;
            boolean faceValidityPass = faceValidity >= minFaceValidity;
            boolean volumeChangePass = volumeChange > minVolumeChange;
            boolean prismPass = worstWallLayers >= (double) minWallLayers;
            boolean prismFreePass = worstWallFreeFraction <= 1.0E-9;
            boolean passed = cellQualityPass && faceValidityPass && volumeChangePass
                    && prismPass && prismFreePass;

            try (FileWriter writer = new FileWriter(evidence, StandardCharsets.UTF_8)) {
                writer.write("{\"schema_version\":1,\"status\":\"" + (passed ? "ok" : "gate_failed")
                        + "\",\"read_only\":true,\"region\":\"" + escape(region.getPresentationName())
                        + "\",\"cells\":" + cells
                        + ",\"measured_cell_quality_min\":" + json(cellQuality)
                        + ",\"measured_face_validity_min\":" + json(faceValidity)
                        + ",\"measured_volume_change_min\":" + json(volumeChange)
                        + ",\"measured_wall_prism_layers_min\":" + json(worstWallLayers)
                        + ",\"measured_wall_prism_free_area_fraction_max\":" + json(worstWallFreeFraction)
                        + ",\"measured_wall_first_layer_height_m_min\":" + json(smallestFirstHeight)
                        + ",\"explicit_mesh_configuration\":" + explicitConfig
                        + ",\"configured_prism_layers\":" + configuredLayers
                        + ",\"required_wall_prism_layers\":" + minWallLayers
                        + ",\"declared_wall_boundaries\":" + wallCount
                        + ",\"zero_face_boundaries\":" + zeroFaceCount
                        + ",\"quality_gate\":{\"cell_quality\":{\"value\":" + json(cellQuality)
                        + ",\"bound\":" + json(minCellQuality) + ",\"op\":\">=\",\"passed\":"
                        + cellQualityPass + "},\"face_validity\":{\"value\":" + json(faceValidity)
                        + ",\"bound\":" + json(minFaceValidity) + ",\"op\":\">=\",\"passed\":"
                        + faceValidityPass + "},\"volume_change\":{\"value\":" + json(volumeChange)
                        + ",\"bound\":" + json(minVolumeChange) + ",\"op\":\">\",\"passed\":"
                        + volumeChangePass + "},\"wall_prism_layers\":{\"value\":"
                        + json(worstWallLayers) + ",\"bound\":" + minWallLayers
                        + ",\"op\":\">=\",\"passed\":" + prismPass
                        + "},\"wall_prism_free_area\":{\"value\":" + json(worstWallFreeFraction)
                        + ",\"bound\":1.0E-9,\"op\":\"<=\",\"passed\":" + prismFreePass
                        + "},\"passed\":" + passed + "}"
                        + ",\"boundaries\":[" + boundaries + "]"
                        + ",\"complete_marker\":\"" + COMPLETE + "\"}\n");
            }
            if (!passed) {
                throw new IllegalStateException("Mesh quality gate failed; measured values are in "
                        + evidence.getName());
            }
            System.out.println(COMPLETE);
        } catch (Throwable failure) {
            String message = String.valueOf(failure);
            // A failure record must still be parseable JSON: escape() turns newlines and other control
            // characters into escapes. A third review predicted this defect and it still shipped once.
            if (!evidence.isFile()) {
                try (FileWriter writer = new FileWriter(evidence, StandardCharsets.UTF_8)) {
                    writer.write("{\"schema_version\":1,\"status\":\"error\",\"usable\":false"
                            + ",\"working_directory\":\"" + escape(dir.getAbsolutePath()) + "\""
                            + ",\"error_class\":\"" + escape(failure.getClass().getName()) + "\""
                            + ",\"error\":\"" + escape(message) + "\",\"complete_marker\":null}\n");
                } catch (Throwable ignored) {
                    // best effort only
                }
            }
            throw new RuntimeException("External-flow mesh quality audit failed", failure);
        } finally {
            Collections.reverse(temporary);
            for (ClientServerObject object : temporary) {
                try {
                    sim.deleteObjects(Collections.singletonList(object));
                } catch (Throwable ignored) {
                    // best effort only
                }
            }
        }
    }

    private static double minOverRegion(Simulation sim, FvRepresentation mesh, Region region,
            List<ClientServerObject> temporary, FieldFunction field) throws Exception {
        return report(sim, temporary, mesh, field, region, "star.base.report.MinReport", null);
    }

    private static double report(Simulation sim, List<ClientServerObject> temporary,
            FvRepresentation mesh, FieldFunction field, NamedObject part, String reportClass, Units units)
            throws Exception {
        ScalarReport report = sim.getReportManager().create(reportClass);
        temporary.add(report);
        report.setRepresentation(mesh);
        report.setFieldFunction(field);
        report.getParts().setObjects(part);
        if (units != null) report.setUnits(units);
        return report.getReportMonitorValue();
    }

    private static FieldFunction selectField(Simulation sim, FvRepresentation mesh, Class<?> type,
            String label) {
        for (FieldFunction candidate : sim.getFieldFunctionManager().getScalarFieldFunctions(mesh)) {
            if (type.isInstance(candidate)) return candidate;
        }
        throw new IllegalStateException("Required mesh metric unavailable: " + label);
    }

    private static FieldFunction nativeField(Simulation sim, FvRepresentation mesh, String name) {
        for (FieldFunction candidate : sim.getFieldFunctionManager().getScalarFieldFunctions(mesh)) {
            if (name.equals(candidate.getFunctionName())) return candidate;
        }
        throw new IllegalStateException("Required native field unavailable: " + name);
    }

    private static FieldFunction prismFreeIndicator(Simulation sim, List<ClientServerObject> temporary) {
        UserFieldFunction indicator = sim.getFieldFunctionManager().createFieldFunction();
        temporary.add(indicator);
        indicator.setFunctionName("TEMP_PRISM_FREE_" + temporary.size());
        indicator.getTypeOption().setSelected(FieldFunctionTypeOption.Type.SCALAR);
        indicator.setDefinition("(${PrismLayerNumber} < 0.5) ? 1 : 0");
        return indicator;
    }

    /** JSON number, or null for the "this boundary has no faces" sentinel and other non-finites. */
    private static String json(double value) {
        if (!Double.isFinite(value) || Math.abs(value) >= NO_FACES) return "null";
        return Double.toString(value);
    }

    private static String explicitConfiguration(Simulation sim,String spec){
        boolean requested=spec.contains("\"mesh_prism_first_layer_height_m\"");
        boolean localRequested=spec.contains("\"mesh_nearfield_bounds_m\"");
        if(!requested && !localRequested)return "null";
        AutoMeshOperation op=null; int count=0;
        for(MeshOperation candidate:sim.get(MeshOperationManager.class).getObjects()) if(candidate instanceof AutoMeshOperation){op=(AutoMeshOperation)candidate;count++;}
        if(count!=1)throw new IllegalStateException("Explicit configuration requires unique auto mesh operation");
        String prismJson="null";
        if(requested){
        PrismThickness total=op.getDefaultValues().get(PrismThickness.class);
        PrismStretchingOption mode=prismMesher(op).getPrismStretchingOption();
        double first=op.getDefaultValues().get(PrismWallThickness.class).getSIValue();
        double thick=total.getAbsoluteSizeValue().getSIValue();
        if(total.getRelativeOrAbsoluteOption().getSelectedElement()!=RelativeOrAbsoluteOption.Type.ABSOLUTE || mode.getSelectedElement()!=PrismStretchingOption.Type.WALL_THICKNESS)throw new IllegalStateException("Explicit prism mode mismatch");
        equal(first,number(spec,"mesh_prism_first_layer_height_m"));equal(thick,number(spec,"mesh_prism_total_thickness_m"));
        if(op.getDefaultValues().get(NumPrismLayers.class).getNumLayers()!=(int)number(spec,"mesh_prism_layers"))throw new IllegalStateException("Layer configuration mismatch");
        prismJson="{\"mode\":\"WALL_THICKNESS\",\"total_mode\":\"ABSOLUTE\",\"configured_first_height_m\":"+first+",\"configured_total_thickness_m\":"+thick+"}";
        }
        if(localRequested){
        String[] labels={"nearfield","wake"}; int matched=0;
        for(CustomMeshControl c:op.getCustomMeshControls().getObjects()) if(c instanceof VolumeCustomMeshControl){
            VolumeCustomMeshControl v=(VolumeCustomMeshControl)c;
            if(v.getGeometryObjects().getObjects().size()!=1)throw new IllegalStateException("Expected one block per volume control");
            Object geometry=v.getGeometryObjects().getObjects().iterator().next();
            if(!(geometry instanceof SimpleBlockPart))throw new IllegalStateException("Expected SI block control");
            SimpleBlockPart b=(SimpleBlockPart)geometry;
            double[] lo=b.getCorner1().getInternalVector().toDoubleArray(), hi=b.getCorner2().getInternalVector().toDoubleArray();
            int found=-1;
            for(int i=0;i<2;i++){
                double[] bounds=array(spec,"mesh_"+labels[i]+"_bounds_m"); boolean same=true;
                for(int a=0;a<3;a++)same &= Math.abs(lo[a]-bounds[a])<1e-12 && Math.abs(hi[a]-bounds[a+3])<1e-12;
                if(same)found=i;
            }
            if(found<0 || (matched&(1<<found))!=0)throw new IllegalStateException("Unexpected or duplicate local block");
            matched|=1<<found;
            if(!v.getCustomConditions().get(VolumeControlTrimmerSizeOption.class).getVolumeControlBaseSizeOption())throw new IllegalStateException("Local trimmer sizing disabled");
            VolumeControlSize size=v.getCustomValues().get(VolumeControlSize.class);
            if(size.getRelativeOrAbsoluteOption().getSelectedElement()!=RelativeOrAbsoluteOption.Type.ABSOLUTE)throw new IllegalStateException("Local control is not absolute");
            equal(size.getAbsoluteSizeValue().getSIValue(),number(spec,"mesh_"+labels[found]+"_cell_size_m"));
        }
        if(matched!=3)throw new IllegalStateException("Both local block controls must be present");
        }
        return "{\"prism\":"+prismJson+",\"local_controls_native_verified\":"+localRequested+"}";
    }
    private static double[] array(String spec,String key){
        Matcher m=Pattern.compile("\""+Pattern.quote(key)+"\"\\s*:\\s*\\[([^\\]]*)\\]").matcher(spec);
        if(!m.find())throw new IllegalArgumentException("Missing bounds "+key);String[] parts=m.group(1).split(",",-1);
        if(parts.length!=6)throw new IllegalArgumentException("Expected six bounds");double[] out=new double[6];
        for(int i=0;i<6;i++){out[i]=Double.parseDouble(parts[i].trim());if(!Double.isFinite(out[i]))throw new IllegalArgumentException("Nonfinite bounds");}return out;
    }
    private static void equal(double actual,double expected){if(!Double.isFinite(actual)||Math.abs(actual-expected)>Math.max(1e-15,Math.abs(expected)*1e-10))throw new IllegalStateException("Native mesh configuration mismatch");}

    private static String string(String text, String key) {
        Matcher match = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\"([^\"]+)\"")
                .matcher(text);
        if (!match.find()) throw new IllegalArgumentException("Missing " + key + " in CaseSpec");
        return match.group(1);
    }

    private static String optional(String text, String key, String fallback) {
        Matcher match = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*\"([^\"]+)\"")
                .matcher(text);
        return match.find() ? match.group(1) : fallback;
    }

    private static double number(String text, String key) {
        Matcher match = Pattern.compile("\"" + Pattern.quote(key) + "\"\\s*:\\s*([-+0-9.eE]+)")
                .matcher(text);
        if (!match.find()) throw new IllegalArgumentException("Missing " + key + " in CaseSpec");
        double value = Double.parseDouble(match.group(1));
        if (!Double.isFinite(value)) throw new IllegalArgumentException("Non-finite " + key);
        return value;
    }

    private static String escape(String value) {
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
