// Adapted from the supplied mesh-quality macro; metric labels and empty-boundary handling corrected.
// Simcenter STAR-CCM+ macro: ExportMeshQuality.java
// Exports volume mesh quality statistics and compact mesh report.
package macro;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Date;
import java.util.List;
import java.util.Locale;

import star.base.neo.ClientServerObject;
import star.base.neo.NeoProperty;
import star.base.report.ElementCountReport;
import star.base.report.MaxReport;
import star.base.report.MinReport;
import star.base.report.ScalarReport;
import star.base.report.VolumeAverageReport;
import star.common.Boundary;
import star.common.CellQualityFunction;
import star.common.FaceValidityFunction;
import star.common.FieldFunction;
import star.common.FieldFunctionManager;
import star.common.FvRepresentation;
import star.common.Region;
import star.common.Simulation;
import star.common.SkewnessAngleFunction;
import star.common.StarMacro;
import star.common.VolumeChangeFunction;
import star.common.WallBoundary;
import star.metrics.CellAspectRatioFunction;
import star.metrics.LeastSquaresQualityFunction;

public class ExportMeshQuality extends StarMacro {

  private Simulation simulation;
  private FvRepresentation volumeMesh;
  private final ArrayList<ClientServerObject> temporaryReports = new ArrayList<>();

  public void execute() {
    execute0();
  }

  private void execute0() {
    simulation = getActiveSimulation();
    volumeMesh = (FvRepresentation) simulation.getRepresentationManager().getObject("Volume Mesh");

    List<Region> regions = getVolumeMeshRegions();
    if (regions.isEmpty()) {
      simulation.println("No regions are described by the Volume Mesh representation.");
      return;
    }

    File outputDir = simulation.getSessionDirFile();
    String timestamp = new SimpleDateFormat("yyyyMMdd_HHmmss").format(new Date());
    File csvFile = new File(outputDir, "mesh_quality_summary_" + timestamp + ".csv");
    File compactReportFile = new File(outputDir, "compact_mesh_report_" + timestamp + ".txt");

    try {
      writeQualitySummary(csvFile, regions);
      writeCompactMeshReport(compactReportFile, regions);
      simulation.println("Mesh quality CSV exported to: " + csvFile.getAbsolutePath());
      simulation.println("Compact mesh report exported to: " + compactReportFile.getAbsolutePath());
      simulation.println("MESH_QA_COMPLETE");
    } catch (Exception ex) {
      simulation.println("Failed to export mesh quality: " + ex.getMessage());
      throw new RuntimeException(ex);
    } finally {
      if (!temporaryReports.isEmpty()) {
        simulation.deleteObjects(temporaryReports);
      }
    }
  }

  private List<Region> getVolumeMeshRegions() {
    List<Region> regions = new ArrayList<>();
    for (Region region : simulation.getRegionManager().getRegions()) {
      if (volumeMesh.describesRegion(region)) {
        regions.add(region);
      }
    }
    return regions;
  }

  private void writeQualitySummary(File csvFile, List<Region> regions) throws Exception {
    try (PrintWriter writer = new PrintWriter(new OutputStreamWriter(new FileOutputStream(csvFile), StandardCharsets.UTF_8))) {
      writer.println("section,region,metric,stat,value");

      writeGlobalVolumeMeshCounts(writer);

      for (Region region : regions) {
        writeElementCounts(writer, region);
      }

      writeMetric(writer, regions, "Cell Quality", CellQualityFunction.class, new String[] {"Cell Quality", "CellQuality"});
      writeMetric(writer, regions, "Volume Change", VolumeChangeFunction.class, new String[] {"Volume Change", "VolumeChange"});
      writeMetric(writer, regions, "Face Validity", FaceValidityFunction.class, new String[] {"Face Validity", "FaceValidity"});
      writeMetric(writer, regions, "Skewness Angle", SkewnessAngleFunction.class, new String[] {"Skewness Angle", "SkewnessAngle"});
      writeMetric(writer, regions, "Cell Aspect Ratio", CellAspectRatioFunction.class, new String[] {"Aspect Ratio", "Cell Aspect Ratio", "CellAspectRatio"});
      writeMetric(writer, regions, "Least Squares Quality", LeastSquaresQualityFunction.class, new String[] {"Least Squares Quality", "LeastSquaresQuality"});
      writeMetric(writer, regions, "Cell Volume", null, new String[] {"Volume", "Cell Volume", "CellVolume"});
      writeYPlusSummary(writer, regions);
    }
  }

