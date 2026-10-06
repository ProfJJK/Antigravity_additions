"""Deployment configuration; privileged execution has no permissive fallback."""
from __future__ import annotations
from dataclasses import dataclass, field
import json
import math
import os
import re
from pathlib import Path
from typing import Any

from .routing import RoutingPolicy, SYNTHESIS_MODEL, load_routing_policy
from .hardware_guard import HardwarePolicy
from .resource_limits import ResourceLimits
from .ramdisk import RamdiskConfig
from .container_policy import DockerPolicy


def validate_subscription_probe(spec: Any) -> None:
    """Validate an operator-confirmed native subscription-status contract.

    No command syntax or provider status fields are guessed. Probe argv is
    literal and cannot interpolate prompts, workspaces, models or credentials.
    """
    if not isinstance(spec, dict):
        raise ValueError('Configure Gemini subscription_probe using a verified native status command')
    arguments = spec.get('arguments')
    if not isinstance(arguments, list) or not arguments or any(
        not isinstance(arg, str) or not arg.strip() or '\x00' in arg
        or '{' in arg or '}' in arg or 'REPLACE' in arg.upper()
        for arg in arguments
    ):
        raise ValueError('subscription_probe.arguments must contain verified literal native argv without placeholders')
    protocol = spec.get('protocol')
    if protocol == 'json-fields':
        expected = spec.get('expected')
        if not isinstance(expected, dict) or not expected:
            raise ValueError('json-fields subscription_probe requires a nonempty expected field mapping')
        for field, value in expected.items():
            if not isinstance(field, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9-]*(?:\.[A-Za-z_][A-Za-z_0-9-]*)*', field) or 'REPLACE' in field.upper():
                raise ValueError('subscription_probe expected keys must be verified dotted field paths')
            if type(value) not in (str, int, float, bool, type(None)) or (type(value) is float and not math.isfinite(value)):
                raise ValueError('subscription_probe expected values must be finite JSON scalars')
            if isinstance(value, str) and ('\x00' in value or 'REPLACE' in value.upper()):
                raise ValueError('subscription_probe expected values must be verified native values, not placeholders')
    elif protocol == 'exact-line':
        line = spec.get('success_line')
        if not isinstance(line, str) or not line.strip() or any(char in line for char in ('\r', '\n', '\x00')) or 'REPLACE' in line.upper():
            raise ValueError('exact-line subscription_probe requires one verified nonempty success_line')
    else:
        raise ValueError('subscription_probe.protocol must be json-fields or exact-line')


@dataclass(frozen=True)
class PipelineConfig:
    private_root: Path
    slot_roots: dict[str, Path]
    workers: dict[str, dict[str, str]]
    providers: dict[str, dict[str, Any]]
    rules: list[dict[str, Any]]
    token_file: Path
    operator_name: str
    port: int = 47824
    timeout_seconds: int = 1800
    lease_seconds: int = 30
    max_attempts: int = 3
    context_budget: int = 16384
    reserved_fraction: float = 0.25
    min_free_memory_mb: int = 1024
    min_free_disk_mb: int = 512
    routing: RoutingPolicy = field(default_factory=RoutingPolicy)
    hardware: HardwarePolicy = field(default_factory=HardwarePolicy)
    execution_limits: ResourceLimits = field(default_factory=ResourceLimits)
    ramdisk: RamdiskConfig = field(default_factory=RamdiskConfig)
    docker: DockerPolicy = field(default_factory=DockerPolicy)
    coding_projects: dict[str, Any] = field(default_factory=dict)
    git_executable: str = field(default_factory=lambda: r'C:\Program Files\Git\cmd\git.exe' if os.name=='nt' else 'git')

    @property
    def job_db(self) -> Path:
        return self.private_root / 'job_board.db'


