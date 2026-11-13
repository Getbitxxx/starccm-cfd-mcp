"""Atomic state publication counterexamples from the real restricted-token failure."""
import json
import os
import sys
import unittest
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from backend import Backend,ps_quote


class PublicationContract(unittest.TestCase):
    def test_real_provider_failure_is_preserved_and_does_not_justify_acl_expansion(self):
        record=json.loads((ROOT/'test-evidence/product-acceptance/deployed-20261018/FILE-PRIMITIVES-DIAGNOSTIC-RESULT.json').read_text())
        self.assertEqual(record['exit_code'],0)
        self.assertEqual(record['execution_time_limit'],'PT0S')
        result=record['result'];self.assertFalse(result['acl_changed']);self.assertFalse(result['star_started'])
        observations={item['label']:item for item in result['observations']}
        for name in ['Remove-Item_original_path','Remove-Item_canonical_path','Move-Item_first','Move-Item_replace']:
            self.assertEqual(observations[name]['result'],'ERROR')
            self.assertEqual(observations[name]['type'],'System.UnauthorizedAccessException')
        for name in ['File.Delete','File.Move_first']:
            self.assertEqual(observations[name]['result'],'OK')
        # The first null-binding attempt is a retained failure, not evidence of successful Replace.
        self.assertEqual(observations['File.Replace_existing']['inner_type'],'System.ArgumentException')

    def test_both_runners_use_identical_atomic_publication_and_never_remove_previous_state(self):
        sources=[(ROOT/'worker'/name).read_text() for name in ['run_pipeline.ps1','run_job.ps1']]
        helpers=[s[s.index('function Publish-FormalStateFile'):s.index('\nfunction ',s.index('function Publish-FormalStateFile')+1)] for s in sources]
        self.assertEqual(helpers[0],helpers[1])
        self.assertIn('[System.Management.Automation.Language.NullString]::Value',helpers[0])
        self.assertIn('[IO.File]::Replace(',helpers[0]);self.assertIn('[IO.File]::Move(',helpers[0])
        self.assertNotIn('Delete(',helpers[0]);self.assertNotIn('Remove-Item',helpers[0])
        self.assertEqual(sources[0].count('Publish-FormalStateFile $temp '),1)
        self.assertEqual(sources[1].count('Publish-FormalStateFile $temporary '),2)