  private void writeGlobalVolumeMeshCounts(PrintWriter writer) {
    writeCsv(writer, "global_count", "ALL", "Cells", "count", volumeMesh.getCellCount());
    writeCsv(writer, "global_count", "ALL", "Interior Faces", "count", volumeMesh.getInteriorFaceCount());
    writeCsv(writer, "global_count", "ALL", "Vertices", "count", volumeMesh.getVertexCount());
  }

  private void writeElementCounts(PrintWriter writer, Region region) {
    ElementCountReport countReport = simulation.getReportManager().create("star.base.report.ElementCountReport");
    temporaryReports.add(countReport);
    countReport.setPresentationName("MQ_TMP_Element Count_" + region.getPresentationName());
    countReport.setRepresentation(volumeMesh);
    countReport.getParts().setQuery(null);
    countReport.getParts().setObjects(region);

    Collection<String> keys = countReport.getCountKeys(region);
    for (String key : keys) {
      writeCsv(writer, "element_count", region.getPresentationName(), key, "count", countReport.getCount(region, key));
    }
  }

  private void writeMetric(PrintWriter writer, List<Region> regions, String metricName, Class<?> functionClass, String[] aliases) {
    FieldFunction fieldFunction = findFieldFunction(functionClass, aliases);
    if (fieldFunction == null) {
      simulation.println("Skipped mesh metric because field function was not found: " + metricName);
      writeCsv(writer, "mesh_quality", "ALL", metricName, "not_found", Double.NaN);
      return;
    }

    for (Region region : regions) {
      writeCsv(writer, "mesh_quality", region.getPresentationName(), metricName, "min", scalarValue("star.base.report.MinReport", fieldFunction, region));
      writeCsv(writer, "mesh_quality", region.getPresentationName(), metricName, "max", scalarValue("star.base.report.MaxReport", fieldFunction, region));
      writeCsv(writer, "mesh_quality", region.getPresentationName(), metricName, "volume_average", scalarValue("star.base.report.VolumeAverageReport", fieldFunction, region));
    }
  }

  private FieldFunction findFieldFunction(Class<?> functionClass, String[] aliases) {
    FieldFunctionManager manager = simulation.getFieldFunctionManager();

    if (functionClass != null) {
      for (FieldFunction fieldFunction : manager.getScalarFieldFunctions(volumeMesh)) {
        if (functionClass.isInstance(fieldFunction)) {
          return fieldFunction;
        }
      }
    }

    for (String alias : aliases) {
      if (manager.hasFunction(alias)) {
        return manager.getFunction(alias);
      }
    }

    return null;
  }

  private double scalarValue(String reportClassName, FieldFunction fieldFunction, Region region) {
    ScalarReport report = simulation.getReportManager().create(reportClassName);
    temporaryReports.add(report);

    report.setPresentationName("MQ_TMP_" + report.getClass().getSimpleName() + "_" + fieldFunction.getFunctionName() + "_" + region.getPresentationName());
    report.setRepresentation(volumeMesh);
    report.setFieldFunction(fieldFunction);
    report.getParts().setQuery(null);
    report.getParts().setObjects(region);

    try {
      return report.getReportMonitorValue();
    } catch (Exception ex) {
      simulation.println("Failed metric " + fieldFunction.getFunctionName() + " on region " + region.getPresentationName() + ": " + ex.getMessage());
      return Double.NaN;
    }
  }

  private void writeYPlusSummary(PrintWriter writer, List<Region> regions) {
    FieldFunction yPlus = findYPlusFieldFunction();
    if (yPlus == null) {
      simulation.println("Skipped y+ export because no y+ field function was found. Run after a flow solution exists.");
      writeCsv(writer, "y_plus", "ALL", "Wall Y+", "not_found", Double.NaN);
      return;
    }

    List<Boundary> wallBoundaries = getWallBoundaries(regions);
    if (wallBoundaries.isEmpty()) {
      simulation.println("Skipped y+ export because no wall boundaries were found.");
      writeCsv(writer, "y_plus", "ALL", yPlus.getFunctionName(), "no_wall_boundaries", Double.NaN);
      return;
    }

    for (Boundary boundary : wallBoundaries) {
      String boundaryName = boundary.getRegion().getPresentationName() + "/" + boundary.getPresentationName();
      double minimum=scalarValueOnBoundary("star.base.report.MinReport", yPlus, boundary);
      double maximum=scalarValueOnBoundary("star.base.report.MaxReport", yPlus, boundary);
      if (!Double.isFinite(minimum) || !Double.isFinite(maximum) || Math.abs(minimum)>1e300 || Math.abs(maximum)>1e300 || minimum>maximum) {
        writeCsv(writer, "y_plus", boundaryName, yPlus.getFunctionName(), "unavailable_or_empty", Double.NaN);
        continue;
      }
      writeCsv(writer, "y_plus", boundaryName, yPlus.getFunctionName(), "min", minimum);
      writeCsv(writer, "y_plus", boundaryName, yPlus.getFunctionName(), "max", maximum);
      writeCsv(writer, "y_plus", boundaryName, yPlus.getFunctionName(), "area_average", scalarValueOnBoundary("star.base.report.AreaAverageReport", yPlus, boundary));
    }
  }

