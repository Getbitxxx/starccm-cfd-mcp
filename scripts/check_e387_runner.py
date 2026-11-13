"""Windows PowerShell AST and pure gate checks; never starts STAR."""
import hashlib
import json
from pathlib import Path
from backend import Backend, ps_quote

ROOT = Path(__file__).resolve().parents[1]


def main():
    path = ROOT / 'scripts/security/run_e387_roles_adhoc.ps1'
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    b = Backend()
    remote = b.remote_root + '\\transport\\e387-runner-' + digest + '.ps1'
    b.scp(path, remote)
    result = b.ps(f'''
if((Get-FileHash -LiteralPath {ps_quote(remote)} -Algorithm SHA256).Hash.ToLowerInvariant() -cne {ps_quote(digest)}){{throw 'Test source upload differs'}}
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({ps_quote(remote)},[ref]$tokens,[ref]$errors)
if($errors.Count -gt 0){{throw 'PowerShell syntax errors'}}
foreach($name in @('Rounded','VersionEvidence')){{$f=$ast.Find({{param($a) $a -is [Management.Automation.Language.FunctionDefinitionAst] -and $a.Name -eq $name}},$true);Invoke-Expression $f.Extent.Text}}
Rounded 0.02 '0.02';Rounded 0.07997708443196 '0.079977084'
$negative=0
foreach($v in @(@(0.021,'0.02'),@(0.07997708264434898,'0.079977084'))){{try{{Rounded $v[0] $v[1]}}catch{{$negative++}}}}
$version=@(VersionEvidence @('Simcenter STAR-CCM+ 2502.0001 Build 20.02.008'))
try{{VersionEvidence @('Simcenter STAR-CCM+ 2502.0001 Build 20.02.009')}}catch{{$negative++}}
try{{VersionEvidence @('20.02.008 is only a path')}}catch{{$negative++}}
if($negative -ne 4){{throw 'Negative volume/area/version checks did not reject'}}
[pscustomobject]@{{parse_errors=@();version_positive=$version;negative_controls_rejected=$negative;star_started=$false;runner_sha256={ps_quote(digest)}}} | ConvertTo-Json
''')
    output = ROOT / 'campaigns/uav-2026/evidence/uav-airfoil-e387/p1.3-fix-20261024/runner-parse.json'
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
