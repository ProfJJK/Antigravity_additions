#Requires -Version 5.1
<# Read-only validation. A protected service descriptor keeps the v7 private
   ACE policy. The observed unprotected three-ACE Scheduler projection is
   accepted only with independent, held-handle proof that its root task file
   retains a protected SYSTEM/Administrators full-control DACL. #>

function Assert-RegisteredTaskAcl {
    param([string]$Sddl,[string]$TaskName='')
    $sd=[Security.AccessControl.RawSecurityDescriptor]::new($Sddl)
    if($null -eq $sd.Owner -or $sd.Owner.Value -notin @('S-1-5-18','S-1-5-32-544') -or
       $null -eq $sd.DiscretionaryAcl -or $sd.DiscretionaryAcl.Count -notin @(2,3)){
        throw 'Registered task ownership/private DACL differs.'
    }
    $seen=[Collections.Generic.HashSet[string]]::new();$systemRead=$false
    foreach($ace in $sd.DiscretionaryAcl){
        if($ace.AceType -ne [Security.AccessControl.AceType]::AccessAllowed -or
           $ace.AceFlags -ne [Security.AccessControl.AceFlags]::None){throw 'Registered task ACL grants differ.'}
        $sid=$ace.SecurityIdentifier.Value
        if($ace.AccessMask -eq 2032127 -and $sid -in @('S-1-5-18','S-1-5-32-544')){
            if(-not $seen.Add($sid)){throw 'Registered task ACL grants differ.'}
        }elseif($ace.AccessMask -eq 1179785 -and $sid -ceq 'S-1-5-18' -and -not $systemRead){
            $systemRead=$true
        }else{throw 'Registered task ACL grants differ.'}
    }
    if($seen.Count -ne 2 -or -not $seen.Contains('S-1-5-18') -or -not $seen.Contains('S-1-5-32-544')){
        throw 'Registered task ACL grants differ.'
    }
    if($sd.ControlFlags -band [Security.AccessControl.ControlFlags]::DiscretionaryAclProtected){return}
    if($sd.DiscretionaryAcl.Count -ne 3 -or -not $systemRead){throw 'Unprotected task descriptor is not the reviewed Scheduler projection.'}
    if($TaskName.Length -gt 240 -or $TaskName -cnotmatch '\ACoChem-4\.2\.7-[A-Za-z0-9][A-Za-z0-9_.-]*\z' -or
       $TaskName.Contains('..') -or $TaskName.EndsWith('.')){throw 'Scheduler projection requires an exact bound root task name.'}
    $path=[IO.Path]::Combine('C:\Windows\System32\Tasks',$TaskName)
    $item=Get-Item -LiteralPath $path -Force -ErrorAction Stop
    if($item.PSIsContainer -isnot [bool] -or $item.PSIsContainer -or
       ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
       ($item.Attributes -band [IO.FileAttributes]::Directory) -or
       $item.Length -lt 1 -or $item.Length -gt 131072){throw 'Task backing file is not an ordinary bounded file.'}
    $hash=Get-FileHash -LiteralPath $path -Algorithm SHA256 -ErrorAction Stop
    if($hash.Hash -isnot [string] -or $hash.Hash -cnotmatch '\A[a-fA-F0-9]{64}\z'){throw 'Task backing file digest is invalid.'}
    $stream=$null
    try{
        # Imported pinned Open-VerifiedFile checks every ancestor, the exact
        # resolved handle identity, one link, length and digest, and denies
        # replacement/writes while the independent ACL witness is collected.
        $stream=Open-VerifiedFile $path $hash.Hash.ToLowerInvariant() ([long]$item.Length)
        if($null -eq $stream){throw 'Task backing file custody is missing.'}
        $acl=Get-Acl -LiteralPath $path -ErrorAction Stop
        $sections=[Security.AccessControl.AccessControlSections]::Owner -bor [Security.AccessControl.AccessControlSections]::Access
        $backing=[Security.AccessControl.RawSecurityDescriptor]::new($acl.GetSecurityDescriptorSddlForm($sections))
        if($null -eq $backing.Owner -or $backing.Owner.Value -notin @('S-1-5-18','S-1-5-32-544') -or
           -not ($backing.ControlFlags -band [Security.AccessControl.ControlFlags]::DiscretionaryAclProtected) -or
           $null -eq $backing.DiscretionaryAcl -or $backing.DiscretionaryAcl.Count -ne 2){throw 'Task backing file ownership/private DACL differs.'}
        $backingSeen=[Collections.Generic.HashSet[string]]::new()
        foreach($ace in $backing.DiscretionaryAcl){
            if($ace.AceType -ne [Security.AccessControl.AceType]::AccessAllowed -or
               $ace.AceFlags -ne [Security.AccessControl.AceFlags]::None -or $ace.AccessMask -ne 2032127 -or
               $ace.SecurityIdentifier.Value -notin @('S-1-5-18','S-1-5-32-544') -or
               -not $backingSeen.Add($ace.SecurityIdentifier.Value)){throw 'Task backing file ACL grants differ.'}
        }
    }finally{if($null -ne $stream){$stream.Dispose()}}
}
