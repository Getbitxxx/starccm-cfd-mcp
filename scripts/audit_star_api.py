"""Read-only API evidence for the installed STAR-CCM+ 2502 build (`javap`, not recall).

The jar locator derives its search patterns from the requested class names. An earlier revision used a
hard-coded pattern list, which silently made the tool blind to every class outside it: it reported
"not found" for classes that do exist, which is exactly the "looks absent but was never looked for"
failure mode this project keeps meeting.

AGENTS.md section 7 requires version-matched official evidence before a macro is written. This
tool dumps the declared members of the classes the project's geometry stages depend on, straight
out of the jars of the installed 20.02.008 build, and writes nothing except stdout. It never
executes STAR.

It exists because the full STAR classpath exceeds the Windows command-line limit: the script
first locates the few jars that actually declare the requested classes and uses only those.

Usage:
    python scripts/audit_star_api.py > test-evidence/<case>/star2502-native-part-api.txt
"""
from __future__ import annotations

import base64
import json
import sys

sys.path.insert(0, 'scripts')
from backend import Backend  # noqa: E402

CLASSES = [
    # Shape construction (used by the static-body geometry stage).
    'star.meshing.MeshPartFactory',
    'star.meshing.SimpleSpherePart',
    'star.meshing.SimpleBlockPart',
    'star.meshing.SimpleCylinderPart',
    'star.meshing.SimpleShapePart',
    # Topology and measurement.
    'star.meshing.MeshPart',
    'star.meshing.LeafMeshPart',
    'star.common.Coordinate',
    'star.common.ScalarPhysicalQuantity',
    'star.common.PartSurface',
    'star.common.GeometryPartManager',
    'star.common.GeometryPart',
    # Units.
    'star.common.Units',
    'star.common.UnitsManager',
]

PS = r'''
$classes=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__CLASSES__')) | ConvertFrom-Json
$javap=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__JAVAP__'))
$starRoot=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__STARROOT__'))
Add-Type -AssemblyName System.IO.Compression.FileSystem
$jars=@(Get-ChildItem -LiteralPath $starRoot -Filter '*.jar' -File -Recurse)
$out=[ordered]@{}
$out.javap_exists=(Test-Path -LiteralPath $javap)
$out.jar_count=$jars.Count
# The full classpath exceeds the Windows command-line limit. Locate the jars that actually
# declare the requested classes and use only those, keeping -cp short and exact.
$owners=@{}
foreach($jar in $jars){
  try{$zip=[IO.Compression.ZipFile]::OpenRead($jar.FullName)}catch{continue}
  try{
    foreach($entry in $zip.Entries){
      $n=$entry.FullName
      $wanted=$false
      foreach($c in $classes){ if($n -eq ($c -replace '\.','/') + '.class'){ $wanted=$true } }
      if($wanted){
        if(!$owners.ContainsKey($n)){$owners[$n]=@()}
        $owners[$n]+=$jar.FullName
      }
    }
  }finally{$zip.Dispose()}
}
$short=@($owners.Values | ForEach-Object {$_} | Sort-Object -Unique)
$out.owner_jars=$short
$cp=($short | ForEach-Object {$_.Replace([char]92,[char]47)}) -join ';'
$out.cp_length=$cp.Length
foreach($c in $classes){
  $prev=$ErrorActionPreference
  $ErrorActionPreference='Continue'
  try{$r = (& $javap -cp $cp $c 2>&1 | Out-String)}catch{$r='JAVAP-ERROR: ' + $_.Exception.Message}
  $ErrorActionPreference=$prev
  $out[$c]=$r
}
$out | ConvertTo-Json -Depth 4
'''


def build_ps(config: dict) -> str:
    javac = config['javac_path']
    javap = javac[: -len('javac.exe')] + 'javap.exe'
    star_root = str(__import__('pathlib').Path(config['star_launcher'].replace('\\', '/')).parent.parent).replace('/', '\\')
    work = config['remote_root'].rstrip('\\/') + '\\transport\\javap'
    return (PS.replace('__CLASSES__', base64.b64encode(json.dumps(CLASSES).encode()).decode())
              .replace('__JAVAP__', base64.b64encode(javap.encode()).decode())
              .replace('__STARROOT__', base64.b64encode(star_root.encode()).decode())
              .replace('__WORK__', base64.b64encode(work.encode()).decode()))


if __name__ == '__main__':
    b = Backend()
    result = b.ps(build_ps(b.config), timeout=900)
    for name, text in result.items():
        if name in ('javap_exists', 'jar_count', 'cp_length'):
            print(name, '=', text)
            continue
        print('=' * 30, name)
        print(text[:4000])
