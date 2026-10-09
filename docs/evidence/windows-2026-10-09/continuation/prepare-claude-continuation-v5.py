"""Create additive Claude history-review successors from frozen v4 wrappers."""
import hashlib
from pathlib import Path

W = Path(__file__).resolve().parent
PINS = {
    "login_pipeline_worker_interactive_r3_v4.ps1": "6f2181eb8e5ba5dc701ee9d69b496de01ffa5d7d74880bfa96e702dbd2e5c150",
    "login-six-workers-status-first-r3-v4.ps1": "c908a3b2ece9858e23b023cab0557cab5ccadd03c3efc0460317bd62a1598bdb",
    "authenticate-native-profiles-status-first-r3-v4.ps1": "724c6ac99b4bbfa41a8da2c45892ee0efaa46deed458359194f308d4ee566dca",
    "run-pipeline-commissioning-r3-v4.ps1": "fdd8f5fcacae85f2d80cb908d87d6fdad0b06c0783d2ff20fa2d59a458c4939d",
    "commission-first-warden-r3-v4.ps1": "6e8acd55bdc3a66a78846b8db5601f07baa9b84677197cffb7b1f86721bd720b",
}
GUARD = "claude-session-history-v5.ps1"
EXPORTS = (
    "Test-ClaudeHistoryInteger", "Get-ClaudeHistoryValue",
    "Get-ClaudeHistorySafeReceiptMetadata", "Assert-ClaudeHistoryPrivateAcl",
    "Assert-ClaudeHistoryPrivateRoot", "Assert-ClaudeHistoryTask",
    "Assert-ClaudeHistoryReceipt", "Assert-ReviewedClaudeHistory",
)


def digest(name):
    return hashlib.sha256((W / name).read_bytes()).hexdigest()


def frozen(name):
    assert digest(name) == PINS[name], name
    return (W / name).read_text(encoding="utf-8-sig")


def once(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new)


def create(name, text):
    with (W / name).open("x", encoding="utf-8", newline="\n") as output:
        output.write(text)
    print(name, digest(name))


