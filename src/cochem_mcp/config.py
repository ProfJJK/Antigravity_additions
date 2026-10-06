"""Explicit, native-runtime configuration; no inferred provider fallback."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    provider: str
    executable: str | None
    workspace_roots: tuple[Path, ...]
    state_dir: Path
    models: dict[str, str]
    default_model: str
    timeout_seconds: int = 1800
    max_workers: int = 1
    max_pending: int = 16
    allowed_tools: tuple[str, ...] = ()

    def workspace(self, value: str) -> Path:
        path = Path(value).expanduser().resolve() if value else self.workspace_roots[0]
        if not path.is_dir():
            raise ValueError(f"Workspace does not exist in this Python runtime: {path}")
        if not any(path == root or root in path.parents for root in self.workspace_roots):
            raise ValueError("Workspace must be within a configured workspace_roots directory")
        return path

    def model(self, value: str) -> str:
        value = value or self.default_model
        resolved = self.models.get(value, value)
        if resolved not in self.models.values():
            raise ValueError(f"Model must be one of the configured aliases: {', '.join(self.models)}")
        return resolved


def load_settings(filename: str, provider: str) -> Settings:
    if provider not in {"codex", "claude"}:
        raise ValueError("Provider must be codex or claude")
    path = Path(filename).expanduser().resolve()
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    section = data.get("providers", {}).get(provider)
    if not isinstance(section, dict):
        raise ValueError(f"Configuration has no providers.{provider} object")
    roots = data.get("workspace_roots")
    if not isinstance(roots, list) or not roots or not all(isinstance(x, str) and x for x in roots):
        raise ValueError("workspace_roots must be a nonempty list of absolute directories")
    if not all(Path(x).expanduser().is_absolute() for x in roots):
        raise ValueError("workspace_roots must be absolute paths in the server's OS")
    resolved_roots = tuple(Path(x).expanduser().resolve(strict=True) for x in roots)
    if not all(x.is_dir() for x in resolved_roots):
        raise ValueError("workspace_roots must contain directories")
    models = section.get("models")
    if not isinstance(models, dict) or not models or not all(
        isinstance(k, str) and k and isinstance(v, str) and v and not v.startswith("-")
        and not any(c.isspace() for c in v) for k, v in models.items()
    ):
        raise ValueError("Each provider requires a nonempty models alias-to-CLI-ID mapping")
    default = section.get("default_model")
    if default not in models:
        raise ValueError("default_model must name a configured model alias")
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else os.environ.get("XDG_STATE_HOME")
    default_state = Path(base) / "CoChem" / "mcp" if base else Path.home() / ".local" / "state" / "cochem" / "mcp"
    state = Path(data.get("state_dir", str(default_state))).expanduser()
    if not state.is_absolute():
        state = path.parent / state
    executable = section.get("executable")
    if executable is not None and (not isinstance(executable, str) or not executable.strip()):
        raise ValueError("executable must be a path/name or omitted for discovery")
    timeout = section.get("timeout_seconds", 1800)
    workers = section.get("max_workers", 1)
    pending = section.get("max_pending", 16)
    if type(timeout) is not int or not 1 <= timeout <= 14400:
        raise ValueError("timeout_seconds must be an integer between 1 and 14400")
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError("max_workers must be an integer between 1 and 4")
    if type(pending) is not int or not workers <= pending <= 100:
        raise ValueError("max_pending must be between max_workers and 100")
    allowed = section.get("allowed_tools", [])
    if (not isinstance(allowed, list) or not all(isinstance(x, str) and x and not x.startswith("-")
            and "\x00" not in x for x in allowed) or (allowed and provider != "claude")):
        raise ValueError("allowed_tools must be a list of Claude native tool permission patterns")
    return Settings(provider, executable, resolved_roots, state.resolve() / provider,
                    models, default, timeout, workers, pending, tuple(allowed))
