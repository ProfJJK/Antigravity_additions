
param([string]$PathList)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$m\$m.psd1")}
$trusted=@('S-1-5-18','S-1-5-32-544','S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
$result=@(foreach($path in (Get-Content -LiteralPath $PathList -Raw|ConvertFrom-Json)){
    $item=Get-Item -LiteralPath $path -Force;$acl=Get-Acl -LiteralPath $path
    $owner=$acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    $rules=@(foreach($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])){
        [ordered]@{sid=$rule.IdentityReference.Value;type=[string]$rule.AccessControlType;rights=[int64]$rule.FileSystemRights;inheritance=[string]$rule.InheritanceFlags;propagation=[string]$rule.PropagationFlags;inherited=$rule.IsInherited}})
    $writers=@($rules|Where-Object {$_.type -eq 'Allow' -and $_.sid -notin $trusted -and $_.propagation -notmatch 'InheritOnly' -and ($_.rights -band 0x500D0156)})
    [ordered]@{path=$path;owner_sid=$owner;trusted_owner=($owner -in $trusted);reparse=[bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint);sddl=$acl.Sddl;effective_untrusted_writers=$writers;aces=$rules}
})
ConvertTo-Json -InputObject $result -Depth 9
