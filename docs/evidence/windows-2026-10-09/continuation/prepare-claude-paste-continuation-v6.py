"""Prepare additive paste-safe Claude successors; no deployment actions."""
import hashlib
from pathlib import Path

W = Path(__file__).resolve().parent
PINS = {
    'login_pipeline_worker_interactive_r3_v5.ps1': '8e3545109c2bbef9a53763f8af8575ac1ff7376d3bea6c27a5ecd8454af182ec',
    'login-six-workers-status-first-r3-v5.ps1': '6e18cf7d8a816dc36464593fdb9eaed51c7bd45963accb16481befa67df10d58',
    'authenticate-native-profiles-status-first-r3-v5.ps1': '89e6efcad31ff05f94aea905abb15db73b5c85e69eb9ac3508b2b33d03c91d04',
    'run-pipeline-commissioning-r3-v5.ps1': '46f2f8ce1d2f30ccf25bb37e9b6f97decd34a45e07ecdd3cc3f61c6544e210c0',
    'run-post-commissioning-setup-r3-v2.ps1': 'dd2b8119f8f1c7a206c8ef12e6c2ef45634695fd83fe36bc8b361154044125f2',
    'run-reboot-setup-r3-v5.ps1': '99c3a415bf3f2b1acb1a7103aae5bec440fb9b2c6549f7590a1626e3c5b693cc',
}
OLD_HISTORY = '82f66a4c135454ad406ad47cc8b6abce25fdad55951dbd6dc70f30a0c68634d4'
INPUT_FUNCTIONS = ('New-ClaudeCodeInput', 'Update-ClaudeCodeInput',
                   'Complete-ClaudeCodeInput', 'Close-ClaudeCodeInput', 'Clear-ClaudeConsoleInput')


def digest(name):
    return hashlib.sha256((W / name).read_bytes()).hexdigest()


def frozen(name):
    if digest(name) != PINS[name]:
        raise ValueError('Frozen source changed: ' + name)
    return (W / name).read_text(encoding='utf-8-sig')


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Unexpected source replacement count')
    return text.replace(old, new)


