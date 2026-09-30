"""DSP Domain Router and Dynamic Pipeline Registry (MC-DSP-03, MC-DSP-04)."""
from __future__ import annotations

import logging
from typing import Any, Callable, Type
from cochem.dsp.base import IDomainPipeline

logger = logging.getLogger("cochem.dsp.router")

_REGISTRY: dict[str, Type[IDomainPipeline]] = {}


def register_pipeline(domain_name: str) -> Callable[[Type[IDomainPipeline]], Type[IDomainPipeline]]:
    """Decorator to register a domain pipeline class supporting runtime plugin discovery."""
    def decorator(cls: Type[IDomainPipeline]) -> Type[IDomainPipeline]:
        _REGISTRY[domain_name] = cls
        return cls
    return decorator


class PipelineError(Exception):
    """Base exception for pipeline routing and execution errors."""

    def __init__(self, message: str, domain: str | None = None, phase: str | None = None) -> None:
        super().__init__(message)
        self.domain = domain
        self.phase = phase


class UnroutableTaskError(PipelineError, KeyError):
    """Raised when a task cannot be routed to any registered domain pipeline."""


class DomainRouter:
    """Dispatches tasks to the appropriate domain pipeline by domain_name or job_type.

    Routes workloads across the three canonical CoChem DSP subsystems:
    1. The Code Forge (code_forge) - Software engineering and Scientific Summit TDD loops.
    2. The Academic Press (academic_press) - Scientific manuscript authoring and typesetting.
    3. The Pedagogy Engine (pedagogy_engine) - LMS integration, didactic grading, and exam generation.
    """

    CODE_FORGE: str = "code_forge"
    ACADEMIC_PRESS: str = "academic_press"
    PEDAGOGY_ENGINE: str = "pedagogy_engine"

    CANONICAL_DOMAINS: frozenset[str] = frozenset({
        CODE_FORGE,
        ACADEMIC_PRESS,
        PEDAGOGY_ENGINE,
    })

    # Standard job_type mappings to canonical DSP domains
    JOB_TYPE_TO_DOMAIN: dict[str, str] = {
        # Code Forge mappings
        "micro_code": CODE_FORGE,
        "macro_audit": CODE_FORGE,
        "code_forge": CODE_FORGE,
        "forge": CODE_FORGE,
        "code": CODE_FORGE,
        "refactor": CODE_FORGE,
        "test_authoring": CODE_FORGE,
        "software_engineering": CODE_FORGE,
        "summit": CODE_FORGE,
        "simulation": CODE_FORGE,
        "pyscf": CODE_FORGE,
        "ase": CODE_FORGE,

        # Academic Press mappings
        "academic_press": ACADEMIC_PRESS,
        "press": ACADEMIC_PRESS,
        "research": ACADEMIC_PRESS,
        "manuscript": ACADEMIC_PRESS,
        "peer_review": ACADEMIC_PRESS,
        "typesetting": ACADEMIC_PRESS,
        "latex": ACADEMIC_PRESS,
        "typst": ACADEMIC_PRESS,
        "paper": ACADEMIC_PRESS,

        # Pedagogy Engine mappings
        "pedagogy_engine": PEDAGOGY_ENGINE,
        "pedagogy": PEDAGOGY_ENGINE,
        "course": PEDAGOGY_ENGINE,
        "canvas": PEDAGOGY_ENGINE,
        "grading": PEDAGOGY_ENGINE,
        "exam": PEDAGOGY_ENGINE,
        "rexams": PEDAGOGY_ENGINE,
        "lms_sync": PEDAGOGY_ENGINE,
        "ocr": PEDAGOGY_ENGINE,
    }

    def __init__(
        self,
        registry: dict[str, Type[IDomainPipeline]] | None = None,
        pipeline_factory: Callable[[str], IDomainPipeline] | None = None,
    ) -> None:
        self._registry: dict[str, Type[IDomainPipeline]] = registry if registry is not None else _REGISTRY
        self._pipeline_factory = pipeline_factory
        self._instances: dict[str, IDomainPipeline] = {}

    def get_instance(self, domain_name: str) -> IDomainPipeline:
        """Retrieves or creates a cached pipeline instance for the given domain."""
        if domain_name in self._instances and domain_name in self._registry:
            return self._instances[domain_name]

        if self._pipeline_factory is not None:
            pipeline = self._pipeline_factory(domain_name)
            self._instances[domain_name] = pipeline
            return pipeline

        if domain_name not in self._registry:
            raise KeyError(f"Domain pipeline '{domain_name}' not registered")

        pipeline = self._registry[domain_name]()
        self._instances[domain_name] = pipeline
        return pipeline

    @classmethod
    def get_pipeline(cls, domain_name: str) -> IDomainPipeline:
        """Retrieves and instantiates the domain pipeline registered under domain_name."""
        if domain_name not in _REGISTRY:
            raise KeyError(f"Domain pipeline '{domain_name}' not registered")
        return _REGISTRY[domain_name]()

    @classmethod
    def list_domains(cls) -> list[str]:
        """Lists all registered domain names in alphabetical order."""
        return sorted(list(_REGISTRY.keys()))

    @classmethod
    def resolve_domain(
        cls,
        job_type: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        """Resolves the owning DSP domain name from explicit payload fields or job_type."""
        # 1. Check explicit payload overrides
        if payload is not None:
            for key in ("dsp_domain", "domain"):
                value = payload.get(key)
                if isinstance(value, str):
                    clean_val = value.strip().lower()
                    if clean_val in _REGISTRY or clean_val in cls.CANONICAL_DOMAINS:
                        return clean_val

        # 2. Check explicit job_type parameter
        if job_type is not None:
            clean_jt = str(job_type).strip().lower()
            if clean_jt in cls.JOB_TYPE_TO_DOMAIN:
                return cls.JOB_TYPE_TO_DOMAIN[clean_jt]
            if clean_jt in _REGISTRY:
                return clean_jt

        # 3. Check job_type / type fields inside payload
        if payload is not None:
            for key in ("job_type", "type"):
                val = payload.get(key)
                if val is not None:
                    clean_val = str(val).strip().lower()
                    if clean_val in cls.JOB_TYPE_TO_DOMAIN:
                        return cls.JOB_TYPE_TO_DOMAIN[clean_val]
                    if clean_val in _REGISTRY:
                        return clean_val

            # 4. Check tags in payload
            tags = payload.get("tags") or payload.get("taxonomy_tags") or []
            if isinstance(tags, (list, tuple, set)):
                for tag in tags:
                    clean_tag = str(tag).strip().lower()
                    if clean_tag in cls.JOB_TYPE_TO_DOMAIN:
                        return cls.JOB_TYPE_TO_DOMAIN[clean_tag]
                    if clean_tag in _REGISTRY:
                        return clean_tag

        raise UnroutableTaskError(
            f"No registered domain pipeline matches job_type={job_type!r}, payload={payload}",
            domain="router",
            phase="routing",
        )

    @classmethod
    def route(
        cls,
        task_payload: dict[str, Any] | Any,
        job_type: str | None = None,
    ) -> str:
        """Resolves the target domain name for a given task payload or raises UnroutableTaskError."""
        if isinstance(task_payload, dict):
            jt = job_type or task_payload.get("job_type") or task_payload.get("type")
            return cls.resolve_domain(job_type=jt, payload=task_payload)

        # Support payload objects carrying attributes
        if hasattr(task_payload, "job_type") or hasattr(task_payload, "taxonomy_tags"):
            jt = job_type or getattr(task_payload, "job_type", None)
            if jt is not None:
                clean_jt = str(jt).strip().lower()
                if clean_jt in cls.JOB_TYPE_TO_DOMAIN:
                    return cls.JOB_TYPE_TO_DOMAIN[clean_jt]
            tags = getattr(task_payload, "taxonomy_tags", None) or []
            for tag in tags:
                clean_tag = str(tag).strip().lower()
                if clean_tag in cls.JOB_TYPE_TO_DOMAIN:
                    return cls.JOB_TYPE_TO_DOMAIN[clean_tag]
                if clean_tag in _REGISTRY:
                    return clean_tag

        raise UnroutableTaskError(
            f"Unsupported task payload or unroutable tags: {type(task_payload)}",
            domain="router",
            phase="routing",
        )

    @classmethod
    def dispatch(
        cls,
        task_payload: dict[str, Any],
        job_type: str | None = None,
    ) -> dict[str, Any]:
        """Dispatches a task to Forge, Press, or Pedagogy by job_type and executes its lifecycle."""
        domain = cls.route(task_payload, job_type=job_type)
        if isinstance(task_payload, dict):
            task_id = str(task_payload.get("task_id", "unspecified_task"))
        else:
            task_id = str(getattr(task_payload, "task_id", "unspecified_task"))
        logger.info("Dispatching task '%s' (job_type=%s) to domain '%s'", task_id, job_type, domain)

        try:
            pipeline = cls.get_pipeline(domain)
        except Exception as exc:
            logger.error("Failed to acquire pipeline for domain '%s' (task '%s'): %s", domain, task_id, exc)
            raise PipelineError(
                f"Failed to acquire pipeline for domain '{domain}': {exc}",
                domain=domain,
                phase="instantiation",
            ) from exc

        try:
            is_valid = pipeline.validate(task_payload)
            if not is_valid:
                err_msg = f"Pipeline '{domain}' validation rejected payload for task '{task_id}'"
                logger.error(err_msg)
                raise PipelineError(err_msg, domain=domain, phase="validation")
        except PipelineError:
            raise
        except Exception as exc:
            logger.error("Validation error on domain '%s' for task '%s': %s", domain, task_id, exc)
            raise PipelineError(
                f"Validation failure on domain '{domain}': {exc}",
                domain=domain,
                phase="validation",
            ) from exc

        try:
            result = pipeline.execute(task_payload)
        except Exception as exc:
            logger.error("Execution error on domain '%s' for task '%s': %s", domain, task_id, exc)
            raise PipelineError(
                f"Execution failure on domain '{domain}': {exc}",
                domain=domain,
                phase="execution",
            ) from exc

        try:
            audit_passed = pipeline.audit(result)
            if not audit_passed:
                err_msg = f"Pipeline '{domain}' audit rejected result for task '{task_id}'"
                logger.error(err_msg)
                raise PipelineError(err_msg, domain=domain, phase="audit")
        except PipelineError:
            raise
        except Exception as exc:
            logger.error("Audit error on domain '%s' for task '%s': %s", domain, task_id, exc)
            raise PipelineError(
                f"Audit failure on domain '{domain}': {exc}",
                domain=domain,
                phase="audit",
            ) from exc

        logger.info("Task '%s' completed successfully on domain '%s'", task_id, domain)
        return result

