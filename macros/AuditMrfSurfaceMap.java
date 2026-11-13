/* Measure outer and envelope patch identities in CaseSpec-owned MRF fluid parts. */
import java.io.File;
import java.io.FileWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import star.common.PartSurface;
import star.common.Simulation;
import star.common.SimulationPartManager;
import star.common.StarMacro;
import star.meshing.MeshOperationPart;

public class AuditMrfSurfaceMap extends StarMacro {
    private static final String COMPLETE = "MRF_SURFACE_MAP_COMPLETE";
    @Override public void execute() {
        Simulation sim = getActiveSimulation();
        File dir = sim.getSessionDirFile();
        try {
            String spec = new String(Files.readAllBytes(new File(dir, "case_spec.json").toPath()), StandardCharsets.UTF_8);
            String geometry = object(spec, "geometry");
            String domain = object(spec, "domain");
            String zone = object(spec, "rotation_zone");
            double[] oc = array(domain, "cylinder_center_xy_m", 2);
            double or = number(domain, "cylinder_radius_m");
            double omin = number(domain, "cylinder_z_min_m"), omax = number(domain, "cylinder_z_max_m");
            double[] ic = array(zone, "center_xy_m", 2);
            double ir = number(zone, "radius_m");
            double imin = number(zone, "z_min_m"), imax = number(zone, "z_max_m");
            if (!(or > ir && ir > 0 && omin < imin && imin < imax && imax < omax) ||
                    Math.abs(oc[0]-ic[0]) > 1e-12 || Math.abs(oc[1]-ic[1]) > 1e-12)
                throw new IllegalStateException("Audited coaxial cylinder geometry dimensions are invalid");
            MeshOperationPart stationary = part(sim, string(geometry, "stationary_fluid_part"));
            MeshOperationPart rotating = part(sim, string(geometry, "rotating_fluid_part"));
            if (stationary == rotating) throw new IllegalStateException("Fluid roles select the same part");
            String[] keys = {"outer_inlet", "outer_outlet", "outer_side", "inner_upstream", "inner_downstream", "inner_side"};
            double[][] samples = {
                {oc[0],oc[1],omax}, {oc[0],oc[1],omin}, {oc[0]+or,oc[1],(omin+omax)/2},
                {ic[0],ic[1],imax}, {ic[0],ic[1],imin}, {ic[0]+ir,ic[1],(imin+imax)/2}
            };
            PartSurface[] fixed = new PartSurface[6], moving = new PartSurface[3];
            Set<PartSurface> distinctFixed = new HashSet<PartSurface>(), distinctMoving = new HashSet<PartSurface>();
            for (int i=0;i<6;i++) { fixed[i]=surface(stationary,samples[i]); distinctFixed.add(fixed[i]); }
            for (int i=0;i<3;i++) {
                moving[i]=surface(rotating,samples[i+3]);
                distinctMoving.add(moving[i]);
                if (!fixed[i+3].getPresentationName().equals(moving[i].getPresentationName()))
                    throw new IllegalStateException("Stationary/rotating envelope surface provenance differs at "+keys[i+3]);
            }
            if (distinctFixed.size()!=6 || distinctMoving.size()!=3 ||
                    distinctFixed.size()!=stationary.getPartSurfaceManager().getObjects().size() ||
                    distinctMoving.size()>=rotating.getPartSurfaceManager().getObjects().size())
                throw new IllegalStateException("Measured role surfaces overlap, omit a stationary patch, or lack recovered rotor walls");
            try (FileWriter out=new FileWriter(new File(dir,"mrf_surface_map.json"),StandardCharsets.UTF_8)) {
                out.write("{\"schema_version\":2,\"status\":\"ok\",\"stationary_part\":\""+esc(stationary.getPresentationName())+
                    "\",\"rotating_part\":\""+esc(rotating.getPresentationName())+"\",");
                for(int i=0;i<6;i++) out.write("\""+keys[i]+"\":\""+esc(fixed[i].getPresentationName())+"\",");
                out.write("\"rotor_wall_surfaces\":"+(rotating.getPartSurfaceManager().getObjects().size()-distinctMoving.size())+
                    ",\"selection_basis\":\"SI cylinder sample patch IDs and unique PartSurface ownership\",\"samples\":[");
                for(int i=0;i<6;i++) {
                    if(i>0)out.write(",");
                    out.write("{\"role\":\""+keys[i]+"\",\"point_m\":["+samples[i][0]+","+samples[i][1]+","+samples[i][2]+
                        "],\"stationary_patch_id\":"+stationary.getPatchIdBasedOnLabXYZ(samples[i][0],samples[i][1],samples[i][2])+
                        (i>=3?",\"rotating_patch_id\":"+rotating.getPatchIdBasedOnLabXYZ(samples[i][0],samples[i][1],samples[i][2]):"")+"}");
                }
                out.write("],\"complete_marker\":\""+COMPLETE+"\"}\n");
            }
            System.out.println(COMPLETE);
        } catch(Throwable failure) { throw new RuntimeException("MRF surface-map audit failed",failure); }
    }
    private static MeshOperationPart part(Simulation sim,String name) { Object p=sim.get(SimulationPartManager.class).getPart(name);if(!(p instanceof MeshOperationPart))throw new IllegalStateException("CaseSpec fluid role is not a Boolean part");return (MeshOperationPart)p; }
    private static PartSurface surface(MeshOperationPart part,double[] xyz) {
        int patch=part.getPatchIdBasedOnLabXYZ(xyz[0],xyz[1],xyz[2]);
        if(patch<0)throw new IllegalStateException("SI cylinder sample has no measured patch ID");
        PartSurface found=null;
        for(PartSurface candidate:part.getPartSurfaceManager().getObjects()) {
            List<Integer> patches=part.getPartSurfacePatches(candidate);
            if(patches.contains(patch)) {
                if(found!=null)throw new IllegalStateException("Patch ID belongs to multiple part surfaces");
                found=candidate;
            }
        }
        if(found==null)throw new IllegalStateException("Sample patch has no unique PartSurface");
        return found;
    }
    private static String object(String j,String k) { Matcher m=Pattern.compile("\\\""+k+"\\\"\\s*:\\s*\\{([^{}]*)\\}").matcher(j);if(!m.find())throw new IllegalArgumentException("Missing object "+k);return m.group(1); }
    private static String string(String j,String k) { Matcher m=Pattern.compile("\\\""+k+"\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(j);if(!m.find())throw new IllegalArgumentException("Missing string "+k);return m.group(1); }
    private static double number(String j,String k) { Matcher m=Pattern.compile("\\\""+k+"\\\"\\s*:\\s*([-+0-9.eE]+)").matcher(j);if(!m.find())throw new IllegalArgumentException("Missing number "+k);double v=Double.parseDouble(m.group(1));if(!Double.isFinite(v))throw new IllegalArgumentException("Nonfinite "+k);return v; }
    private static double[] array(String j,String k,int size) { Matcher m=Pattern.compile("\\\""+k+"\\\"\\s*:\\s*\\[([^\\]]+)\\]").matcher(j);if(!m.find())throw new IllegalArgumentException("Missing vector "+k);String[] values=m.group(1).split(",");if(values.length!=size)throw new IllegalArgumentException("Wrong vector size "+k);double[] a=new double[size];for(int i=0;i<size;i++){a[i]=Double.parseDouble(values[i].trim());if(!Double.isFinite(a[i]))throw new IllegalArgumentException("Nonfinite vector "+k);}return a; }
    private static String esc(String s) { return s.replace("\\","\\\\").replace("\"","\\\"").replace("\n","\\n").replace("\r","\\r").replace("\t","\\t"); }
}

