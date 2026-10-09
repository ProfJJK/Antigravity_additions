"""Create successors only. Keep v3 attempt namespaces and unchanged Python pins."""
import hashlib
from pathlib import Path

W=Path(__file__).resolve().parent
PIN={
 'check-worker-native-auth-status-six-r3-v3.ps1':'84983fad313a8ff8112882d22c9a2d53d89681d3172dabd57726fec8095ad1f3',
 'login_pipeline_worker_interactive_r3.ps1':'1ec70cd0beeacde2d11df1948ac009be92fbdd5e5cdb8cf78b3f7f6346920109',
 'login-six-workers-status-first-r3-v3.ps1':'8bccc9de2553b60cfc2fdf81b8352a3bc6ae184d02445a1cb02ff5847e5a079a',
 'authenticate-native-profiles-status-first-r3-v3.ps1':'0c375dfb36d761bdce5bcffb905bc00146b9bbc6de32f45d81aea8bd0f6b0747',
 'commission-first-warden-r3-v3.ps1':'153b77a003329c0295da867cf87dae24b2aa36549e6003e00998fca39bd92db9',
 'run-pipeline-commissioning-r3-v3.ps1':'dda02eeee0006509fc8c32319f74e089e4a79fbfdda659bbb8a443eaa8640aa0',
}

def sha(name):return hashlib.sha256((W/name).read_bytes()).hexdigest()
def read(name):
 assert sha(name)==PIN[name],name
 return (W/name).read_text(encoding='utf-8-sig')
def write(name,text):
 with (W/name).open('x',encoding='utf-8',newline='\n') as f:f.write(text)
 print(name,sha(name))
def replace(text,old,new):
 assert old in text,old
 return text.replace(old,new)

def import_line(importer,var):
 loop='d' if var=='d' else 'text'
 return f" foreach(${loop} in @({importer} (Join-Path $PSScriptRoot 'protected-code-inspection-v4.ps1') '{sha('protected-code-inspection-v4.ps1')}' @('Assert-CodeTreeOnce'))){{. ([scriptblock]::Create(${loop}))}}\n"

def main():
 name='check-worker-native-auth-status-six-r3-v3.ps1';text=read(name)
 start=text.index('function Assert-CodeTreeOnce {');end=text.index('$installedEntries=Assert-CodeTreeOnce',start)
 loader="""# Read and hold the exact reviewed scanner before importing its single definition.
$inspectionPath=Join-Path $PSScriptRoot 'protected-code-inspection-v4.ps1'
$inspectionStream=Open-VerifiedFile $inspectionPath 'SCANNER_PIN' (Get-Item -LiteralPath $inspectionPath).Length
$held.Add($inspectionStream)
$reader=[IO.StreamReader]::new($inspectionStream,[Text.Encoding]::UTF8,$true,4096,$true)
try{$inspectionText=$reader.ReadToEnd()}finally{$reader.Dispose()}
$tokens=$null;$errors=$null;$inspectionAst=[Management.Automation.Language.Parser]::ParseInput($inspectionText,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Reviewed protected inspection helper does not parse.'}
$definitions=@($inspectionAst.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Assert-CodeTreeOnce'},$true))
if($definitions.Count -ne 1){throw 'Exact protected inspection definition missing.'}
. ([scriptblock]::Create($definitions[0].Extent.Text))
""".replace('SCANNER_PIN',sha('protected-code-inspection-v4.ps1'))
 text=text[:start]+loader+text[end:]
 write(name.replace('-v3.ps1','-v4.ps1'),text)

 name='login_pipeline_worker_interactive_r3.ps1';text=read(name)
 text=replace(text,"@('Assert-CodeTreeOnce','Assert-VenvBinding'","@('Assert-VenvBinding'")
 marker='Initialize-FileIdentity\ntry{'
 hold=("$inspectionPath=Join-Path $PSScriptRoot 'protected-code-inspection-v4.ps1'\n"
       "$held.Add((Open-VerifiedFile $inspectionPath '"+sha('protected-code-inspection-v4.ps1')+"' (Get-Item -LiteralPath $inspectionPath).Length))\n")
 text=replace(text,marker,import_line('Import-PinnedFunctions','d')+'Initialize-FileIdentity\n'+hold+'try{')
 write('login_pipeline_worker_interactive_r3_v4.ps1',text)

 name='login-six-workers-status-first-r3-v3.ps1';text=read(name)
 text=replace(text,"'login_pipeline_worker_interactive_r3.ps1'","'login_pipeline_worker_interactive_r3_v4.ps1'")
 text=replace(text,PIN['login_pipeline_worker_interactive_r3.ps1'],sha('login_pipeline_worker_interactive_r3_v4.ps1'))
 text=replace(text,'check-worker-native-auth-status-six-r3-v3.ps1','check-worker-native-auth-status-six-r3-v4.ps1')
 text=replace(text,PIN['check-worker-native-auth-status-six-r3-v3.ps1'],sha('check-worker-native-auth-status-six-r3-v4.ps1'))
 text=replace(text,",'Assert-CodeTreeOnce'",'')
 marker=' $runtime=Assert-R3InstalledBindings'
 text=replace(text,marker,import_line('Import-SeriesFunctions','d')+marker)
 write(name.replace('-v3.ps1','-v4.ps1'),text)

 name='authenticate-native-profiles-status-first-r3-v3.ps1';text=read(name)
 text=replace(text,'login_pipeline_worker_interactive_r3.ps1','login_pipeline_worker_interactive_r3_v4.ps1')
 text=replace(text,PIN['login_pipeline_worker_interactive_r3.ps1'],sha('login_pipeline_worker_interactive_r3_v4.ps1'))
 for old,new in [('login-six-workers-status-first-r3-v3.ps1','login-six-workers-status-first-r3-v4.ps1'),('check-worker-native-auth-status-six-r3-v3.ps1','check-worker-native-auth-status-six-r3-v4.ps1')]:
  text=replace(text,old,new)
  text=replace(text,PIN[old],sha(new))
 write(name.replace('-v3.ps1','-v4.ps1'),text)

 name='commission-first-warden-r3-v3.ps1';text=read(name)
 text=replace(text,",'Assert-CodeTreeOnce'",'')
 marker=' $runtime=Assert-R3InstalledBindings;'
 text=replace(text,marker,import_line('Import-FirstStartFunctions','text')+marker)
 write(name.replace('-v3.ps1','-v4.ps1'),text)

 name='run-pipeline-commissioning-r3-v3.ps1';text=read(name)
 text=replace(text,'login_pipeline_worker_interactive_r3.ps1','login_pipeline_worker_interactive_r3_v4.ps1')
 text=replace(text,PIN['login_pipeline_worker_interactive_r3.ps1'],sha('login_pipeline_worker_interactive_r3_v4.ps1'))
 for old,new in [('authenticate-native-profiles-status-first-r3-v3.ps1','authenticate-native-profiles-status-first-r3-v4.ps1'),('commission-first-warden-r3-v3.ps1','commission-first-warden-r3-v4.ps1'),('login-six-workers-status-first-r3-v3.ps1','login-six-workers-status-first-r3-v4.ps1')]:
  text=replace(text,old,new)
  text=replace(text,PIN[old],sha(new))
 text=text.replace('Default is metadata-only. Never repeat a partial series.','Default is metadata-only. Deliberate authentication continuation uses a fresh attempt; partial controller start remains held.')
 write(name.replace('-v3.ps1','-v4.ps1'),text)

if __name__=='__main__':main()
