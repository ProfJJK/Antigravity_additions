"""Unified entrypoint for the CoChem CLI."""

import argparse
import importlib
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Tuple

import cochem
from cochem.cli.audit import run_audit
from cochem.cli.init_wizard import (
    ExistingProjectError,
    InsufficientDiskSpaceError,
    run_init_wizard,
)

DEFAULT_MIN_DISK_GB = 1.0

# Optional subcommands owned by other modules: (name, module, register attr, run attr).
# They are resolved lazily so this entrypoint has no import-time coupling to them.
_OPTIONAL_SUBCOMMANDS: Tuple[Tuple[str, str, str, str], ...] = (
    ("pes", "cochem.cli.pes", "register_pes_subparser", "run_pes_cli"),
    (
        "conformer",
        "cochem.cli.conformer",
        "register_conformer_subparser",
        "run_conformer_cli",
    ),
)


def _load_optional(module_name: str, attr: str) -> Optional[Callable[..., Any]]:
    """Import ``module_name`` on demand and return ``attr``; None if unavailable.

    A broken optional module must never take down the core init/audit entrypoint,
    so any import-time failure is treated as "not available".
    """
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return None
    return getattr(module, attr, None)


def build_parser() -> argparse.ArgumentParser:
    """Build the centralized command line argument parser."""
    parser = argparse.ArgumentParser(
        prog="cochem",
        description="CoChem CLI: Unified Autonomous Molecular Engineering Engine",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"cochem {cochem.__version__}",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )

    subparsers = parser.add_subparsers(
        dest="subcommand",
        title="subcommands",
        description="Available subcommands",
        required=False,
    )

    # init subcommand
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize a new CoChem workspace micro-silo",
        description="Scaffold micro-silos, verify hardware, and seal .cochem_project.json",
    )
    init_parser.add_argument(
        "target_dir",
        nargs="?",
        default=".",
        help="Target directory to initialize (default: current directory)",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Force reinitialization of an existing project",
    )
    init_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing workspace configuration",
    )
    init_parser.add_argument(
        "--interactive",
        action="store_true",
        help="Show the hardware/environment audit and prompt for project name and confirmation",
    )
    init_parser.add_argument(
        "--min-disk-gb",
        type=float,
        default=DEFAULT_MIN_DISK_GB,
        dest="min_disk_gb",
        help=f"Minimum free disk space in GB required to initialize (default: {DEFAULT_MIN_DISK_GB})",
    )

    # audit subcommand
    audit_parser = subparsers.add_parser(
        "audit",
        help="Run self-diagnostic anti-spoofing and integrity audit",
        description="Verify project integrity seal and perform AST anti-spoofing scan",
    )
    audit_parser.add_argument(
        "target_dir",
        nargs="?",
        default=".",
        help="Target project directory to audit (default: current directory)",
    )
    audit_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output structured JSON audit results",
    )

    # Optional subcommands, registered only when their modules are importable.
    for _name, module_name, register_attr, _run_attr in _OPTIONAL_SUBCOMMANDS:
        register = _load_optional(module_name, register_attr)
        if register is not None:
            register(subparsers)

    return parser


def build_main_parser() -> argparse.ArgumentParser:
    """Top-level parser (alias of build_parser)."""
    return build_parser()


def _handle_init(args: argparse.Namespace) -> int:
    try:
        run_init_wizard(
            target_dir=args.target_dir,
            force=args.force,
            overwrite=getattr(args, "overwrite", False),
            min_disk_gb=getattr(args, "min_disk_gb", DEFAULT_MIN_DISK_GB),
            interactive=getattr(args, "interactive", False),
        )
        if getattr(args, "verbose", False):
            print(f"Successfully initialized workspace at {args.target_dir}")
        return 0
    except ExistingProjectError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except InsufficientDiskSpaceError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Initialization failed: {exc}", file=sys.stderr)
        return 1


def _handle_audit(args: argparse.Namespace) -> int:
    target_path = Path(args.target_dir).resolve()
    result = run_audit(target_path, json_output=args.json_output)

    if args.json_output:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        if getattr(args, "verbose", False):
            print(f"Auditing workspace at {target_path}...")
        print(f"Verdict: {result.verdict} (Score: {result.score}/100)")
        for finding in result.findings:
            sev = finding.get("severity", "INFO")
            cat = finding.get("category", "GENERAL")
            msg = finding.get("message", "")
            print(f"  - [{sev}] {cat}: {msg}")

    if result.verdict == "PASS":
        return 0
    else:
        return 1


def _run_guarded(handler, args: argparse.Namespace, name: str) -> int:
    try:
        return int(handler(args))
    except KeyboardInterrupt:
        print(f"cochem {name}: interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # no uncaught tracebacks from the CLI
        logging.getLogger("cochem.cli").error("%s failed: %s", name, exc)
        print(f"cochem {name}: error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _dispatch_optional(args: argparse.Namespace) -> int:
    """Route to an optional subcommand handler, loading its module on demand."""
    for name, module_name, _register_attr, run_attr in _OPTIONAL_SUBCOMMANDS:
        if args.subcommand == name:
            handler = _load_optional(module_name, run_attr)
            if handler is None:
                return 2
            return _run_guarded(handler, args, name)
    return 2


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Central entrypoint for the CoChem CLI (exit codes: 0 ok, 1 runtime error, 2 usage)."""
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]
    argv = list(argv)

    if len(argv) == 0:
        parser.print_usage()
        return 2

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return code if isinstance(code, int) else 2

    if not args.subcommand:
        parser.print_usage()
        return 2

    if getattr(args, "verbose", False):
        logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    if args.subcommand == "init":
        return _handle_init(args)
    elif args.subcommand == "audit":
        return _handle_audit(args)
    return _dispatch_optional(args)


if __name__ == "__main__":
    sys.exit(main())
