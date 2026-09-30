"""DomainRouter dispatch engine for the CoChem V4.1.2 Domain-Specific Pipelines.

SRS-412-07 (Chapter 7) defines three canonical DSP subsystems:

* ``code_forge``      - software engineering, TDD loops, Scientific Summit
                        arbitration, simulation back-ends (PySCF / ASE).
* ``academic_press``  - manuscript drafting, citation verification, peer
                        review and LaTeX / Typst typesetting.
* ``pedagogy_engine`` - LMS synchronisation, R/exams generation, OCR and
                        FERPA-compliant grading.

This module owns the global pipeline registry, maps inbound tasks onto a
domain and drives the four-phase lifecycle contract of every
:class:`~cochem.dsp.base.IDomainPipeline`::

    route -> validate -> execute -> audit

Every failure is surfaced as a structured :class:`PipelineError` carrying
the domain and the lifecycle phase in which the failure occurred.
"""

from __future__ import annotations

import dataclasses
import importlib
import logging
import threading
import time
import types
from collections.abc import Callable, Iterable, Mapping
from typing import Any, ClassVar, TypeVar

from cochem.dsp.base import IDomainPipeline

__all__ = [
    "DomainRouter",
    "PipelineError",
    "UnroutableTaskError",
    "register_pipeline",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical domain names (SRS-412-07).
# ---------------------------------------------------------------------------
_CODE_FORGE = "code_forge"
_ACADEMIC_PRESS = "academic_press"
_PEDAGOGY_ENGINE = "pedagogy_engine"

# ---------------------------------------------------------------------------
# Global registry state.
# ---------------------------------------------------------------------------
_REGISTRY: dict[str, type[IDomainPipeline]] = {}
_REGISTRY_LOCK = threading.RLock()

# Class-level instance cache used when ``DomainRouter.get_instance`` is
# invoked on the class itself rather than on a router instance.
_CLASS_INSTANCES: dict[str, IDomainPipeline] = {}
_CLASS_CACHE_LOCK = threading.RLock()

# Modules that register the canonical orchestrators through
# ``@register_pipeline``. They are imported lazily on first demand so that
# importing the router never creates an import cycle with the orchestrators.
_BOOTSTRAP_MODULES: Mapping[str, str] = types.MappingProxyType(
    {
        _CODE_FORGE: "cochem.dsp.forge.orchestrator",
        _ACADEMIC_PRESS: "cochem.dsp.press.orchestrator",
        _PEDAGOGY_ENGINE: "cochem.dsp.pedagogy.orchestrator",
    }
)

# Payload keys, in priority order.
_STRICT_OVERRIDE_KEY = "dsp_domain"  # dedicated key: an invalid value is fatal
_SOFT_OVERRIDE_KEY = "domain"  # generic key: an invalid value falls through
_PAYLOAD_JOB_TYPE_KEYS: tuple[str, ...] = ("job_type", "type")
_PAYLOAD_TAG_KEYS: tuple[str, ...] = ("taxonomy_tags", "tags")
_ROUTING_ATTRIBUTES: tuple[str, ...] = (
    _STRICT_OVERRIDE_KEY,
    _SOFT_OVERRIDE_KEY,
    *_PAYLOAD_JOB_TYPE_KEYS,
    *_PAYLOAD_TAG_KEYS,
)

_PipelineT = TypeVar("_PipelineT", bound=IDomainPipeline)


def _build_job_type_map() -> Mapping[str, str]:
    taxonomy: dict[str, tuple[str, ...]] = {
        _CODE_FORGE: (
            "micro_code",
            "macro_audit",
            "code_forge",
            "forge",
            "code",
            "refactor",
            "test_authoring",
            "software_engineering",
            "summit",
            "simulation",
            "pyscf",
            "ase",
        ),
        _ACADEMIC_PRESS: (
            "academic_press",
            "press",
            "research",
            "manuscript",
            "peer_review",
            "typesetting",
            "latex",
            "typst",
            "paper",
        ),
        _PEDAGOGY_ENGINE: (
            "pedagogy_engine",
            "pedagogy",
            "course",
            "canvas",
            "grading",
            "exam",
            "rexams",
            "lms_sync",
            "ocr",
        ),
    }
    mapping: dict[str, str] = {}
    for domain, job_types in taxonomy.items():
        for job_type in job_types:
            if job_type in mapping and mapping[job_type] != domain:
                raise RuntimeError(
                    f"Job type '{job_type}' mapped to both '{mapping[job_type]}' and '{domain}'"
                )
            mapping[job_type] = domain
    return types.MappingProxyType(mapping)


def _normalise_token(value: Any) -> str | None:
    """Lower-case, trim and snake-case a routing token; ``None`` if unusable."""
    if not isinstance(value, str):
        return None
    token = value.strip().lower().replace("-", "_").replace(" ", "_")
    return token or None


def _is_affirmative(verdict: Any) -> bool:
    """Strict boolean check for validate()/audit() verdicts.

    Only genuine booleans (or NumPy-style boolean scalars exposing ``item()``)
    count. Truthy non-boolean objects such as non-empty strings or dicts are
    rejected so that a pipeline cannot pass a phase by accident.
    """
    if isinstance(verdict, bool):
        return verdict
    item = getattr(verdict, "item", None)
    if type(verdict).__name__ in {"bool_", "bool8"} and callable(item):
        return item() is True
    return False


# ---------------------------------------------------------------------------
# Structured exceptions.
# ---------------------------------------------------------------------------
class PipelineError(Exception):
    """Structured DSP failure carrying the domain and lifecycle phase."""

    def __init__(self, message: str, domain: str | None = None, phase: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.domain = domain
        self.phase = phase

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        return (self.__class__, (self.message, self.domain, self.phase))

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        """Serialisable representation for job_board error columns / logs."""
        cause = self.__cause__
        return {
            "error_type": type(self).__name__,
            "message": self.message,
            "domain": self.domain,
            "phase": self.phase,
            "cause_type": type(cause).__name__ if cause is not None else None,
            "cause": str(cause) if cause is not None else None,
        }


class UnroutableTaskError(PipelineError, KeyError):
    """Raised when a payload or job_type cannot resolve to a DSP domain."""

    def __init__(self, message: str, domain: str | None = "router", phase: str | None = "routing") -> None:
        super().__init__(message, domain=domain, phase=phase)

    def __str__(self) -> str:
        # KeyError.__str__ would repr()-quote the message; keep it readable.
        return self.message


# ---------------------------------------------------------------------------
# Registration decorator.
# ---------------------------------------------------------------------------
def register_pipeline(domain_name: str) -> Callable[[type[_PipelineT]], type[_PipelineT]]:
    """Register a pipeline class under ``domain_name`` and return it unchanged."""
    if not isinstance(domain_name, str) or not domain_name.strip():
        raise ValueError(f"register_pipeline requires a non-empty domain name, got {domain_name!r}")
    name = domain_name.strip()

    def _decorator(pipeline_cls: type[_PipelineT]) -> type[_PipelineT]:
        if not isinstance(pipeline_cls, type):
            raise TypeError(f"@register_pipeline('{name}') must decorate a class, got {pipeline_cls!r}")
        missing = [
            method
            for method in ("validate", "execute", "audit")
            if not callable(getattr(pipeline_cls, method, None))
        ]
        if missing:
            raise TypeError(
                f"Pipeline class {pipeline_cls.__qualname__} registered for '{name}' "
                f"lacks lifecycle methods: {', '.join(missing)}"
            )
        with _REGISTRY_LOCK:
            previous = _REGISTRY.get(name)
            if previous is not None and previous is not pipeline_cls:
                logger.warning(
                    "DSP domain '%s' re-registered: %s.%s replaces %s.%s",
                    name,
                    pipeline_cls.__module__,
                    pipeline_cls.__qualname__,
                    previous.__module__,
                    previous.__qualname__,
                )
                with _CLASS_CACHE_LOCK:
                    _CLASS_INSTANCES.pop(name, None)
            _REGISTRY[name] = pipeline_cls
        logger.debug("Registered DSP pipeline %s for domain '%s'", pipeline_cls.__qualname__, name)
        return pipeline_cls

    return _decorator


# ---------------------------------------------------------------------------
# Hybrid class/instance method descriptor.
# ---------------------------------------------------------------------------
class _HybridMethod:
    """Bind to the instance when accessed on one, otherwise to the class.

    Lets ``router.get_instance(d)`` use the router's private cache while
    ``DomainRouter.get_instance(d)`` falls back to a process-wide cache
    instead of failing with a missing-argument ``TypeError``.
    """

    def __init__(self, func: Callable[..., Any]) -> None:
        self._func = func
        self.__doc__ = func.__doc__
        self.__name__ = func.__name__
        self.__wrapped__ = func

    def __get__(self, obj: Any, objtype: type | None = None) -> types.MethodType:
        target = obj if obj is not None else objtype
        return types.MethodType(self._func, target)


# ---------------------------------------------------------------------------
# Router.
# ---------------------------------------------------------------------------
class DomainRouter:
    """Routes tasks onto DSP domains and drives the pipeline lifecycle."""

    CODE_FORGE: ClassVar[str] = _CODE_FORGE
    ACADEMIC_PRESS: ClassVar[str] = _ACADEMIC_PRESS
    PEDAGOGY_ENGINE: ClassVar[str] = _PEDAGOGY_ENGINE
    CANONICAL_DOMAINS: ClassVar[frozenset[str]] = frozenset({_CODE_FORGE, _ACADEMIC_PRESS, _PEDAGOGY_ENGINE})
    JOB_TYPE_TO_DOMAIN: ClassVar[Mapping[str, str]] = _build_job_type_map()
    LIFECYCLE_PHASES: ClassVar[tuple[str, ...]] = (
        "routing",
        "instantiation",
        "validation",
        "execution",
        "audit",
    )

    def __init__(self, pipeline_factory: Callable[[str], IDomainPipeline] | None = None) -> None:
        if pipeline_factory is not None and not callable(pipeline_factory):
            raise TypeError("pipeline_factory must be callable")
        self._pipeline_factory = pipeline_factory
        self._instances: dict[str, IDomainPipeline] = {}
        self._lock = threading.RLock()

    # -- registry ----------------------------------------------------------
    @classmethod
    def list_domains(cls) -> list[str]:
        """Sorted names of every currently registered domain."""
        with _REGISTRY_LOCK:
            return sorted(_REGISTRY.keys())

    @classmethod
    def _lookup_pipeline_class(cls, domain_name: str) -> type[IDomainPipeline]:
        with _REGISTRY_LOCK:
            pipeline_cls = _REGISTRY.get(domain_name)
        if pipeline_cls is not None:
            return pipeline_cls

        module_name = _BOOTSTRAP_MODULES.get(domain_name)
        import_failure: BaseException | None = None
        if module_name is not None:
            # Import outside the registry lock: the module's own
            # @register_pipeline call re-enters the registry.
            try:
                importlib.import_module(module_name)
            except ImportError as exc:
                import_failure = exc
                logger.error("Failed to bootstrap DSP domain '%s' from %s: %s", domain_name, module_name, exc)
            with _REGISTRY_LOCK:
                pipeline_cls = _REGISTRY.get(domain_name)
            if pipeline_cls is not None:
                return pipeline_cls

        message = f"No pipeline registered for DSP domain '{domain_name}'"
        if module_name is not None:
            message += f" (bootstrap module '{module_name}' did not register it)"
        error = KeyError(message)
        if import_failure is not None:
            raise error from import_failure
        raise error

    @classmethod
    def get_pipeline(cls, domain_name: str) -> IDomainPipeline:
        """Instantiate a fresh pipeline for ``domain_name``; ``KeyError`` if unknown."""
        if not isinstance(domain_name, str) or not domain_name:
            raise KeyError(f"Invalid DSP domain name {domain_name!r}")
        pipeline_cls = cls._lookup_pipeline_class(domain_name)
        return pipeline_cls()

    @_HybridMethod
    def get_instance(owner: Any, domain_name: str) -> IDomainPipeline:  # noqa: N805
        """Return a cached pipeline instance, creating it on first use.

        Called on a router instance the cache is private to that router (and
        honours its ``pipeline_factory``). Called on the class it uses a
        process-wide cache shared by all class-level callers.
        """
        if isinstance(owner, type):
            router_cls = owner
            cache = _CLASS_INSTANCES
            lock = _CLASS_CACHE_LOCK
            factory = None
        else:
            router_cls = type(owner)
            cache = owner._instances
            lock = owner._lock
            factory = owner._pipeline_factory

        with lock:
            cached = cache.get(domain_name)
            if cached is not None:
                return cached
            if factory is not None:
                instance = factory(domain_name)
                if instance is None:
                    raise KeyError(f"pipeline_factory returned no pipeline for DSP domain '{domain_name}'")
            else:
                instance = router_cls.get_pipeline(domain_name)
            cache[domain_name] = instance
            return instance

    # -- resolution --------------------------------------------------------
    @classmethod
    def _match_domain_token(cls, value: Any) -> str | None:
        """Map a single routing token onto a domain name, or ``None``."""
        if not isinstance(value, str):
            return None
        raw = value.strip()
        if not raw:
            return None
        with _REGISTRY_LOCK:
            if raw in _REGISTRY:
                return raw
            token = _normalise_token(raw)
            if token is not None and token in _REGISTRY:
                return token
        if token is None:
            return None
        if token in cls.CANONICAL_DOMAINS:
            return token
        return cls.JOB_TYPE_TO_DOMAIN.get(token)

    @staticmethod
    def _iter_tags(raw_tags: Any) -> list[Any]:
        if raw_tags is None:
            return []
        if isinstance(raw_tags, (str, bytes)):
            return [raw_tags.decode("utf-8", errors="replace") if isinstance(raw_tags, bytes) else raw_tags]
        if isinstance(raw_tags, Mapping):
            return list(raw_tags.keys())
        if isinstance(raw_tags, Iterable):
            return list(raw_tags)
        return [raw_tags]

    @classmethod
    def resolve_domain(cls, job_type: str | None = None, payload: Mapping[str, Any] | None = None) -> str:
        """Resolve a domain from an explicit job type and/or a payload.

        Priority: ``payload['dsp_domain']`` > ``payload['domain']`` >
        explicit ``job_type`` > ``payload['job_type'|'type']`` >
        ``payload['taxonomy_tags'|'tags']``. Raises
        :class:`UnroutableTaskError` when nothing resolves.
        """
        if payload is not None and not isinstance(payload, Mapping):
            raise UnroutableTaskError(
                f"Routing payload must be a mapping, got {type(payload).__name__}"
            )
        data: Mapping[str, Any] = payload if payload is not None else {}
        attempted: list[str] = []

        # 1. Payload domain overrides.
        strict_override = data.get(_STRICT_OVERRIDE_KEY)
        if strict_override is not None:
            domain = cls._match_domain_token(strict_override)
            if domain is None:
                raise UnroutableTaskError(
                    f"payload['{_STRICT_OVERRIDE_KEY}']={strict_override!r} does not name a "
                    f"registered or canonical DSP domain"
                )
            return domain

        soft_override = data.get(_SOFT_OVERRIDE_KEY)
        if soft_override is not None:
            domain = cls._match_domain_token(soft_override)
            if domain is not None:
                return domain
            attempted.append(f"{_SOFT_OVERRIDE_KEY}={soft_override!r}")

        # 2. Explicit job_type parameter (authoritative when supplied).
        if job_type is not None and not (isinstance(job_type, str) and not job_type.strip()):
            domain = cls._match_domain_token(job_type)
            if domain is None:
                raise UnroutableTaskError(f"Unknown job_type {job_type!r}: no DSP domain handles it")
            return domain

        # 3. Payload job type keys.
        for key in _PAYLOAD_JOB_TYPE_KEYS:
            candidate = data.get(key)
            if candidate is None:
                continue
            domain = cls._match_domain_token(candidate)
            if domain is not None:
                return domain
            attempted.append(f"{key}={candidate!r}")

        # 4. Taxonomy tags (first resolvable tag wins).
        for key in _PAYLOAD_TAG_KEYS:
            raw_tags = data.get(key)
            if raw_tags is None:
                continue
            for tag in cls._iter_tags(raw_tags):
                domain = cls._match_domain_token(tag)
                if domain is not None:
                    return domain
            attempted.append(f"{key}={raw_tags!r}")

        detail = "; ".join(attempted) if attempted else "no routing fields present"
        raise UnroutableTaskError(f"Task cannot be routed to any DSP domain ({detail})")

    @classmethod
    def _extract_routing_fields(cls, task_payload: Any) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        for attribute in _ROUTING_ATTRIBUTES:
            value = getattr(task_payload, attribute, None)
            if value is not None:
                fields[attribute] = value
        metadata = getattr(task_payload, "metadata", None)
        if isinstance(metadata, Mapping):
            for attribute in _ROUTING_ATTRIBUTES:
                if attribute not in fields and metadata.get(attribute) is not None:
                    fields[attribute] = metadata[attribute]
        return fields

    @classmethod
    def route(cls, task_payload: Mapping[str, Any] | Any, job_type: str | None = None) -> str:
        """Resolve the domain for a mapping or an attribute-bearing task object."""
        if isinstance(task_payload, Mapping):
            return cls.resolve_domain(job_type=job_type, payload=task_payload)
        if task_payload is None or isinstance(task_payload, (str, bytes, int, float, bool)):
            if job_type is not None:
                return cls.resolve_domain(job_type=job_type, payload=None)
            raise UnroutableTaskError(
                f"Task payload of type {type(task_payload).__name__} carries no routing information"
            )
        return cls.resolve_domain(job_type=job_type, payload=cls._extract_routing_fields(task_payload))

    # -- dispatch ----------------------------------------------------------
    @staticmethod
    def _coerce_payload(task_payload: Any, domain: str) -> dict[str, Any]:
        if isinstance(task_payload, dict):
            return task_payload
        if isinstance(task_payload, Mapping):
            return dict(task_payload)
        if dataclasses.is_dataclass(task_payload) and not isinstance(task_payload, type):
            return dataclasses.asdict(task_payload)
        to_dict = getattr(task_payload, "to_dict", None)
        if callable(to_dict):
            converted = to_dict()
            if isinstance(converted, Mapping):
                return dict(converted)
        if hasattr(task_payload, "__dict__"):
            return dict(vars(task_payload))
        raise PipelineError(
            f"Task payload of type {type(task_payload).__name__} cannot be converted to a dict "
            f"for domain '{domain}'",
            domain=domain,
            phase="validation",
        )

    @_HybridMethod
    def dispatch(owner: Any, task_payload: Mapping[str, Any] | Any, job_type: str | None = None) -> dict[str, Any]:  # noqa: N805
        """Run route -> validate -> execute -> audit and return the result.

        On a router instance the pipeline comes from that router's cache;
        on the class a fresh pipeline is instantiated per dispatch.
        """
        router_cls = owner if isinstance(owner, type) else type(owner)
        started = time.perf_counter()

        # Phase 1: routing (raises UnroutableTaskError itself).
        domain = router_cls.route(task_payload, job_type=job_type)

        # Phase 2: instantiation.
        try:
            if isinstance(owner, type):
                pipeline = router_cls.get_pipeline(domain)
            else:
                pipeline = owner.get_instance(domain)
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(
                f"Could not instantiate pipeline for domain '{domain}': {exc}",
                domain=domain,
                phase="instantiation",
            ) from exc

        # Phase 3: validation.
        try:
            payload = router_cls._coerce_payload(task_payload, domain)
            verdict = pipeline.validate(payload)
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(
                f"Validation failure on domain '{domain}': {exc}",
                domain=domain,
                phase="validation",
            ) from exc
        if not _is_affirmative(verdict):
            raise PipelineError(
                f"Domain '{domain}' rejected the task payload during validation (verdict={verdict!r})",
                domain=domain,
                phase="validation",
            )

        # Phase 4: execution.
        try:
            result = pipeline.execute(payload)
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(
                f"Execution failure on domain '{domain}': {exc}",
                domain=domain,
                phase="execution",
            ) from exc
        if not isinstance(result, Mapping):
            raise PipelineError(
                f"Domain '{domain}' execute() returned {type(result).__name__}, expected a dict",
                domain=domain,
                phase="execution",
            )
        if not isinstance(result, dict):
            result = dict(result)

        # Phase 5: audit.
        try:
            audit_verdict = pipeline.audit(result)
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(
                f"Audit failure on domain '{domain}': {exc}",
                domain=domain,
                phase="audit",
            ) from exc
        if not _is_affirmative(audit_verdict):
            raise PipelineError(
                f"Domain '{domain}' audit rejected the execution result (verdict={audit_verdict!r})",
                domain=domain,
                phase="audit",
            )

        logger.info(
            "DSP dispatch completed on domain '%s' in %.1f ms",
            domain,
            (time.perf_counter() - started) * 1000.0,
        )
        return result