def main():
    # Validate the complete input closure and all fresh destinations before the
    # first write, so a not-yet-frozen reboot helper cannot leave half a chain.
    for name in PINS:
        frozen(name)
    reboot_source = W / "commission-first-warden-r3-v5.py"
    compile(reboot_source.read_bytes(), str(reboot_source), "exec")
    for name in (
        "login_pipeline_worker_interactive_r3_v5.ps1",
        "login-six-workers-status-first-r3-v5.ps1",
        "authenticate-native-profiles-status-first-r3-v5.ps1",
        "commission-first-warden-r3-v5.ps1",
        "run-pipeline-commissioning-r3-v5.ps1",
    ):
        assert not (W / name).exists(), name
    guard_hash = digest(GUARD)
    exports = "@(" + ",".join("'" + name + "'" for name in EXPORTS) + ")"
    old_leaf = "login_pipeline_worker_interactive_r3_v4.ps1"
    new_leaf = "login_pipeline_worker_interactive_r3_v5.ps1"
    text = frozen(old_leaf)
    marker = "Initialize-FileIdentity\n$inspectionPath="
    loader = (
        "Initialize-FileIdentity\n"
        "$historyPath=Join-Path $PSScriptRoot '" + GUARD + "'\n"
        "foreach($definition in @(Import-PinnedFunctions $historyPath '" + guard_hash + "' " + exports + ")){. ([scriptblock]::Create($definition))}\n"
        "$held.Add((Open-VerifiedFile $historyPath '" + guard_hash + "' (Get-Item -LiteralPath $historyPath).Length))\n"
        "$inspectionPath="
    )
    text = once(text, marker, loader)
    old = (
        '    $previous=@(Get-ChildItem -LiteralPath \'C:\\Program Files\\CoChem\' -Directory -Filter "InteractiveClaude427-r3-$Slot-*" -ErrorAction Stop)\n'
        '    $previous+=@(Get-ChildItem -LiteralPath \'C:\\Program Files\\CoChem\' -Directory -Filter "InteractiveClaude427-$Slot-*" -ErrorAction Stop)\n'
        "    if($previous.Count){$holds+='An earlier login session exists for this slot; preserve and review it before another login.'}\n"
    )
    new = (
        "    # Review immutable earlier sessions; never reopen their input/output channels.\n"
        "    if($Apply){$null=@(Assert-ReviewedClaudeHistory -Slot $Slot -Folder $folder -Runtime $runtime)}\n"
        "    else{\n"
        '        $previous=@(Get-ChildItem -LiteralPath \'C:\\Program Files\\CoChem\' -Directory -Filter "InteractiveClaude427-r3-$Slot-*" -ErrorAction Stop)\n'
        '        $previous+=@(Get-ChildItem -LiteralPath \'C:\\Program Files\\CoChem\' -Directory -Filter "InteractiveClaude427-$Slot-*" -ErrorAction Stop)\n'
        "        if($previous.Count){$holds+='Earlier Claude sessions require protected terminal-receipt review in Administrator Apply before a new login.'}\n"
        "    }\n"
    )
    text = once(text, old, new)
    create(new_leaf, text)

    old_series = "login-six-workers-status-first-r3-v4.ps1"
    new_series = "login-six-workers-status-first-r3-v5.ps1"
    text = frozen(old_series).replace(old_leaf, new_leaf).replace(PINS[old_leaf], digest(new_leaf))
    start = text.index("function Assert-FreshClaudeLogin {")
    end = text.index("function Get-SeriesWorkerRootHolds {", start)
    text = text[:start] + (
        "function Assert-FreshClaudeLogin {\n"
        " param([string]$Slot)\n"
        " $null=@(Assert-ReviewedClaudeHistory -Slot $Slot -Folder $folder -Runtime $runtime)\n"
        "}\n"
    ) + text[end:]
    marker = " $runtime=Assert-R3InstalledBindings"
    loader = (
        " foreach($d in @(Import-SeriesFunctions (Join-Path $PSScriptRoot '" + GUARD + "') '" + guard_hash + "' " + exports + ")){. ([scriptblock]::Create($d))}\n"
    )
    text = once(text, marker, loader + marker)
    text = once(text, "  Confirm-AttendedLoginReady $slot $Provider", (
        "  # Review history before consuming owner readiness or starting a login.\n"
        "  if($Provider -ceq 'claude'){Assert-FreshClaudeLogin $slot}\n"
        "  Confirm-AttendedLoginReady $slot $Provider"
    ))
    create(new_series, text)

    old_outer = "authenticate-native-profiles-status-first-r3-v4.ps1"
    new_outer = "authenticate-native-profiles-status-first-r3-v5.ps1"
    text = frozen(old_outer).replace(old_leaf, new_leaf).replace(PINS[old_leaf], digest(new_leaf))
    text = text.replace(old_series, new_series).replace(PINS[old_series], digest(new_series))
    create(new_outer, text)

    old_start = "commission-first-warden-r3-v4.ps1"
    new_start = "commission-first-warden-r3-v5.ps1"
    text = frozen(old_start)
    text = once(text, "commission-first-warden-r3-v3.py", "commission-first-warden-r3-v5.py")
    text = once(text, "9770007a8c658a73e68ccc0c370eeaab2cc6568cf20cd009601e8c35c25de755", digest("commission-first-warden-r3-v5.py"))
    text = once(text,
        "empty six SSD/five RAM roots, exact four-file slot1 Docker fixture privately preserved before normal daemon cleanup, closed registry, Defender/RAM attestation, free loopback port",
        "empty six SSD roots; current RAM roots with exact preserved fixture, or verified reboot adoption with absent dedicated subtree, durable prior-ledger capture/intent and six empty RAM roots; closed registry, Defender/RAM attestation and free loopback port; existing R: drive and startup task retained")
    create(new_start, text)

    text = frozen("run-pipeline-commissioning-r3-v4.ps1")
    for old, new in ((old_leaf, new_leaf), (old_series, new_series), (old_outer, new_outer)):
        text = text.replace(old, new).replace(PINS[old], digest(new))
    text = text.replace(old_start, new_start).replace(PINS[old_start], digest(new_start))
    create("run-pipeline-commissioning-r3-v5.ps1", text)


if __name__ == "__main__":
    main()
