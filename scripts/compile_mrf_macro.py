"""Compile the Phase 2 MRF macro against the installed STAR-CCM+ 2502 API, without starting STAR.

Same javac/471-jar/JDK-21.0.4 pattern as scripts/compile_e387_p2.py, plus the project's
single-slot precondition: no STAR process may be running when the compile job is submitted.
Evidence goes to campaigns/uav-2026/evidence/uav-prop-apc10x7e/compile/.
"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend import Backend, ps_quote

ROOT = Path(__file__).resolve().parents[1]
NAMES = sys.argv[1:] if len(sys.argv) > 1 else ['ConfigureMrfPropellerPhysics']
EVIDENCE = ROOT / 'campaigns/uav-2026/evidence/uav-prop-apc10x7e/compile'


def main() -> int:
    b = Backend()
    sources = {f'macros/{name}.java': hashlib.sha256((ROOT / 'macros' / (name + '.java')).read_bytes()).hexdigest()
               for name in NAMES}
    digest = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
    remote = b.remote_root + '\\candidates\\mrf-prop-' + digest
    b.ps('New-Item -ItemType Directory -Path ' + ps_quote(remote) + ' -Force | Out-Null')
    for name in NAMES:
        b.scp(ROOT / 'macros' / (name + '.java'), remote + '\\' + name + '.java')
    star_root = b.config['star_launcher'].replace('\\', '/').rsplit('/', 2)[0]
    checks = '\n'.join(
        f"if((Get-FileHash -LiteralPath {ps_quote(remote + chr(92) + Path(path).name)} -Algorithm SHA256).Hash.ToLowerInvariant() -ne {ps_quote(sha)}){{throw 'Compile source hash mismatch'}}"
        for path, sha in sources.items())
    result = b.ps(f'''
$star=@(Get-CimInstance Win32_Process | Where-Object {{$_.Name -match '^(starccm|star-ccm|starccmplus).*\\.exe$'}})
if($star.Count -gt 0){{throw ('Existing STAR process detected: '+$star.Count+' - single STAR slot is busy, compile refused')}}
$directory={ps_quote(remote)}
{checks}
$classes=Join-Path $directory 'out'
if(Test-Path -LiteralPath $classes){{Remove-Item -LiteralPath $classes -Recurse -Force}}
New-Item -ItemType Directory -Path $classes | Out-Null
$jars=@(Get-ChildItem -LiteralPath {ps_quote(star_root)} -Filter '*.jar' -File -Recurse | ForEach-Object {{$_.FullName.Replace([char]92,[char]47)}})
if($jars.Count -ne 471){{throw 'Expected 471 installed STAR API jars'}}
$javac={ps_quote(b.config['javac_path'])}
$version=(& $javac -version) -join "`n"
if($version -notmatch '21.0.4'){{throw 'Expected JDK 21.0.4'}}
$files=@(Get-ChildItem -LiteralPath $directory -Filter '*.java' -File | ForEach-Object {{'"'+$_.FullName.Replace([char]92,[char]47)+'"'}})
$argsText='-proc:none'+[Environment]::NewLine+'-encoding UTF-8'+[Environment]::NewLine+'-cp'+[Environment]::NewLine+'"'+($jars -join ';')+'"'+[Environment]::NewLine+'-d'+[Environment]::NewLine+'"'+$classes.Replace([char]92,[char]47)+'"'+[Environment]::NewLine+($files -join [Environment]::NewLine)
$argfile=Join-Path $directory 'javac.args'
[IO.File]::WriteAllText($argfile,$argsText,(New-Object Text.UTF8Encoding($false)))
$stdout=Join-Path $directory 'javac.stdout.txt';$stderr=Join-Path $directory 'javac.stderr.txt'
$process=Start-Process -FilePath $javac -ArgumentList ('@'+$argfile) -Wait -PassThru -NoNewWindow -RedirectStandardOutput $stdout -RedirectStandardError $stderr
$classesList=@(Get-ChildItem -LiteralPath $classes -Filter '*.class' -File | ForEach-Object {{[pscustomobject]@{{name=$_.Name;sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}}}})
[pscustomobject]@{{exit_code=$process.ExitCode;javac=$version;jar_count=$jars.Count;star_processes=$star.Count;remote_directory=$directory;class_count=$classesList.Count;class_hashes=$classesList;stdout=[Text.Encoding]::Default.GetString([IO.File]::ReadAllBytes($stdout));stderr=[Text.Encoding]::Default.GetString([IO.File]::ReadAllBytes($stderr));compiler_log_encoding=[Text.Encoding]::Default.WebName}} | ConvertTo-Json -Depth 5
''', timeout=180)
    result.update(schema_version=1, kind='ad_hoc_macro_compile', case_id='uav-prop-apc10x7e',
                  macro_set='phase2_mrf', source_hashes=sources, star_started=False, reviewed=False,
                  compiled_at=datetime.now(timezone.utc).isoformat(),
                  command='javac @javac.args -proc:none -encoding UTF-8 -cp <471 JARs> -d out <sources>')
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE / f'COMPILE-MRF-2502-{digest[:12]}.json'
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: result.get(k) for k in ['exit_code', 'javac', 'jar_count', 'star_processes',
                                                 'class_hashes', 'stderr']}, ensure_ascii=False, indent=2))
    return 0 if result.get('exit_code') == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