  private FieldFunction findYPlusFieldFunction() {
    FieldFunctionManager manager = simulation.getFieldFunctionManager();
    String[] aliases = new String[] {
      "WallYplus",
      "WallYPlus",
      "Wall Y+",
      "Wall y+",
      "Wall Yplus",
      "Yplus",
      "YPlus",
      "Y+"
    };

    for (String alias : aliases) {
      if (manager.hasFunction(alias)) {
        return manager.getFunction(alias);
      }
    }

    FieldFunction fallback = findYPlusByName(manager.getScalarFieldFunctions(volumeMesh), true);
    if (fallback != null) {
      return fallback;
    }

    fallback = findYPlusByName(manager.getScalarFieldFunctions(), true);
    if (fallback != null) {
      return fallback;
    }

    fallback = findYPlusByName(manager.getScalarFieldFunctions(volumeMesh), false);
    if (fallback != null) {
      return fallback;
    }

    return findYPlusByName(manager.getScalarFieldFunctions(), false);
  }

  private FieldFunction findYPlusByName(Collection<FieldFunction> fieldFunctions, boolean requireWall) {
    for (FieldFunction fieldFunction : fieldFunctions) {
      String combinedName = (fieldFunction.getFunctionName() + " " + fieldFunction.getPresentationName()).toLowerCase(Locale.ROOT);
      boolean yPlusName = combinedName.contains("yplus") || combinedName.contains("y+");
      boolean wallName = combinedName.contains("wall");
      if (yPlusName && (!requireWall || wallName)) {
        return fieldFunction;
      }
    }
    return null;
  }

  private List<Boundary> getWallBoundaries(List<Region> regions) {
    List<Boundary> wallBoundaries = new ArrayList<>();
    for (Region region : regions) {
      for (Boundary boundary : region.getBoundaryManager().getBoundaries()) {
        if (boundary.getBoundaryType() instanceof WallBoundary || boundary.getPresentationName().toUpperCase(Locale.ROOT).contains("WALL")) {
          wallBoundaries.add(boundary);
        }
      }
    }
    return wallBoundaries;
  }

  private double scalarValueOnBoundary(String reportClassName, FieldFunction fieldFunction, Boundary boundary) {
    ScalarReport report = simulation.getReportManager().create(reportClassName);
    temporaryReports.add(report);

    report.setPresentationName("MQ_TMP_" + report.getClass().getSimpleName() + "_" + fieldFunction.getFunctionName() + "_" + boundary.getPresentationName());
    report.setRepresentation(volumeMesh);
    report.setFieldFunction(fieldFunction);
    report.getParts().setQuery(null);
    report.getParts().setObjects(boundary);

    try {
      return report.getReportMonitorValue();
    } catch (Exception ex) {
      simulation.println("Failed y+ metric " + fieldFunction.getFunctionName() + " on boundary " + boundary.getQualifiedName() + ": " + ex.getMessage());
      return Double.NaN;
    }
  }

  private void writeCompactMeshReport(File outputFile, List<Region> regions) throws Exception {
    NeoProperty compactReport = volumeMesh.generateCompactMeshReport(regions);
    try (PrintWriter writer = new PrintWriter(new OutputStreamWriter(new FileOutputStream(outputFile), StandardCharsets.UTF_8))) {
      writer.println(compactReport.toFormattedString());
    }
  }

  private void writeCsv(PrintWriter writer, String section, String region, String metric, String stat, double value) {
    writer.println(csv(section) + "," + csv(region) + "," + csv(metric) + "," + csv(stat) + "," + value);
  }

  private String csv(String value) {
    if (value == null) {
      return "";
    }
    return "\"" + value.replace("\"", "\"\"") + "\"";
  }
}