def render():
    old = {name: frozen(name) for name in PINS}
    history = digest('claude-session-history-v6.ps1')
    code_input = digest('claude-code-input-v6.ps1')
    outputs = {}

    def add(name, text):
        outputs[name] = text.encode('utf-8')
        return hashlib.sha256(outputs[name]).hexdigest()

    leaf = old['login_pipeline_worker_interactive_r3_v5.ps1']
    leaf = leaf.replace('claude-session-history-v5.ps1', 'claude-session-history-v6.ps1').replace(OLD_HISTORY, history)
    exports = '@(' + ','.join("'" + name + "'" for name in INPUT_FUNCTIONS) + ')'
    marker = "$inspectionPath=Join-Path $PSScriptRoot 'protected-code-inspection-v4.ps1'"
    imports = (
        "$codeInputPath=Join-Path $PSScriptRoot 'claude-code-input-v6.ps1'\n"
        "foreach($definition in @(Import-PinnedFunctions $codeInputPath '" + code_input + "' " + exports + ")){. ([scriptblock]::Create($definition))}\n"
        "$held.Add((Open-VerifiedFile $codeInputPath '" + code_input + "' (Get-Item -LiteralPath $codeInputPath).Length))\n"
    )
    leaf = once(leaf, marker, imports + marker)
    start = leaf.index('    $instance=$task.Run($null);')
    end = leaf.index('    Assert-TaskTerminal $task\n', start)
    leaf = leaf[:start] + r'''    $instance=$task.Run($null);$log=Join-Path $root 'operator.log';$inputPath=Join-Path $root 'input.once';$seen=0;$deadline=[DateTime]::UtcNow.AddMinutes(14)
    $inputState=New-ClaudeCodeInput;$nativePromptReady=$false;$cancelSent=$false
    $nativePrompt='Paste code here if prompted >'
    Write-Host "Claude login for $Slot. Open its displayed browser URL manually if needed."
    Write-Host 'Paste the returned code at the masked Code prompt, then press Enter. Escape cancels. Letters P and Q are ordinary code characters.'
    try{
        while(-not (Test-InstanceComplete $instance)){
            if([DateTime]::UtcNow -ge $deadline){Write-CancelRequest $root $nonce;throw 'Login wrapper deadline reached; cancellation requested. Preserve the task/root and review terminal cleanup; no automatic retry.'}
            if(-not $nativePromptReady -and -not $inputState.Submitted -and -not $cancelSent -and (Test-Path -LiteralPath $log)){
                $stream=[IO.File]::Open($log,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite)
                try{
                    if($stream.Length -gt 131072){throw 'Operator log exceeds its bound.'}
                    $reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8);$text=$reader.ReadToEnd()
                    $promptAt=$text.IndexOf($nativePrompt,[StringComparison]::Ordinal)
                    if($promptAt -ge 0){
                        if($promptAt -gt $seen){[Console]::Write($text.Substring($seen,$promptAt-$seen))}
                        $seen=$text.Length;$nativePromptReady=$true
                        Write-Host '';[Console]::Write('Code (masked; Enter submits, Escape cancels): ')
                    }else{
                        # Retain only a possible split prompt prefix; complete
                        # browser URLs and all other output remain visible.
                        $displayEnd=$text.Length
                        for($tail=[Math]::Min($nativePrompt.Length-1,$text.Length);$tail -gt 0;$tail--){
                            if($text.EndsWith($nativePrompt.Substring(0,$tail),[StringComparison]::Ordinal)){$displayEnd=$text.Length-$tail;break}
                        }
                        if($displayEnd -gt $seen){[Console]::Write($text.Substring($seen,$displayEnd-$seen));$seen=$displayEnd}
                    }
                }finally{$stream.Dispose()}
            }
            $keysRead=0
            while($keysRead -lt 256 -and [Console]::KeyAvailable){
                Update-ClaudeCodeInput -State $inputState -Key ([Console]::ReadKey($true));$keysRead++
                if($inputState.SubmissionRequested -or $inputState.CancelRequested){break}
            }
            if($inputState.CancelRequested -and -not $cancelSent){
                Write-CancelRequest $root $nonce;$cancelSent=$true
                $drain=Clear-ClaudeConsoleInput;if($drain.console_queue_clear -isnot [bool] -or -not $drain.console_queue_clear){throw 'Console input exceeded its clearing bound; preserve the session.'}
                Write-Host '';Write-Host 'Cancellation requested; waiting for owned Job/profile cleanup.'
            }elseif($inputState.SubmissionRequested -and -not $inputState.Submitted -and $nativePromptReady -and -not $cancelSent){
                if(Test-InstanceComplete $instance){throw 'Login ended while awaiting input; no code was written.'}
                Write-PrivatePacket $inputPath $nonce $inputState.Secret
                Complete-ClaudeCodeInput -State $inputState
                $drain=Clear-ClaudeConsoleInput;if($drain.console_queue_clear -isnot [bool] -or -not $drain.console_queue_clear){throw 'Console input exceeded its clearing bound; preserve the session.'}
                Write-Host '';Write-Host 'One line submitted. Waiting for native login exit and cleanup. Escape cancels.'
            }
            Start-Sleep -Milliseconds 100
        }
    }catch{
        try{Write-CancelRequest $root $nonce}catch{};throw
    }finally{
        Close-ClaudeCodeInput -State $inputState
        $drain=Clear-ClaudeConsoleInput;if($drain.console_queue_clear -isnot [bool] -or -not $drain.console_queue_clear){throw 'Console input exceeded its clearing bound; preserve the session.'}
    }
''' + leaf[end:]
    leaf_hash = add('login_pipeline_worker_interactive_r3_v6.ps1', leaf)
    series = old['login-six-workers-status-first-r3-v5.ps1']
    series = series.replace('login_pipeline_worker_interactive_r3_v5.ps1', 'login_pipeline_worker_interactive_r3_v6.ps1').replace(PINS['login_pipeline_worker_interactive_r3_v5.ps1'], leaf_hash)
    series = series.replace('claude-session-history-v5.ps1', 'claude-session-history-v6.ps1').replace(OLD_HISTORY, history)
    series_hash = add('login-six-workers-status-first-r3-v6.ps1', series)
    outer = old['authenticate-native-profiles-status-first-r3-v5.ps1']
    outer = outer.replace('login_pipeline_worker_interactive_r3_v5.ps1', 'login_pipeline_worker_interactive_r3_v6.ps1').replace(PINS['login_pipeline_worker_interactive_r3_v5.ps1'], leaf_hash)
    outer = outer.replace('login-six-workers-status-first-r3-v5.ps1', 'login-six-workers-status-first-r3-v6.ps1').replace(PINS['login-six-workers-status-first-r3-v5.ps1'], series_hash)
    outer_hash = add('authenticate-native-profiles-status-first-r3-v6.ps1', outer)
    top = old['run-pipeline-commissioning-r3-v5.ps1']
    for before, after, pin in (
        ('login_pipeline_worker_interactive_r3_v5.ps1', 'login_pipeline_worker_interactive_r3_v6.ps1', leaf_hash),
        ('login-six-workers-status-first-r3-v5.ps1', 'login-six-workers-status-first-r3-v6.ps1', series_hash),
        ('authenticate-native-profiles-status-first-r3-v5.ps1', 'authenticate-native-profiles-status-first-r3-v6.ps1', outer_hash),
    ):
        top = top.replace(before, after).replace(PINS[before], pin)
    top_hash = add('run-pipeline-commissioning-r3-v6.ps1', top)
    batch = old['run-post-commissioning-setup-r3-v2.ps1']
    batch = batch.replace('run-pipeline-commissioning-r3-v5.ps1', 'run-pipeline-commissioning-r3-v6.ps1').replace(PINS['run-pipeline-commissioning-r3-v5.ps1'], top_hash)
    batch = once(batch, 'PostCommissioningSetup4.2.7-windows-20261008-r3-v2', 'PostCommissioningSetup4.2.7-windows-20261008-r3-v3')
    batch_hash = add('run-post-commissioning-setup-r3-v3.ps1', batch)
    launcher = old['run-reboot-setup-r3-v5.ps1']
    launcher = launcher.replace('run-pipeline-commissioning-r3-v5.ps1', 'run-pipeline-commissioning-r3-v6.ps1').replace(PINS['run-pipeline-commissioning-r3-v5.ps1'], top_hash)
    launcher = launcher.replace('run-post-commissioning-setup-r3-v2.ps1', 'run-post-commissioning-setup-r3-v3.ps1').replace(PINS['run-post-commissioning-setup-r3-v2.ps1'], batch_hash)
    add('run-reboot-setup-r3-v6.ps1', launcher)
    return outputs


def main():
    outputs = render()
    if any((W / name).exists() or (W / name).is_symlink() for name in outputs):
        raise FileExistsError('Fresh continuation outputs already exist; preserve them')
    for name, raw in outputs.items():
        with (W / name).open('xb') as stream:
            stream.write(raw)
        print(name, hashlib.sha256(raw).hexdigest())


if __name__ == '__main__':
    main()