@unittest.skipUnless(os.environ.get('STARCCM_WINDOWS_GUARDS')=='1','explicit Windows atomic I/O fixture; no STAR')
class WindowsAtomicPublication(unittest.TestCase):
    def test_state_acl_allows_only_observed_owner_transfer_and_redundant_admin_ace(self):
        base=ROOT/'test-evidence/product-acceptance/deployed-20261018'
        before=json.loads((base/'ATOMIC-IO-RESTRICTED-PREPARED.json').read_text())['initial_sddl']
        observed=json.loads((base/'ATOMIC-IO-CONTROLLER-ACL-AFTER.json').read_text())
        after=observed['sddl'];sid=observed['worker_sid']
        payload={'before':before,'worker_sid':sid,'cases':[
            {'name':'original','sddl':before,'expected':True},
            {'name':'actual_os_replace','sddl':after,'expected':True},
            {'name':'extra_sid','sddl':after.replace('D:AI','D:AI(A;;FR;;;S-1-5-21-1-2-3-9999)',1),'expected':False},
            {'name':'worker_full_control','sddl':after.replace('0x1301bf','FA'),'expected':False},
            {'name':'worker_write_dac','sddl':after.replace('0x1301bf','0x1701bf'),'expected':False},
            {'name':'worker_write_owner','sddl':after.replace('0x1301bf','0x1b01bf'),'expected':False},
            {'name':'unexpected_owner','sddl':after.replace('O:'+sid,'O:S-1-5-21-1-2-3-9999',1),'expected':False},
            {'name':'group_changed','sddl':after.replace('G:S-1-5-21-125541176-3054636984-2811364288-513','G:S-1-5-21-1-2-3-9999',1),'expected':False},
            {'name':'inheritance_disabled','sddl':after.replace('D:AI','D:PAI',1),'expected':False},
            {'name':'explicit_worker_duplicate','sddl':after.replace('D:AI','D:AI(A;;0x1301bf;;;'+sid+')',1),'expected':False},
            {'name':'deny_ace','sddl':after.replace('D:AI','D:AI(D;;FR;;;'+sid+')',1),'expected':False}]}
        source=(ROOT/'worker/identity_probe.ps1').read_text()
        helper=source[source.index('function Assert-FormalStateFileAcl'):source.index("if((Get-FileHash -LiteralPath $ConfigPath")]
        code='$p=ConvertFrom-Json '+ps_quote(json.dumps(payload))+';\n'+helper+r'''
$before=New-Object Security.AccessControl.FileSecurity
$before.SetSecurityDescriptorSddlForm($p.before)
$results=@()
foreach($case in $p.cases){
 $after=New-Object Security.AccessControl.FileSecurity;$after.SetSecurityDescriptorSddlForm($case.sddl)
 $accepted=$false;$errorText=$null
 try{Assert-FormalStateFileAcl $before $after $p.worker_sid;$accepted=$true}catch{$errorText=$_.Exception.Message}
 $results+=@{name=$case.name;accepted=$accepted;expected=$case.expected;error=$errorText}
}
@{results=$results;filesystem_acl_changed=$false;star_started=$false}|ConvertTo-Json -Depth 6
'''
        result=Backend().ps(code)
        self.assertEqual(len(result['results']),11)
        self.assertTrue(all(row['accepted'] is row['expected'] for row in result['results']),result)
        evidence=base/'review'/('state-acl-whitelist-'+uuid.uuid4().hex[:16]+'.json')
        evidence.write_text(json.dumps(result,indent=2)+'\n')

    def test_actual_publisher_preserves_old_state_when_source_missing_or_destination_locked(self):
        source=(ROOT/'worker/run_job.ps1').read_text()
        helper=source[source.index('function Publish-FormalStateFile'):source.index('\nfunction Write-State')]
        directory=r'C:\CodexCFDRestricted\atomic-state-'+uuid.uuid4().hex[:16]
        code='$root='+ps_quote(directory)+';[void][IO.Directory]::CreateDirectory($root);\n'+helper+r'''
$destination=Join-Path $root 'state.json';$temporary=Join-Path $root 'state.tmp.json'
for($i=1;$i -le 20;$i++){
 [IO.File]::WriteAllText($temporary,('{"state":"value-'+$i+'"}'))
 Publish-FormalStateFile $temporary $destination
 if(([IO.File]::ReadAllText($destination)|ConvertFrom-Json).state -cne ('value-'+$i) -or [IO.File]::Exists($temporary)){throw 'Atomic publication failed'}
}
$old=[IO.File]::ReadAllText($destination);$missingRefused=$false
try{Publish-FormalStateFile $temporary $destination}catch{$missingRefused=$true}
if(!$missingRefused -or [IO.File]::ReadAllText($destination) -cne $old){throw 'Missing source damaged previous state'}
[IO.File]::WriteAllText($temporary,'{"state":"must-not-replace"}')
$held=[IO.File]::Open($destination,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
$lockedRefused=$false
try{try{Publish-FormalStateFile $temporary $destination}catch{$lockedRefused=$true}}finally{$held.Dispose()}
@{publications=20;missing_refused=$missingRefused;locked_refused=$lockedRefused;old_preserved=([IO.File]::ReadAllText($destination) -ceq $old);failed_source_preserved=[IO.File]::Exists($temporary);star_started=$false}|ConvertTo-Json
'''
        result=Backend().ps(code)
        self.assertEqual(result['publications'],20)
        for name in ['missing_refused','locked_refused','old_preserved','failed_source_preserved']:
            self.assertTrue(result[name],result)
        evidence=ROOT/'test-evidence/product-acceptance/deployed-20261018/review'/('atomic-state-'+uuid.uuid4().hex[:16]+'.json')
        evidence.write_text(json.dumps(dict(result,fixture=directory),indent=2)+'\n')


if __name__=='__main__':unittest.main()
