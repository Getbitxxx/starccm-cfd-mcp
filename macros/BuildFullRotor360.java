/* Expand an explicitly audited fluid sector on an isolated geometry-only copy. */
import java.io.*;
import java.nio.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.*;
import java.util.regex.*;
import star.base.neo.DoubleVector;
import star.common.*;
import star.meshing.*;

public class BuildFullRotor360 extends StarMacro {
    private static final String COMPLETE="FULL_ROTOR_360_BUILD_COMPLETE";
    @Override public void execute() {
        Simulation sim=getActiveSimulation(); File dir=sim.getSessionDirFile();
        File evidence=new File(dir,"full_rotor_360_build.json");
        try {
            String spec=Files.readString(new File(dir,"case_spec.json").toPath(),StandardCharsets.UTF_8);
            String sourceName=string(spec,"source_sector_fluid_part");
            String sourceRegionName=string(spec,"source_sector_fluid_region");
            if(!"rotating_fluid_sector".equals(string(spec,"source_sector_role")))
                throw new IllegalArgumentException("Source must be explicitly audited as rotating_fluid_sector");
            SimulationPartManager parts=sim.get(SimulationPartManager.class);
            GeometryPart source=parts.getPart(sourceName);
            if(!(source instanceof MeshPart) || !((MeshPart)source).isClosed() || !((MeshPart)source).isManifold())
                throw new IllegalStateException("Source fluid-sector topology is not closed and manifold");
            List<String> expectedSurfaces=strings(spec,"source_sector_surface_names");
            Set<String> actualSurfaces=new TreeSet<>();
            for(PartSurface surface:source.getPartSurfaces())actualSurfaces.add(surface.getPresentationName());
            if(expectedSurfaces.isEmpty() || !actualSurfaces.equals(new TreeSet<>(expectedSurfaces)))
                throw new IllegalStateException("Source surface ownership differs from audited CaseSpec");
            Region sourceRegion=sim.getRegionManager().getRegion(sourceRegionName);
            if(sourceRegion==null || !(sourceRegion.getRegionType() instanceof FluidRegion))
                throw new IllegalStateException("Source region is not an audited fluid region");
            Set<PartSurface> owned=new HashSet<>();
            for(Boundary boundary:sourceRegion.getBoundaryManager().getBoundaries())
                for(Object obj:boundary.getPartSurfaceGroup().getObjects())if(obj instanceof PartSurface)owned.add((PartSurface)obj);
            if(!owned.containsAll(source.getPartSurfaces()))
                throw new IllegalStateException("Source fluid-region boundaries do not own source surfaces");
            File sourceStl=new File(dir,"original_sector_fluid.stl");
            ((MeshPart)source).exportStlPart(sourceStl.getAbsolutePath(),true);
            double sourceVolume=stlVolume(sourceStl);
            double lo=number(spec,"source_sector_volume_min_m3"),hi=number(spec,"source_sector_volume_max_m3");
            if(!(sourceVolume>=lo && sourceVolume<=hi))throw new IllegalStateException("Measured source fluid-sector volume outside audited range: "+sourceVolume);
            List<String> selectedLegacy=strings(spec,"legacy_regions_to_remove");
            Set<String> actualLegacy=new TreeSet<>();
            for(Region region:sim.getRegionManager().getRegions())actualLegacy.add(region.getPresentationName());
            if(selectedLegacy.isEmpty() || !actualLegacy.equals(new TreeSet<>(selectedLegacy)))
                throw new IllegalStateException("Legacy region inventory does not match explicit cleanup ownership");
            int beforeRegions=actualLegacy.size(),beforeInterfaces=sim.getInterfaceManager().getObjects().size();
            int beforeContinua=sim.getContinuumManager().getObjects().size();
            FvRepresentation volumeMesh=(FvRepresentation)sim.getRepresentationManager().getObject("Volume Mesh");
            if(volumeMesh==null)throw new IllegalStateException("Source volume mesh representation unavailable for explicit cleanup audit");
            long beforeCells=volumeMesh.getCellCount();
            int beforeIteration=sim.getSimulationIterator().getCurrentIteration();
            boolean beforeInitialized=sim.isInitialized();
            int beforeMeshOperations=sim.get(MeshOperationManager.class).getObjects().size();
            List<String> legacyReports=new ArrayList<>();
            for(star.base.report.Report report:sim.getReportManager().getObjects())
                legacyReports.add(report.getPresentationName());
            for(String physicalReport:new String[]{string(spec,"thrust_report"),string(spec,"torque_report")})
                if(legacyReports.contains(physicalReport))
                    throw new IllegalArgumentException("New physical report role overlaps retained legacy catalogue: "+physicalReport);
            // Source regions, interfaces, mesh and old fields are retired only in this worker copy.
            sim.getSolution().clearSolution();
            sim.get(MeshPipelineController.class).clearGeneratedMeshes();
            sim.getInterfaceManager().removeObjects(new ArrayList<>(sim.getInterfaceManager().getObjects()));
            sim.getRegionManager().removeObjects(new ArrayList<>(sim.getRegionManager().getObjects()));
            sim.get(MeshOperationManager.class).removeObjects(new ArrayList<>(sim.get(MeshOperationManager.class).getObjects()));
            sim.getContinuumManager().removeObjects(new ArrayList<>(sim.getContinuumManager().getObjects()));
            // Keep the old report catalogue: its monitors/field functions are dependency-linked.
            // The original regions, continua, mesh and fields have been explicitly removed above.
            // R17 physical reports use distinct CaseSpec roles and never read these old reports.
            // Clearing/removing source mesh disconnects its old FvRepresentation. Reacquire the
            // current manager object; a failed read must never become a fabricated zero-cell count.
            List<String> representationsAfter=new ArrayList<>();boolean anyFvRepresentation=false;int fvRepresentationCount=0;
            Representation currentVolume=null;
            for(Representation representation:sim.getRepresentationManager().getObjects()){
                String representationName=representation.getPresentationName();
                representationsAfter.add(representationName+":"+representation.getClass().getName());
                if(representation instanceof FvRepresentation){anyFvRepresentation=true;fvRepresentationCount++;}
                if("Volume Mesh".equals(representationName)){
                    if(currentVolume!=null)throw new IllegalStateException("Duplicate current Volume Mesh representations");
                    currentVolume=representation;
                }
            }
            if(currentVolume!=null && fvRepresentationCount>1)
                throw new IllegalStateException("Additional current finite-volume representations make cleanup evidence ambiguous");
            Long afterCells=null;String meshEvidenceRule;
            if(currentVolume!=null){
                if(!(currentVolume instanceof FvRepresentation))throw new IllegalStateException("Current Volume Mesh has an unexpected representation type");
                afterCells=(long)((FvRepresentation)currentVolume).getCellCount();
                meshEvidenceRule="fresh_volume_representation_cell_count";
            }else meshEvidenceRule="no_fv_representation_and_zero_regions";
            int afterRegions=sim.getRegionManager().getRegions().size();
            boolean geometryOnlyVerified=sim.getRegionManager().getRegions().isEmpty() && sim.getInterfaceManager().getObjects().isEmpty() &&
                sim.getContinuumManager().getObjects().isEmpty() && !sim.isInitialized() && sim.getSimulationIterator().getCurrentIteration()==0 &&
                coldMeshEvidenceAllows(afterCells,afterRegions,anyFvRepresentation);
            if(!geometryOnlyVerified)throw new IllegalStateException("Source cleanup did not yield a geometry-only state");
            List<String> outputNames=strings(spec,"sector_fluid_parts");
            double[] angles=array(spec,"sector_repeat_angles_deg",-1),axis=array(spec,"axis",3),origin=array(spec,"center_m",3);
            if(outputNames.size()!=angles.length+1 || !outputNames.get(0).equals(sourceName) || angles.length==0)
                throw new IllegalArgumentException("Sector fluid output selectors do not match explicit repeat angles");
            for(GeometryPart existing:parts.getObjects())if(outputNames.subList(1,outputNames.size()).contains(existing.getPresentationName()))
                throw new IllegalStateException("Selected repeated sector already exists: "+existing.getPresentationName());
            for(int i=0;i<angles.length;i++) {
                TransformPartsOperation op=(TransformPartsOperation)sim.get(MeshOperationManager.class).createTransformPartsOperation(Arrays.asList(source));
                op.setPresentationName("Sector repeat "+angles[i]);op.setCreateNewOutputParts(true);op.setDeleteEmptyParts(false);op.setLinkOutputPartName(false);
                RotationControl rotate=op.getTransforms().createRotationControl();
                rotate.getAngle().setValueAndUnits(angles[i],sim.getUnitsManager().getObject("deg"));
                rotate.getAxisVector().setComponents(axis[0],axis[1],axis[2]);rotate.setOrigin(new DoubleVector(origin));
                op.execute(); GeometryPart output=null;
                for(SimulationObject obj:op.getOutputPartObjects())if(obj instanceof GeometryPart){if(output!=null)throw new IllegalStateException("Multiple sector repeat outputs");output=(GeometryPart)obj;}
                if(!(output instanceof MeshPart) || !((MeshPart)output).isClosed() || !((MeshPart)output).isManifold())
                    throw new IllegalStateException("Repeated fluid-sector topology failed");
                output.setPresentationName(outputNames.get(i+1));
            }
            sim.saveState(new File(dir,"full_rotor_360_built.sim").getAbsolutePath());
            String json="{\"schema_version\":2,\"status\":\"ok\",\"source_part\":\""+escape(sourceName)+"\",\"source_role\":\"rotating_fluid_sector\",\"source_signed_volume_m3\":"+sourceVolume+
                ",\"source_closed\":true,\"source_manifold\":true,\"source_surfaces\":"+jsonStrings(expectedSurfaces)+",\"sector_fluid_parts\":"+jsonStrings(outputNames)+
                ",\"rotation_angles_deg\":"+Arrays.toString(angles)+",\"axis_direction\":"+Arrays.toString(axis)+",\"axis_origin_m\":"+Arrays.toString(origin)+
                ",\"cleanup_before\":{\"regions\":"+beforeRegions+",\"interfaces\":"+beforeInterfaces+",\"continua\":"+beforeContinua+",\"mesh_operations\":"+beforeMeshOperations+",\"cells\":"+beforeCells+",\"iteration\":"+beforeIteration+",\"initialized\":"+beforeInitialized+"}"+
                ",\"cleanup_after\":{\"regions\":"+afterRegions+",\"interfaces\":0,\"continua\":0,\"cells\":"+afterCells+",\"iteration\":0,\"initialized\":false}"+
                ",\"geometry_only_verified\":"+geometryOnlyVerified+
                ",\"cleanup_mesh_evidence_rule\":\""+meshEvidenceRule+"\",\"representations_after_cleanup\":"+jsonStrings(representationsAfter)+
                ",\"current_fv_representation_count\":"+fvRepresentationCount+
                ",\"legacy_report_catalogue_retained\":"+jsonStrings(legacyReports)+
                ",\"legacy_reports_reused_for_new_solution\":false,\"source_cad_preserved\":true,\"complete_marker\":\""+COMPLETE+"\"}\n";
            Files.writeString(evidence.toPath(),json,StandardCharsets.UTF_8);System.out.println(COMPLETE);
        } catch(Throwable e){try{Files.writeString(evidence.toPath(),"{\"status\":\"error\",\"error\":\""+escape(e.toString())+"\"}\n",StandardCharsets.UTF_8);}catch(Throwable ignored){}throw new RuntimeException("Full-circle fluid-sector preparation failed",e);}
    }
    static boolean coldMeshEvidenceAllows(Long cells,int regions,boolean anyFvRepresentation){
        return regions==0 && (cells!=null ? cells.longValue()==0 : !anyFvRepresentation);
    }
    private static double stlVolume(File file)throws Exception{byte[] b=Files.readAllBytes(file.toPath());ByteBuffer q=ByteBuffer.wrap(b).order(ByteOrder.LITTLE_ENDIAN);if(b.length<84)throw new IOException("Short STL");int n=q.getInt(80);if(n<=0 || b.length!=84L+50L*n)throw new IOException("Invalid binary STL");double volume=0;for(int i=0;i<n;i++){double[][]v=new double[3][3];for(int j=0;j<3;j++)for(int a=0;a<3;a++)v[j][a]=q.getFloat(84+i*50+12+j*12+a*4);double[]a=v[0],c=v[1],d=v[2];volume+=(a[0]*(c[1]*d[2]-c[2]*d[1])-a[1]*(c[0]*d[2]-c[2]*d[0])+a[2]*(c[0]*d[1]-c[1]*d[0]))/6;}return volume;}
    private static String string(String j,String k){Matcher m=Pattern.compile("\""+Pattern.quote(k)+"\"\\s*:\\s*\"([^\"]+)\"").matcher(j);if(!m.find())throw new IllegalArgumentException("Missing "+k);return m.group(1);}
    private static double number(String j,String k){Matcher m=Pattern.compile("\""+Pattern.quote(k)+"\"\\s*:\\s*([-+0-9.eE]+)").matcher(j);if(!m.find())throw new IllegalArgumentException("Missing "+k);double v=Double.parseDouble(m.group(1));if(!Double.isFinite(v))throw new IllegalArgumentException("Nonfinite "+k);return v;}
    private static List<String> strings(String j,String k){Matcher m=Pattern.compile("\""+Pattern.quote(k)+"\"\\s*:\\s*\\[([^\\]]*)\\]").matcher(j);if(!m.find())throw new IllegalArgumentException("Missing "+k);List<String> out=new ArrayList<>();Matcher t=Pattern.compile("\"([^\"]+)\"").matcher(m.group(1));while(t.find())out.add(t.group(1));return out;}
    private static double[] array(String j,String k,int n){Matcher m=Pattern.compile("\""+Pattern.quote(k)+"\"\\s*:\\s*\\[([^\\]]+)\\]").matcher(j);if(!m.find())throw new IllegalArgumentException("Missing "+k);String[] words=m.group(1).split(",");if(n>0&&words.length!=n)throw new IllegalArgumentException("Wrong vector length "+k);double[] v=new double[words.length];for(int i=0;i<v.length;i++){v[i]=Double.parseDouble(words[i].trim());if(!Double.isFinite(v[i]))throw new IllegalArgumentException("Nonfinite "+k);}return v;}
    private static String jsonStrings(List<String> v){List<String> a=new ArrayList<>();for(String s:v)a.add("\""+escape(s)+"\"");return "["+String.join(",",a)+"]";}
    private static String escape(String s){StringBuilder out=new StringBuilder();for(char c:s.toCharArray()){if(c=='\\')out.append("\\\\");else if(c=='"')out.append("\\\"");else if(c<0x20)out.append(String.format("\\u%04x",(int)c));else out.append(c);}return out.toString();}
}
