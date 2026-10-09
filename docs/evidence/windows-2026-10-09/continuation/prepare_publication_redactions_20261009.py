"""Redact four reviewed XML publication copies; preserve their original evidence.

Default is a read-only preview. Apply is permitted only inside the reviewed
fresh checkout and only when the copied XML still matches the pinned original.
No credentials are printed, and source test fixtures are never modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import xml.etree.ElementTree as ET


PUBLICATION_ROOT = Path(r"C:\Users\ansac\source\repos\Pipeline-Mix-Model-Concurrent")
REVIEWED = {
    "heartbeat-repairs-initial.xml": (
        "1c0ac6c0b5ab4f98f36a9e26044a4fc4a751a7a562796929fbc44fba8bf18d45", 2),
    "execution-prerequisites-r3-tests-initial.xml": (
        "7c237baa372f76717cf70c9f65835ac08fbb08c577690d511d54aaa503147e4d", 2),
    "first-start-reboot-r3-v5-tests.xml": (
        "cce9b1a4abc2091ab2347d77da24c1a6ecceac03aa61c6116ccb958f4bb92609", 2),
    "worker-native-status-draft-tests.xml": (
        "563915c6ce392ed1e6703d192936493511dd2bbfb1dc1a63593d4452a307090a", 4),
}
VALUE = re.compile(
    rb"(?P<prefix>['\"]ANTHROPIC_API_KEY['\"]\s*:\s*['\"])"
    rb"(?P<value>[^'\"\r\n]{4,})(?P<suffix>['\"])")
REDACTION = b"[REDACTED_CREDENTIAL_ENV_VALUE]"
REMAINING_SECRET = re.compile(
    rb"(?<![A-Za-z0-9_-])(?:sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{24,}|"
    rb"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|"
    rb"ya29\.[A-Za-z0-9_-]{30,}|1//[A-Za-z0-9_-]{30,})")
MAXIMUM = 16 * 1024 * 1024


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def plain(path: Path) -> None:
    for item in (path, *path.parents):
        info = item.lstat()
        if item.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("reparse_path_refused")


def bounded(path: Path) -> bytes:
    plain(path)
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if before.st_nlink != 1 or not 0 < before.st_size <= MAXIMUM:
            raise ValueError("original_size_or_link_refused")
        raw = stream.read(MAXIMUM + 1)
        after = os.fstat(stream.fileno())
    if len(raw) > MAXIMUM or (
        before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns
    ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("original_changed_during_read")
    return raw


def outcomes(raw: bytes):
    root = ET.fromstring(raw)
    # Preserve every XML node, attribute and JUnit result; only text changes.
    return [(item.tag, tuple(sorted(item.attrib.items()))) for item in root.iter()]


def redact(raw: bytes, count: int) -> bytes:
    if not re.search(rb"(?:self\s*=\s*environ\(\{|environ\(\{)", raw):
        raise ValueError("reviewed_environment_dump_missing")
    sanitized, actual = VALUE.subn(
        lambda item: item.group("prefix") + REDACTION + item.group("suffix"), raw)
    if actual != count or sanitized == raw:
        raise ValueError("reviewed_redaction_count_differs")
    if REMAINING_SECRET.search(sanitized):
        raise ValueError("additional_secret_shape_requires_review")
    if outcomes(raw) != outcomes(sanitized):
        raise ValueError("xml_outcomes_or_structure_changed")
    return sanitized


def atomic_replace(path: Path, raw: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".publication-redaction-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-file", type=Path, required=True)
    parser.add_argument("--destination-file", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        source = args.source_file.absolute()
        destination = args.destination_file.absolute()
        plain(PUBLICATION_ROOT)
        root = PUBLICATION_ROOT.resolve()
        relative = destination.relative_to(root)
        if relative.parts[:2] != ("docs", "evidence"):
            raise ValueError("destination_not_publication_evidence")
        if source.name not in REVIEWED or destination.name != source.name:
            raise ValueError("file_not_reviewed_for_redaction")
        if source.resolve().is_relative_to(root) or source.resolve() == destination.resolve():
            raise ValueError("original_source_must_remain_outside_fresh_checkout")
        original_sha, count = REVIEWED[source.name]
        original = bounded(source)
        copied = bounded(destination)
        if sha(original) != original_sha or sha(copied) != original_sha:
            raise ValueError("original_or_publication_copy_pin_differs")
        sanitized = redact(original, count)
        audit = destination.with_name(destination.name + ".redaction-audit.json")
        plain(audit.parent)
        if audit.exists() or audit.is_symlink():
            raise ValueError("preserve_existing_redaction_audit")
        record = {
            "schema": "cochem-publication-redaction/1",
            "publication_file": relative.as_posix(),
            "original_sha256": original_sha,
            "redacted_sha256": sha(sanitized),
            "redacted_value_occurrences": count,
            "reason": "A pytest failure captured a credential environment value; redact the public copy.",
            "original_source_preserved": True,
            "xml_result_attributes_and_structure_preserved": True,
        }
        if args.apply:
            if sha(bounded(source)) != original_sha or sha(bounded(destination)) != original_sha:
                raise ValueError("source_or_destination_changed_before_publication")
            atomic_replace(destination, sanitized)
            with audit.open("xb") as stream:
                stream.write((json.dumps(record, indent=2, sort_keys=True) + "\n").encode())
                stream.flush()
                os.fsync(stream.fileno())
            if sha(bounded(destination)) != record["redacted_sha256"] or sha(bounded(source)) != original_sha:
                raise ValueError("final_publication_or_source_verification_failed")
        print(json.dumps({**record, "mode": "SANITIZED_PUBLICATION_COPY" if args.apply else "READ_ONLY_PREVIEW"}))
        return 0
    except (ValueError, OSError, ET.ParseError) as error:
        # Never echo parser excerpts, source content, file data or OS messages.
        code = str(error) if isinstance(error, ValueError) and re.fullmatch(r"[a-z_]+", str(error)) else type(error).__name__
        print(json.dumps({"status": "PUBLICATION_REDACTION_HELD", "reason": code}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