def load_config(filename: str) -> PipelineConfig:
    raw = json.loads(Path(filename).read_text(encoding='utf-8-sig'))
    private = Path(raw['private_root']).expanduser()
    roots = {key: Path(value).expanduser() for key, value in raw['slot_roots'].items()}
    workers = raw['workers']
    if not private.is_absolute() or not roots or len(roots) > 64:
        raise ValueError('private_root must be absolute; configure one to 64 worker identities (at most four active)')
    if set(workers) != set(roots):
        raise ValueError('workers and slot_roots must have identical keys')
    paths = [private.resolve(), *(root.resolve() for root in roots.values())]
    if any(not root.is_absolute() for root in roots.values()):
        raise ValueError('All slot roots must be absolute paths in the Windows runtime')
    for i, left in enumerate(paths):
        if any(left == right or left in right.parents or right in left.parents for right in paths[i+1:]):
            raise ValueError('Private state and slot roots must be distinct and nonoverlapping')
    for identity in workers.values():
        if any(not isinstance(identity.get(key),str) or not identity[key].strip() or '\x00' in identity[key]
               for key in ('name','credential_target')):
            raise ValueError('Each worker requires a dedicated account name and Credential Manager target')
    if len({x['name'].casefold() for x in workers.values()}) != len(workers):
        raise ValueError('Each slot requires a distinct worker identity')
    providers = raw['providers']
    for provider in ('codex', 'claude', 'gemini'):
        spec = providers.get(provider, {})
        if any(not isinstance(spec.get(key),str) or not spec[key].strip() or '\x00' in spec[key]
               for key in ('executable','model')):
            raise ValueError(f'Configure exact executable and model for {provider}')
        allowed = spec.get('allowed_tools',[])
        if not isinstance(allowed,list) or any(not isinstance(item,str) or not item or '\x00' in item for item in allowed):
            raise ValueError('allowed_tools must be a list of nonempty tool names')
    gemini = providers['gemini']
    argv = gemini.get('arguments')
    if not isinstance(argv, list) or not argv or not all(isinstance(x,str) and '\x00' not in x for x in argv) or '{model}' not in argv:
        raise ValueError('Gemini arguments must be a verified native headless argv containing a separate {model} entry')
    if any('REPLACE_WITH' in arg for arg in argv):
        raise ValueError('Configure verified Agy headless arguments first; the example is an explicit placeholder')
    if any(re.search(r'\{[A-Za-z_][A-Za-z_0-9]*\}',arg) and arg not in ('{model}','{workspace}') for arg in argv):
        raise ValueError('Unsupported or embedded Gemini argument placeholder')
    if gemini.get('protocol') not in ('gemini-json', 'terminal-json'):
        raise ValueError('Select a supported Gemini native result protocol: gemini-json or terminal-json')
    validate_subscription_probe(gemini.get('subscription_probe'))
    from .inference_policy import gemini_inference_arguments
    inference_args = gemini_inference_arguments(gemini)
    if inference_args.count('{model}') != 1 or any(
            re.search(r'\{[A-Za-z_][A-Za-z_0-9]*\}',arg) and arg not in ('{model}','{workspace}')
            for arg in inference_args):
        raise ValueError('Gemini inference-only arguments require exactly one separate {model} entry and supported placeholders')
    # This is required by the architecture, not an inferred alias/provider swap.
    if gemini['model'] != SYNTHESIS_MODEL:
        raise ValueError(f'The SRS synthesis route requires exact native model {SYNTHESIS_MODEL}; aliases are not remapped')
    values = {}
    for key, default, low, high in [
        ('port',47824,1024,65535), ('timeout_seconds',1800,1,14400),
        ('lease_seconds',30,5,600), ('max_attempts',3,1,10),
        ('context_budget',16384,1024,1048576), ('min_free_memory_mb',1024,0,1048576),
        ('min_free_disk_mb',512,0,1048576),
    ]:
        value = raw.get(key,default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'{key} must be an integer in {low}..{high}')
        values[key] = value
    fraction = raw.get('reserved_fraction',.25)
    if type(fraction) not in (float,int) or not 0 < fraction <= 1:
        raise ValueError('reserved_fraction must be in (0,1]')
    token = Path(raw['token_file']).expanduser()
    if not token.is_absolute():
        raise ValueError('token_file must be an absolute operator-readable, worker-inaccessible path')
    if any(token.resolve()==root.resolve() or root.resolve() in token.resolve().parents for root in roots.values()):
        raise ValueError('Controller token cannot be placed in a worker slot')
    operator = raw.get('operator_name')
    if not isinstance(operator,str) or not operator.strip():
        raise ValueError('operator_name must identify the Windows user running Antigravity')
    rules = raw.get('rules',[])
    if not isinstance(rules,list):
        raise ValueError('rules must be a list of rule objects')
    from .oracle import Rule
    for rule in rules:
        try:
            if not isinstance(rule,dict):
                raise TypeError('Rule must be an object')
            Rule(**rule)
        except (TypeError,ValueError) as exc:
            raise ValueError(f'Invalid rule configuration: {exc}') from exc
    routing = load_routing_policy(raw.get('routing'))
    hardware = HardwarePolicy.from_dict(raw.get('hardware'))
    execution_limits = ResourceLimits.from_dict(raw.get('execution_limits'))
    ramdisk = RamdiskConfig.from_dict(raw.get('ramdisk'))
    docker = DockerPolicy.from_dict(raw.get('docker'))
    git_executable = raw.get('git_executable',r'C:\Program Files\Git\cmd\git.exe' if os.name=='nt' else 'git')
    if (not isinstance(git_executable,str) or not git_executable.strip() or '\x00' in git_executable
            or (os.name=='nt' and not Path(git_executable).is_absolute())):
        raise ValueError('git_executable must be one explicit absolute executable on Windows')
    projects = raw.get('coding_projects',{})
    if not isinstance(projects,dict):
        raise ValueError('coding_projects must be an operator-owned project registry')
    if projects:
        if not ramdisk.enabled or not docker.enabled:
            raise ValueError('Coding projects require verified RAM workspaces and enabled Docker testing')
        from .coding import validate_coding_projects
        projects = validate_coding_projects(projects)
        # The source registry cannot turn protected control state or another
        # worker's scratch into material provided to a coding model.
        protected = [private.resolve(), token.resolve(), *(root.resolve() for root in roots.values())]
        if ramdisk.enabled:
            protected.append(Path(ramdisk.mount_root).resolve())
        for project in projects.values():
            repository = project.repository.resolve()
            if any(repository == path or repository in path.parents or path in repository.parents
                   for path in protected):
                raise ValueError('Coding repositories must be disjoint from control state, tokens and execution workspaces')
    if ramdisk.enabled:
        mount = Path(ramdisk.mount_root).resolve()
        durable = [private.resolve(), token.resolve(), *(root.resolve() for root in roots.values())]
        if any(mount == path or mount in path.parents or path in mount.parents for path in durable):
            raise ValueError('RAM storage must be disjoint from persistent control state, tokens and identity roots')
    return PipelineConfig(private.resolve(), {k:v.resolve() for k,v in roots.items()}, workers,
                          providers, rules, token, operator, reserved_fraction=fraction, routing=routing,
                          hardware=hardware,execution_limits=execution_limits,ramdisk=ramdisk,
                          docker=docker,coding_projects=projects,git_executable=git_executable, **values)
