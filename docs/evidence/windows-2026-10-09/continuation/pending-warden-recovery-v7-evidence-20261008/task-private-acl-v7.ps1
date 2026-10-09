#Requires -Version 5.1
<# Read-only validation only. Task Scheduler may append one SYSTEM file-read
   ACE when it registers a SYSTEM task. The two protected SYSTEM and
   Administrators full-control ACEs remain required, with no other grants,
   inheritance, callback conditions or task/descriptor mutation. #>

function Assert-RegisteredTaskAcl {
    param([string]$Sddl)
    $sd=[Security.AccessControl.RawSecurityDescriptor]::new($Sddl)
    if($null -eq $sd.Owner -or $sd.Owner.Value -notin @('S-1-5-18','S-1-5-32-544') -or
       -not ($sd.ControlFlags -band [Security.AccessControl.ControlFlags]::DiscretionaryAclProtected) -or
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
}
