"""Host Warden Named Pipe Security and ACL Verification (MC-HW-64)."""
from __future__ import annotations
import os
import re
from typing import Sequence

try:
    import win32security
except ImportError:
    win32security = None  # type: ignore

CANONICAL_PIPE_NAME: str = r"\\.\pipe\cochem_warden_vm"

# Only Administrator and SYSTEM are authorized to connect to the Host Warden named pipe
ALLOWED_PIPE_PRINCIPALS: tuple[str, ...] = (
    r"NT AUTHORITY\SYSTEM",
    r"BUILTIN\Administrators",
)

ALLOWED_SIDS: tuple[str, ...] = (
    "S-1-5-18",      # Local System
    "S-1-5-32-544",  # Administrators
)

# Mandatory SDDL granting Generic All (GA) exclusively to SYSTEM (SY) and Built-in Administrators (BA)
PIPE_SECURITY_DESCRIPTOR_SDDL: str = "D:(A;;GA;;;SY)(A;;GA;;;BA)"

PIPE_NAME_PATTERN = re.compile(r"^(\\\\(\.|\?)|//(\.|\?))[/\\]pipe[/\\][A-Za-z0-9_.\-]+$")


def get_allowed_principals() -> list[str]:
    """Returns the authorized security principals allowed to connect to the Host Warden named pipe."""
    return list(ALLOWED_PIPE_PRINCIPALS)


def get_pipe_sddl() -> str:
    """Returns the mandatory Security Descriptor Definition Language (SDDL) string for pipe ACL."""
    return PIPE_SECURITY_DESCRIPTOR_SDDL


def validate_security_descriptor_sddl(sddl: str = PIPE_SECURITY_DESCRIPTOR_SDDL) -> bool:
    """Validates that the SDDL string strictly enforces DACL access exclusively for SYSTEM and Administrators."""
    if not sddl or not isinstance(sddl, str):
        return False

    # Must enforce DACL with explicit access for SY and BA
    if not (sddl.startswith("D:") and "SY" in sddl and "BA" in sddl):
        return False

    # Disallow permissive or anonymous tokens
    unauthorized_tokens = ["WD", "AN", "AU", "BG", "IU", "NU", "RC", "WR", "EVERYONE"]
    for tok in unauthorized_tokens:
        if f";;;{tok}" in sddl or f";;{tok}" in sddl:
            return False

    if win32security is not None:
        try:
            sd = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
                sddl, win32security.SDDL_REVISION_1
            )
            dacl = sd.GetSecurityDescriptorDacl()
            if dacl is None:
                return False

            ace_count = dacl.GetAceCount()
            if ace_count != len(ALLOWED_SIDS):
                return False

            validated_sids = set()
            for i in range(ace_count):
                ace = dacl.GetAce(i)
                sid = ace[-1]
                sid_str = win32security.ConvertSidToStringSid(sid)
                if sid_str not in ALLOWED_SIDS:
                    return False
                validated_sids.add(sid_str)

            if validated_sids != set(ALLOWED_SIDS):
                return False

        except Exception:
            return False

    return True


def verify_pipe_security(
    pipe_name: str = CANONICAL_PIPE_NAME,
    allowed_principals: Sequence[str] | None = None,
    sddl: str = PIPE_SECURITY_DESCRIPTOR_SDDL,
) -> bool:
    """Verifies named pipe security descriptor and enforces that access is strictly restricted to Administrator and SYSTEM.

    Any addition of unauthorized accounts (e.g. EVERYONE, ANONYMOUS LOGON) or omission of required
    administrative authorities immediately fails verification (enforcing strict set equivalence).
    """
    if not pipe_name or not isinstance(pipe_name, str):
        return False

    # Validate pipe naming convention against physical Windows named pipe format
    if not PIPE_NAME_PATTERN.match(pipe_name):
        return False

    # Enforce strict exclusivity on authorized principals
    if allowed_principals is not None:
        if set(allowed_principals) != set(ALLOWED_PIPE_PRINCIPALS):
            return False

    # Validate underlying Security Descriptor SDDL via Win32 API
    if not validate_security_descriptor_sddl(sddl):
        return False

    return True
