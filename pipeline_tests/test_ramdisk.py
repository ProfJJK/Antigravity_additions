"""Real filesystem preservation plus bounded native protocol contracts.

The portable binary-decoder cases do not claim to mount Windows storage.
The opt-in native test queries a provisioned ImDisk driver and writes real RAM.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import tempfile
import sys
import uuid

import pytest

from cochem_pipeline.ramdisk import (
    DEFAULT_MOUNT, RamdiskConfig, RamdiskError, RamdiskManager, backup_mount_contents,
    imdisk_create_argv, ordinary_tree, parse_imdisk_device, parse_mount_reparse, mount_recovery_action,
    cache_junction_data, validate_cache_junction,
    validate_native_cache_selection,
)


def device_bytes(*, size_mb=1024, flags=0x1110, number=7, image_offset=0, drive=0, filename=r"\Device\AWEAlloc"):
    encoded = filename.encode("utf-16-le")
    result = bytearray(48 + len(encoded))
    struct.pack_into("<I", result, 0, number)
    struct.pack_into("<q", result, 8, size_mb * 1024 * 1024)
    struct.pack_into("<qIHH", result, 32, image_offset, flags, drive, len(encoded))
    result[48:] = encoded
    return bytes(result)


def reparse_bytes(target="\\Device\\ImDisk7\\", *, tag=0xA0000003):
    encoded = target.encode("utf-16-le")
    return struct.pack("<IHHHHHH", tag, 8 + len(encoded), 0, 0, len(encoded), 0, 0) + encoded


def test_default_preserves_adopted_directory_mount_and_physical_ram():
    config = RamdiskConfig()
    assert config.mount_root == DEFAULT_MOUNT
    assert config.backing == "awe"
    assert RamdiskConfig.from_dict(config.as_dict()) == config
    command = imdisk_create_argv(config)
    assert command[command.index("-m") + 1] == DEFAULT_MOUNT
    assert command[command.index("-t") + 1] == "file"
    assert command[command.index("-o") + 1] == "awe"
    assert "-f" not in command and "-F" not in command
    assert "/fs:ntfs" in command[-1]


@pytest.mark.parametrize("value", ["R:\\", r"D:\temp", r"\\server\share\folder", "relative", r"D:\x\..\escape",
                                   r"D:\x\bad:ads", r"D:\x\wild*card", "D:\\x\\nul\x00"])
def test_reject_drive_letters_unc_escapes_and_unsafe_mount_names(value):
    with pytest.raises(ValueError):
        RamdiskConfig(mount_root=value)


@pytest.mark.parametrize("field,value", [("enabled", 1), ("size_mb", True), ("size_mb", 0), ("size_mb", 1048577),
    ("min_free_mb", 0), ("min_free_mb", 8192), ("reserve_host_memory_mb", 0), ("backing", "file"),
    ("imdisk_executable", "imdisk.exe"), ("imdisk_executable", r"C:\Windows\other.exe")])
def test_invalid_ram_storage_settings_fail_closed(field, value):
    with pytest.raises(ValueError):
        RamdiskConfig(**{field: value})


def test_unknown_configuration_cannot_silently_weaken_mount_policy():
    with pytest.raises(ValueError, match="Unknown"):
        RamdiskConfig.from_dict({"allow_reparse_points": True})


def test_real_backup_preserves_files_and_links_without_following_them(tmp_path):
    mount = tmp_path / "tdd_runs"
    mount.mkdir()
    (mount / "important.py").write_bytes(b"original\x00bytes")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("unchanged")
    (mount / "hostile-link").symlink_to(outside, target_is_directory=True)
    backup = backup_mount_contents(mount, tmp_path)
    assert backup.parent == tmp_path and backup != mount
    assert list(mount.iterdir()) == []
    assert (backup / "important.py").read_bytes() == b"original\x00bytes"
    assert (backup / "hostile-link").is_symlink()
    assert (outside / "secret").read_text() == "unchanged"
    assert backup_mount_contents(mount, tmp_path) is None
    assert (backup / "important.py").exists()


def test_real_backup_rejects_linked_mount_and_backup_ancestor(tmp_path):
    mount = tmp_path / "mount"
    mount.mkdir()
    (mount / "keep").write_text("keep")
    linked = tmp_path / "linked"
    linked.symlink_to(mount, target_is_directory=True)
    for path, parent in [(linked, tmp_path), (mount, linked), (mount, mount)]:
        with pytest.raises(RamdiskError):
            backup_mount_contents(path, parent)
    assert (mount / "keep").read_text() == "keep"


def test_physical_paths_reject_a_symlink_in_any_ancestor(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "child").mkdir()
    link = tmp_path / "link"
    link.symlink_to(actual, target_is_directory=True)
    with pytest.raises(RamdiskError, match="reparse point or symlink"):
        ordinary_tree(link / "child")
    ordinary_tree(actual / "child")


def test_driver_query_distinguishes_nonpageable_ram_from_pageable_and_disk_files():
    awe = RamdiskConfig(size_mb=1024)
    observed = parse_imdisk_device(device_bytes(), awe, target=r"\Device\ImDisk7")
    assert observed["nonpageable"] is True
    vm = RamdiskConfig(size_mb=1024, backing="vm")
    virtual_bytes = device_bytes(flags=0x210, filename="")
    assert parse_imdisk_device(virtual_bytes, vm, target=r"\Device\ImDisk7")["nonpageable"] is False
    with pytest.raises(RamdiskError, match="backing type"):
        parse_imdisk_device(virtual_bytes, awe, target=r"\Device\ImDisk7")
    with pytest.raises(RamdiskError, match="backing type"):
        parse_imdisk_device(device_bytes(flags=0x110, filename=r"C:\disk.img"), awe, target=r"\Device\ImDisk7")


@pytest.mark.parametrize("change", [{"size_mb": 2048}, {"number": 8}, {"image_offset": 512}, {"drive": ord("R")},
                                    {"flags": 0x1111}, {"filename": r"C:\not-ram.img"}])
def test_wrong_native_volume_properties_are_not_accepted(change):
    with pytest.raises(RamdiskError):
        parse_imdisk_device(device_bytes(**change), RamdiskConfig(size_mb=1024), target=r"\Device\ImDisk7")


@pytest.mark.parametrize("data", [b"", bytes(47), device_bytes()[:49]])
def test_truncated_kernel_device_responses_fail_closed(data):
    with pytest.raises(RamdiskError):
        parse_imdisk_device(data, RamdiskConfig(size_mb=1024), target=r"\Device\ImDisk7")


def test_only_an_exact_imdisk_mount_reparse_target_is_accepted():
    assert parse_mount_reparse(reparse_bytes()) == r"\Device\ImDisk7"
    for target in (r"\??\C:\Windows", r"\Device\ImDisk7\secret", r"\Device\HarddiskVolume7", r"\Device\ImDisk7.."):
        with pytest.raises(RamdiskError):
            parse_mount_reparse(reparse_bytes(target))
    with pytest.raises(RamdiskError):
        parse_mount_reparse(reparse_bytes(tag=0xA000000C))  # symbolic link is not a mount
    with pytest.raises(RamdiskError):
        parse_mount_reparse(reparse_bytes()[:-1])


def test_selective_cache_junction_cannot_authorize_auth_home_or_another_slot():
    target = DEFAULT_MOUNT + r"\slot1\.cochem-scratch\claude-projects"
    payload = cache_junction_data(target)
    validate_cache_junction(payload, target)
    for outside in (DEFAULT_MOUNT + r"\slot2\.cochem-scratch\claude-projects", r"C:\Users\worker\.claude"):
        with pytest.raises(RamdiskError, match="own RAM slot"):
            validate_cache_junction(cache_junction_data(outside), target)
    with pytest.raises(RamdiskError):
        validate_cache_junction(payload[:-2], target)


@pytest.mark.parametrize("target", [r"\\server\share\cache", r"D:\root\..\secret", r"D:\root\cache:ads", "relative"])
def test_cache_junction_encoder_rejects_nonlocal_or_ambiguous_targets(target):
    with pytest.raises(RamdiskError):
        cache_junction_data(target)


def test_native_report_must_confirm_actual_project_cache_and_persistent_auth_directory():
    source = r"C:\Users\CoChemWorker1\.claude\projects"
    target = DEFAULT_MOUNT + r"\slot1\.cochem-scratch\claude-projects"
    parent = r"C:\Users\CoChemWorker1\.claude"
    for selected in (source, source.upper(), target):
        validate_native_cache_selection({"projectsDirectory": selected, "configDirectory": parent}, source, target)
    for result in ({}, {"CLAUDE_PROJECT_DIR": target}, {"projectsDirectory": source},
                   {"projectsDirectory": source + "-other", "configDirectory": parent},
                   {"projectsDirectory": target, "configDirectory": target},
                   {"projectsDirectory": target.replace("slot1", "slot2"), "configDirectory": parent}):
        with pytest.raises(RamdiskError, match="does not confirm"):
            validate_native_cache_selection(result, source, target)


def lifecycle_record(config, *, state="READY", boot_id=100):
    return {"schema": 1, "state": state, "mount_root": str(Path(config.mount_root)),
            "config": config.as_dict(), "boot_id": boot_id,
            **({"observed": {"target": r"\Device\ImDisk7", "device_number": 7, "volume_serial": 123}}
               if state == "READY" else {})}


def test_crash_and_reboot_recovery_preserve_exact_owned_mount_boundary():
    config = RamdiskConfig()
    target = r"\Device\ImDisk7"
    assert mount_recovery_action(None, config, 100, None) == "CREATE"
    for state in ("PREPARING", "READY"):
        prior = lifecycle_record(config, state=state)
        assert mount_recovery_action(prior, config, 100, target) == "VERIFY"
        assert mount_recovery_action(prior, config, 200, target) == "RECREATE"
        assert mount_recovery_action(prior, config, 200, None) == "CREATE"
    with pytest.raises(RamdiskError, match="ownership ledger"):
        mount_recovery_action(None, config, 100, target)
    with pytest.raises(RamdiskError, match="target changed"):
        mount_recovery_action(lifecycle_record(config), config, 200, r"\Device\ImDisk8")


@pytest.mark.parametrize("change", [{"boot_id": False}, {"boot_id": -1}, {"state": "UNKNOWN"},
    {"schema": 2}, {"mount_root": r"C:\other\mount"}, {"config": {}}, {"observed": {}},
    {"observed": {"target": r"C:\Windows", "device_number": 7, "volume_serial": 123}}])
def test_corrupt_or_reconfigured_lifecycle_records_cannot_authorize_unmount(change):
    config = RamdiskConfig()
    prior = {**lifecycle_record(config), **change}
    with pytest.raises(RamdiskError):
        mount_recovery_action(prior, config, 200, r"\Device\ImDisk7")


@pytest.mark.skipif(os.name == "nt", reason="Non-Windows fail-closed contract")
def test_no_linux_directory_can_impersonate_a_native_ram_mount(tmp_path):
    from cochem_pipeline.windows import WorkerIdentity, WindowsIsolationError
    manager = RamdiskManager(RamdiskConfig(enabled=True), tmp_path,
                            {"slot1": WorkerIdentity("RamWorker", "CoChem/test")})
    with pytest.raises(WindowsIsolationError, match="real Windows SYSTEM"):
        manager.ensure()
    with pytest.raises(RamdiskError, match="ensured"):
        manager.workspace("slot1")


@pytest.mark.parametrize("names", [["../outside"], ["slot:stream"], ["NUL"], ["slot1", "SLOT1"]])
def test_slot_names_cannot_alias_or_escape_ram_roots(tmp_path, names):
    from cochem_pipeline.windows import WorkerIdentity
    with pytest.raises(ValueError):
        RamdiskManager(RamdiskConfig(enabled=True), tmp_path,
                       {name: WorkerIdentity("RamWorker" + str(index), "CoChem/" + str(index))
                        for index, name in enumerate(names)})


def test_real_windows_imdisk_mount_acl_and_ram_io():
    if os.name != "nt" or not os.environ.get("COCHEM_WINDOWS_RAMDISK_CONFIG"):
        pytest.skip("Requires SYSTEM, real ImDisk+AWEAlloc and COCHEM_WINDOWS_RAMDISK_CONFIG")
    from cochem_pipeline.config import load_config
    from cochem_pipeline.windows import WorkerIdentity
    config = load_config(os.environ["COCHEM_WINDOWS_RAMDISK_CONFIG"])
    manager = RamdiskManager(config.ramdisk, config.private_root,
        {slot: WorkerIdentity(**identity) for slot, identity in config.workers.items()})
    evidence = manager.ensure()
    assert evidence["observed"]["filesystem"] == "NTFS"
    assert evidence["observed"]["backing"] == config.ramdisk.backing
    descriptor = manager.workspace(next(iter(config.workers)))
    descriptor.validate(identity=descriptor.identity, cwd=descriptor.root)
    payload = os.urandom(1048576)
    target = descriptor.root / ("native-ram-io-" + uuid.uuid4().hex)
    try:
        target.write_bytes(payload)
        assert target.read_bytes() == payload
        # Exercise cleanup admission against this actual verified RAM volume:
        # the stricter reserve deliberately exceeds currently measured free
        # bytes without allocating or pretending to exhaust physical memory.
        from dataclasses import replace
        import shutil
        from cochem_pipeline.ramdisk import RamdiskCapacityError
        measured_free = shutil.disk_usage(descriptor.root).free
        reserve = measured_free // 1048576 + 1
        assert reserve < descriptor.config.size_mb
        constrained = replace(descriptor, config=replace(descriptor.config,min_free_mb=reserve))
        with pytest.raises(RamdiskCapacityError):
            constrained.validate(identity=descriptor.identity)
        cleanup_proof = constrained.validate(identity=descriptor.identity,require_capacity=False)
        assert cleanup_proof['volume_serial'] == descriptor.volume_serial
        env = descriptor.environment()
        assert all(Path(value).is_dir() for value in env.values())
        assert set(env).isdisjoint({"HOME", "USERPROFILE", "CODEX_HOME", "CLAUDE_CONFIG_DIR"})
    finally:
        target.unlink(missing_ok=True)


def test_real_windows_worker_writes_through_selective_cache_and_keeps_auth_home_physical():
    if os.name != "nt" or not os.environ.get("COCHEM_WINDOWS_RAMDISK_CONFIG"):
        pytest.skip("Requires SYSTEM, real worker logons and COCHEM_WINDOWS_RAMDISK_CONFIG")
    from cochem_pipeline.config import load_config
    from cochem_pipeline.windows import WorkerIdentity, launch_worker
    config = load_config(os.environ["COCHEM_WINDOWS_RAMDISK_CONFIG"])
    identities = {slot: WorkerIdentity(**identity) for slot, identity in config.workers.items()}
    manager = RamdiskManager(config.ramdisk, config.private_root, identities)
    manager.inspect()
    slot = next(iter(identities))
    descriptor = manager.workspace(slot)
    name = "native-worker-cache-proof-" + uuid.uuid4().hex
    logdir = config.private_root / name
    logdir.mkdir()
    script = ('import json,os,pathlib; '
              'profile=pathlib.Path(os.environ["USERPROFILE"]); '
              'cache=profile/".claude"/"projects"; '
              'cache.joinpath(' + repr(name) + ').write_text("actual-worker-write"); '
              'print(json.dumps({"profile":str(profile),"cache":str(cache),"temp":os.environ["TEMP"],'
              '"home":os.environ["HOME"],"node_options":os.environ["NODE_OPTIONS"],'
              '"credential_redirected": "CLAUDE_CONFIG_DIR" in os.environ or "CODEX_HOME" in os.environ}))')
    try:
        with tempfile.TemporaryFile("w+b") as stdin:
            with launch_worker(identities[slot], [sys.executable, "-I", "-c", script], descriptor.root,
                               stdin, logdir / "out", logdir / "err", limits=config.execution_limits,
                               ramdisk_workspace=descriptor) as process:
                assert process.wait(30) == 0
        result = json.loads((logdir / "out").read_text(encoding="utf-8"))
        assert result["credential_redirected"] is False
        assert result["home"] == result["profile"]
        ordinary_tree(Path(result["profile"]) / ".claude")
        assert Path(result["temp"]) == descriptor.scratch / "temp"
        assert (descriptor.scratch / "claude-projects" / name).read_text() == "actual-worker-write"
        report = descriptor.cache_write_observation()
        assert report["observed_project_cache_files"] >= 1
        assert report["physical_ram_write_verified"] is True
        assert report["auth_directory_relocated"] is False
        assert "--max-old-space-size=512" in result["node_options"]
    finally:
        (descriptor.scratch / "claude-projects" / name).unlink(missing_ok=True)
        for path in logdir.iterdir():
            path.unlink()
        logdir.rmdir()


def test_real_windows_installed_claude_selects_the_bound_project_cache_without_inference():
    if os.name != "nt" or not os.environ.get("COCHEM_WINDOWS_RAMDISK_CONFIG"):
        pytest.skip("Requires SYSTEM, installed native Claude and COCHEM_WINDOWS_RAMDISK_CONFIG")
    from cochem_pipeline.config import load_config
    from cochem_pipeline.windows import WorkerIdentity, launch_worker
    from cochem_mcp.providers import executable_prefix
    config = load_config(os.environ["COCHEM_WINDOWS_RAMDISK_CONFIG"])
    identities = {slot: WorkerIdentity(**identity) for slot, identity in config.workers.items()}
    manager = RamdiskManager(config.ramdisk, config.private_root, identities)
    manager.inspect()
    slot = next(iter(identities))
    descriptor = manager.workspace(slot)
    logdir = config.private_root / ("native-cache-auth-check-" + uuid.uuid4().hex)
    logdir.mkdir()
    argv = executable_prefix("claude", config.providers["claude"]["executable"])
    argv += ["--setting-sources", "", "auth", "status", "--json"]
    try:
        with tempfile.TemporaryFile("w+b") as stdin:
            with launch_worker(identities[slot], argv, descriptor.root, stdin, logdir / "out", logdir / "err",
                               limits=config.execution_limits, ramdisk_workspace=descriptor) as process:
                assert process.wait(30) in (0, 1)  # Login status may be false; no inference/login is initiated.
        native = json.loads((logdir / "out").read_text(encoding="utf-8"))
        descriptor.validate_native_cache_report(native)
        assert descriptor.cache_binding_report()["native_report_verified"] is True
    finally:
        for path in logdir.iterdir():
            path.unlink()
        logdir.rmdir()
