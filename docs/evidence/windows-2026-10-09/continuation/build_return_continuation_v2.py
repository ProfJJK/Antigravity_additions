from pathlib import Path

W=Path(__file__).parent
text=(W/'run-return-setup-r3.ps1').read_text()
text=text.replace('three already reviewed\nsteps, once and in order: inspection, scoped foundation, private project.', 'two reviewed continuation\nsteps, once and in order: fresh scoped foundation, private project.')
text=text.replace("if($Step.name -in @('inspection','foundation')){", "if($Step.name -ceq 'foundation'){")
start=text.index("        if($Step.name -eq 'inspection'){")
end=text.index("    }elseif($Step.name -eq 'project'){",start)
text=text[:start]+'''        if($Report.ram_scoped_roots_verified -ne 6 -or $Report.empty_registry_capacity -ne 4 -or
           $Report.automatic_retry_allowed -isnot [bool] -or $Report.automatic_retry_allowed -or
           $Report.partial_outputs_preserved -isnot [bool] -or -not $Report.partial_outputs_preserved -or
           $Report.ram_provision_started -ne $true -or $Report.registry_provision_started -ne $true -or
           $Report.diagnostic_receipt_sha256 -cne 'cccd0f32d8a15f92d9b9a6bdca8688ea48ea22920fdf89c293839d6d3452bae9' -or
           $Report.failed_foundation_receipt_sha256 -cne 'bf1237fa1d435985b16e9faf61c8e51afcc0eaadbab31e9ad43bff9e11716336'){
            throw 'Continuation foundation scope/prior evidence differs.'}
'''+text[end:]
text=text.replace('cochem-return-setup-series/1','cochem-return-continuation-series/2').replace('THREE_SETUP_PHASES_VERIFIED','TWO_CONTINUATION_PHASES_VERIFIED')
text=text.replace('Running reviewed setup phase:', 'Running reviewed continuation phase:')
text=text.replace('Setup series stopped.', 'Continuation stopped.')
text=text.replace('Hold all three wrappers', 'Hold both wrappers')
lines=text.splitlines()
text='\n'.join(x for x in lines if "name='inspection';path=" not in x)+'\n'
text=text.replace("path=(Join-Path $PSScriptRoot 'provision-execution-foundation-r3.ps1');sha256='402c27276b56b42cac3c8ecc6fa5b8fd0da4361c57c3abd5c2bf2d432724208c'", "path=(Join-Path $PSScriptRoot 'provision-execution-foundation-r3-v2.ps1');sha256='UNFROZEN'")
text=text.replace("plan_schema='cochem-execution-foundation-plan/1';result_schema='cochem-execution-foundation-task-result/1'", "plan_schema='cochem-execution-foundation-plan/2';result_schema='cochem-execution-foundation-task-result/2'")
text=text.replace('ExecutionFoundation4.2.7-windows-20261007-r3\\execution-foundation.json', 'ExecutionFoundation4.2.7-windows-20261007-r3-v2\\execution-foundation.json')
with (W/'run-return-continuation-r3-v2.ps1').open('x',encoding='utf-8',newline='\n') as out:out.write(text)

test=(W/'test_return_setup_series_r3.py').read_text()
test=test.replace("SCRIPT=ROOT/'run-return-setup-r3.ps1'", "SCRIPT=ROOT/'run-return-continuation-r3-v2.ps1'")
test=test.replace("        ('inspection','execution-prerequisites','ExecutionPrerequisites','READ_ONLY_PREREQUISITES_VERIFIED'),\n",'')
test=test.replace("        row['fixture']=value;rows.append(row)", """        if name=='foundation':
            row['result_schema']='cochem-execution-foundation-task-result/2'
            row['plan_schema']='cochem-execution-foundation-plan/2'
            row['receipt']=receipt.replace('r3\\\\execution-foundation.json','r3-v2\\\\execution-foundation.json')
            value.update(schema=row['result_schema'],receipt_path=row['receipt'],partial_outputs_preserved=True,
                diagnostic_receipt_sha256='cccd0f32d8a15f92d9b9a6bdca8688ea48ea22920fdf89c293839d6d3452bae9',
                failed_foundation_receipt_sha256='bf1237fa1d435985b16e9faf61c8e51afcc0eaadbab31e9ad43bff9e11716336')
        row['fixture']=value;rows.append(row)""")
test=test.replace("['inspection','foundation','project']", "['foundation','project']")
test=test.replace('THREE_SETUP_PHASES_VERIFIED','TWO_CONTINUATION_PHASES_VERIFIED')
start=test.index("@pytest.mark.parametrize('stage,key,replacement',[")
end=test.index('def test_invalid_success_cannot_advance',start)
test=test[:start]+'''@pytest.mark.parametrize('stage,key,replacement',[
    (0,'status','READ_ONLY_PLAN'),(0,'receipt_path',r'C:\different.json'),(0,'activation_ready','false'),
    (0,'empty_registry_capacity',8),(0,'ram_scoped_roots_verified',4),(0,'automatic_retry_allowed',True),
    (0,'install_receipt_sha256','b'*64),(0,'last_task_result',2),(0,'partial_outputs_preserved',False),
    (0,'diagnostic_receipt_sha256','0'*64),(0,'failed_foundation_receipt_sha256','0'*64),
    (1,'baseline_commit','0'*40),(1,'files',4),(1,'config_sha256','c'*64)])
'''+test[end:]
test=test.replace("step=phases()[2]", "step=phases()[1]")
test=test.replace('Running reviewed setup phase', 'Running reviewed continuation phase')
with (W/'test_return_continuation_r3_v2.py').open('x',encoding='utf-8',newline='\n') as out:out.write(test)
print('CREATED_V2_SERIES_AND_FIXTURES')
