"""Independent health and real-controller smoke acceptance for a candidate release.

Only the Python standard library and supervisor diagnostic redaction are used.
Fixture tests of validation do not establish native Windows/model execution.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
import uuid

from .monitor import classify_error, redact_diagnostic

_MAX_RESPONSE = 16 * 1024 * 1024
_KINDS = {"MACRO_PLANNING_REQUEST", "MANIFEST_GENERATOR", "CHAPTER_DRAFT", "SYNTHESIS"}
# This independent acceptance contract deliberately does not import the release
# under test. Configured overrides are checked against the persisted policy.
_DEFAULT_TIERS = {
    "1-3": [("gemini", "gemini-3.8-flash", None), ("claude", "claude-haiku-4-5", None), ("codex", "gpt-6-luna", None)],
    "4-6": [("claude", "claude-sonnet-5-5", None), ("codex", "gpt-6-sol", None), ("gemini", "gemini-3.1-pro", None)],
    "7-9": [("claude", "claude-opus-5-5", None), ("codex", "gpt-6-astra", "low"), ("gemini", "gemini-3.1-pro", None)],
    "10": [("claude", "claude-fable-5-1", None), ("codex", "gpt-6-astra", "ultra")],
}
_SCORE_CONTENT = {"objective","title","description","requirements","dependencies","chapters",
                  "chapter_count","artifact_text","wbs","tasks","acceptance_criteria","scope"}
_SCORE_AUTHORITY = {"score","complexity","complexity_score","tier","routing","route","priority",
                    "provider","model","requested_model","reasoning_effort","policy_digest"}
_SCORE_RISKS = {
    "security":("security","authentication","authorization","credentials","cryptography","permissions"),
    "data_integrity":("migration","schema","transaction","consistency","corruption"),
    "concurrency":("concurrency","concurrent","distributed","deadlock","race condition","synchronization"),
    "destructive_change":("delete","deletion","destructive","irreversible","production","rollback"),
    "formal_reasoning":("proof","theorem","optimization","numerical","algorithm","invariant"),
}


def _json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Controller returned duplicate JSON keys")
            result[key] = value
        return result

    def nonfinite(_):
        raise ValueError("Controller returned nonfinite JSON numbers")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=nonfinite)


class ControllerClient:
    """Authenticated loopback-only client independent of pipeline imports."""

    def __init__(self, port: int, token_file: str | Path, timeout: float = 5):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("Controller port must be an integer in 1024..65535")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Controller timeout must be finite and positive")
        self.port, self.token_file, self.timeout = port, Path(token_file), timeout

    def call(self, operation: str, data: dict | None = None) -> dict:
        if not isinstance(operation, str) or not (operation in {"/health", "/submit", "/cancel"}
                or re.fullmatch(r"/workflow/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", operation)):
            raise ValueError("Unsupported controller operation")
        if data is not None and not isinstance(data, dict):
            raise ValueError("Controller requests must be JSON objects")
        if self.token_file.is_symlink() or not self.token_file.is_file() or self.token_file.stat().st_size > 4096:
            raise ValueError("Controller token must be a bounded regular private file")
        with self.token_file.open("r", encoding="ascii") as stream:
            token = stream.read(4097).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,512}", token):
            raise ValueError("Controller token has an invalid format")
        body = json.dumps(data, ensure_ascii=True, allow_nan=False) if data is not None else None
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.timeout)
        try:
            connection.request("POST" if data is not None else "GET", operation, body,
                {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
            response = connection.getresponse()
            content = response.read(_MAX_RESPONSE + 1)
            if response.status >= 400:
                # A remote error body can contain arbitrary data, including
                # echoed credentials. Never include it in an exception/log.
                raise RuntimeError(f"Controller HTTP operation failed with status {response.status}")
            if len(content) > _MAX_RESPONSE:
                raise ValueError("Controller response exceeds the acceptance size limit")
            value = _json(content)
            if not isinstance(value, dict):
                raise ValueError("Controller response must be a JSON object")
            return value
        finally:
            connection.close()


def _require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def _models(provider_models: dict) -> dict[str, str]:
    _require(isinstance(provider_models, dict), "Explicit provider models are required")
    models = {}
    for provider in ("codex", "claude", "gemini"):
        value = provider_models.get(provider)
        value = value.get("model") if isinstance(value, dict) else value
        _require(isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", value)),
                 "Each smoke provider requires an explicit model ID")
        models[provider] = value
    return models


def _target_identity(target: dict) -> tuple[str, str, str | None]:
    _require(isinstance(target, dict), "Routing target must be an object")
    provider, model, effort = (target.get(key) for key in ("provider", "model", "reasoning_effort"))
    _require(isinstance(provider, str) and provider in {"codex", "claude", "gemini"}
             and isinstance(model, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", model)),
             "Routing target has an invalid provider or model")
    _require(effort is None or (provider == "codex" and isinstance(effort,str) and effort in {"low", "ultra"}),
             "Routing target has an invalid reasoning effort")
    return provider, model, effort


def configured_tiers(routing_policy: dict | None = None) -> dict:
    """Read the configured model order without importing pipeline code."""
    _require(routing_policy is None or isinstance(routing_policy, dict), "Routing policy must be an object")
    tiers = (routing_policy or {}).get("tiers")
    if tiers is None:
        _require(routing_policy is None or "tiers" not in routing_policy, "Routing policy score bands cannot be null")
        return {key: list(value) for key, value in _DEFAULT_TIERS.items()}
    _require(isinstance(tiers, dict) and set(tiers) == set(_DEFAULT_TIERS), "Routing policy has invalid score bands")
    result = {}
    for tier, targets in tiers.items():
        _require(isinstance(targets, list) and len(targets) == len(_DEFAULT_TIERS[tier]),
                 "Routing policy has an invalid candidate count")
        result[tier] = [_target_identity(target) for target in targets]
        _require(len(set(result[tier])) == len(result[tier]), "Routing policy contains duplicate targets")
    return result


def configured_routing_policy(raw: dict | None = None) -> dict:
    """Normalize the documented v1 policy independently of candidate code."""
    tiers = configured_tiers(raw)
    raw = raw or {}
    cooldowns = {"quota":300.,"auth":300.,"busy":30.,"backlog":30.,"provider":30.,"timeout":30.,
                 "resource":30.,"configuration":300.,"compatibility":300.,"protocol":30.,
                 "context":0.,"code":0.,"unknown":30.}
    result = {"policy_version":1,"tiers":{tier:[{"provider":provider,"model":model,"reasoning_effort":effort}
        for provider,model,effort in entries] for tier,entries in tiers.items()},
        "model_limits":{},"provider_limits":{},"quota_pool_limits":{},"backlog_threshold":8,
        "failure_cooldowns":cooldowns,"backoff_base_seconds":30.,"backoff_max_seconds":600.,
        "backoff_jitter_fraction":.2,"max_routing_cycles":0,"max_routing_seconds":0.,"max_dispatches":0}
    _require(not set(raw)-set(result), "Routing policy contains undocumented fields")
    _require(type(raw.get("policy_version",1)) is int and raw.get("policy_version",1) == 1,
             "Routing policy version is unsupported")
    for targets in (raw.get("tiers") or {}).values():
        _require(all(not set(target)-{"provider","model","reasoning_effort"} for target in targets),
                 "Routing policy targets contain undocumented fields")

    def number(value, low, high, integral=False):
        _require((type(value) is int if integral else type(value) in (int,float))
                 and math.isfinite(value) and low <= value <= high, "Routing policy limit is invalid")
        return value if integral else float(value)

    providers = raw.get("provider_limits",{})
    _require(isinstance(providers,dict) and not set(providers)-{"codex","claude","gemini"},
             "Routing policy provider limits are invalid")
    for provider in ("codex","claude","gemini"):
        value = providers.get(provider,{})
        _require(isinstance(value,dict) and not set(value)-{"max_concurrency","quota_pool"},
                 "Routing policy provider limits are invalid")
        pool = value.get("quota_pool",provider)
        _require(isinstance(pool,str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}",pool)),
                 "Routing policy quota pool is invalid")
        result["provider_limits"][provider] = {"max_concurrency":number(value.get("max_concurrency",2),1,64,True),
                                                 "quota_pool":pool}
    pools = raw.get("quota_pool_limits",{provider:2 for provider in ("codex","claude","gemini")})
    _require(isinstance(pools,dict), "Routing policy quota limits are invalid")
    for pool in {entry["quota_pool"] for entry in result["provider_limits"].values()}:
        _require(pool in pools, "Routing policy quota pool has no configured limit")
        result["quota_pool_limits"][pool] = number(pools[pool],1,64,True)
    keys = {provider+":"+model+(":"+effort if effort else "") for entries in tiers.values() for provider,model,effort in entries}
    keys.add("gemini:gemini-3.1-pro")
    limits = raw.get("model_limits",{})
    _require(isinstance(limits,dict) and not set(limits)-keys, "Routing policy model limits are invalid")
    result["model_limits"] = {key:number(limits.get(key,1),1,64,True) for key in keys}
    overrides = raw.get("failure_cooldowns",{})
    _require(isinstance(overrides,dict) and not set(overrides)-set(cooldowns), "Routing policy failure cooldowns are invalid")
    result["failure_cooldowns"] = {key:number(overrides.get(key,value),0,86400) for key,value in cooldowns.items()}
    for key,low,high in (("backlog_threshold",1,100000),("max_routing_cycles",0,1000000),("max_dispatches",0,1000000)):
        result[key] = number(raw.get(key,result[key]),low,high,True)
    for key,low,high in (("backoff_base_seconds",.001,600),("backoff_max_seconds",.001,600),
                         ("backoff_jitter_fraction",0,1),("max_routing_seconds",0,31536000)):
        result[key] = number(raw.get(key,result[key]),low,high)
    _require(result["backoff_max_seconds"] >= result["backoff_base_seconds"], "Routing policy backoff limits are invalid")
    return result


def score_routing_task(kind: str, payload: dict) -> dict:
    """Frozen version-1 scoring contract, independent of the candidate release."""
    _require(isinstance(kind,str) and kind in _KINDS and isinstance(payload,dict),
             "Routing scoring requires a task kind and structured payload")
    def canonical(value):
        return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False)
    _require(len(canonical(payload).encode("utf-8")) <= 16*1024*1024, "Routing scoring input is too large")
    def strings(value):
        if isinstance(value,str):
            yield value
        elif isinstance(value,list):
            for child in value:
                yield from strings(child)
        elif isinstance(value,dict):
            _require(all(isinstance(key,str) for key in value), "Routing content keys must be strings")
            for key in sorted(value):
                if key.casefold() not in _SCORE_AUTHORITY:
                    yield from strings(value[key])
    content={key:payload[key] for key in sorted(_SCORE_CONTENT) if key in payload}
    text="\n".join(strings(content))
    byte_count=len(text.encode("utf-8"))
    tokens=max(len(re.findall(r"\S+",text)),(byte_count+3)//4)
    requirements,dependencies=payload.get("requirements",[]),payload.get("dependencies",[])
    requirement_count=len({canonical(value) for value in requirements}) if isinstance(requirements,list) else 0
    dependency_count=len({canonical(value) for value in dependencies}) if isinstance(dependencies,list) else 0
    chapter_count=payload.get("chapter_count",0)
    chapter_count=chapter_count if type(chapter_count) is int and chapter_count>=0 else 0
    breadth=max([chapter_count,*(len(payload[key]) for key in ("chapters","wbs","tasks") if isinstance(payload.get(key),list))])
    folded=text.casefold()
    risks=sorted(group for group,phrases in _SCORE_RISKS.items()
                 if any(re.search(r"(?<!\w)"+re.escape(phrase)+r"(?!\w)",folded) for phrase in phrases))
    points={"baseline":1,"task_kind":0 if kind=="CHAPTER_DRAFT" else 2,
            "input_size":sum(tokens>limit for limit in (512,2048,8192)),
            "requirements":sum(requirement_count>=limit for limit in (4,12)),
            "dependencies":sum(dependency_count>=limit for limit in (1,4)),
            "work_breadth":sum(breadth>=limit for limit in (3,8)),
            "risk_groups":sum(len(risks)>=limit for limit in (1,3))}
    score=min(10,sum(points.values()))
    return {"score":score,"tier":"1-3" if score<=3 else "4-6" if score<=6 else "7-9" if score<=9 else "10",
            "policy_version":1,"rationale":[{"criterion":key,"points":value} for key,value in points.items()],
            "metrics":{"kind":kind,"utf8_bytes":byte_count,"estimated_tokens":tokens,
                       "requirement_count":requirement_count,"dependency_count":dependency_count,
                       "work_breadth":breadth,"risk_groups":risks,"uncapped_score":sum(points.values())}}


def verify_routing_assignment(job: dict, routing_policy: dict | None = None) -> dict:
    """Bind accepted native metadata to the persisted configured routing choice."""
    expected_policy = configured_routing_policy(routing_policy)
    expected_tiers = configured_tiers(expected_policy)
    routing, route, receipt = (job.get(key) for key in ("routing", "route", "receipt"))
    _require(all(isinstance(value, dict) for value in (routing, route, receipt)),
             "Job lacks its persisted routing assignment or native receipt")
    policy, score_details, candidates = (routing.get(key) for key in ("policy", "score_details", "candidates"))
    _require(isinstance(policy, dict) and policy == expected_policy,
             "Persisted routing policy differs from configured policy")
    policy_digest = _digest(policy)
    _require(policy_digest == _digest(expected_policy) and routing.get("policy_hash") == policy_digest
             and route.get("policy_digest") == policy_digest,
             "Routing policy digest is inconsistent")
    _require(isinstance(score_details, dict) and type(score_details.get("score")) is int
             and 1 <= score_details["score"] <= 10, "Routing complexity score is invalid")
    recomputed = score_routing_task(job.get("kind"),job.get("payload"))
    _require(_digest(score_details) == _digest(recomputed), "Recorded routing score or rationale differs from task content")
    score = score_details["score"]
    tier = "1-3" if score <= 3 else "4-6" if score <= 6 else "7-9" if score <= 9 else "10"
    _require(score_details.get("tier") == tier and type(route.get("score")) is int
             and route.get("score") == score and route.get("tier") == tier,
             "Routing complexity score and tier disagree")
    expected = [("gemini", "gemini-3.1-pro", None)] if job.get("kind") == "SYNTHESIS" else expected_tiers[tier]
    _require(isinstance(candidates, list) and [_target_identity(target) for target in candidates] == expected,
             "Persisted routing candidates differ from configured score band")
    for target, (provider,model,effort) in zip(candidates,expected):
        key = provider+":"+model+(":"+effort if effort else "")
        _require(target.get("key") == key and type(target.get("max_concurrency")) is int
                 and target.get("max_concurrency") == expected_policy["model_limits"][key]
                 and target.get("quota_pool") == expected_policy["provider_limits"][provider]["quota_pool"],
                 "Persisted routing target limits differ from configured policy")
    index = route.get("candidate_index")
    _require(type(index) is int and 0 <= index < len(candidates), "Routing candidate index is invalid")
    provider, model, effort = _target_identity(route)
    _require((provider, model, effort) == expected[index], "Selected routing target differs from recorded priority candidate")
    _require(all(type(route.get(key)) is type(candidates[index].get(key)) and route.get(key) == candidates[index].get(key)
                 for key in ("key","quota_pool","max_concurrency")),
             "Selected routing target limits differ from its recorded candidate")
    _require(receipt.get("provider") == provider and receipt.get("requested_model") == model,
             "Native receipt differs from the selected routing provider or model")
    _require(receipt.get("requested_effort") == effort and receipt.get("reported_effort") in (None,effort),
             "Native receipt differs from the selected reasoning effort")
    reservation = route.get("reservation_sha256")
    _require(isinstance(reservation, str) and bool(re.fullmatch(r"[a-f0-9]{64}", reservation))
             and receipt.get("route_reservation_sha256") == reservation,
             "Native receipt is not bound to its routing reservation")
    _require(type(route.get("cycle")) is int and route["cycle"] >= 0, "Routing cycle is invalid")
    return {"provider": provider, "model": model, "reasoning_effort": effort,
            "score": score, "tier": tier, "candidate_index": index,
            "cycle": route["cycle"], "policy_digest": policy_digest, "reservation_sha256": reservation}


def verify_smoke_workflow(workflow: dict, provider_models: dict, routing_policy: dict | None = None) -> dict:
    """Validate the entire two-chapter result and bound native receipt evidence.

    A valid PID field is metadata, not an OS revalidation of an exited process.
    The trusted authenticated controller produces receipts; this function
    verifies their consistency without pretending fixtures are live inference.
    """
    _models(provider_models)
    _require(isinstance(workflow, dict) and workflow.get("status") == "COMPLETED", "Smoke workflow did not complete")
    workflow_id, root, jobs, artifacts = (workflow.get(key) for key in ("workflow_id", "root", "jobs", "artifacts"))
    _require(isinstance(workflow_id, str) and bool(workflow_id), "Smoke workflow has no ID")
    _require(isinstance(root, dict) and root.get("job_id") == workflow_id and root.get("kind") == "MACRO_PLANNING_REQUEST"
             and root.get("status") == "COMPLETED", "Smoke root is not a completed macro request")
    _require(isinstance(jobs, list) and len(jobs) == 5 and all(isinstance(job, dict) for job in jobs),
             "Smoke DAG must contain root, manifest, two chapters and synthesis")
    identifiers = [job.get("job_id") for job in jobs]
    _require(all(isinstance(value, str) and value for value in identifiers) and len(set(identifiers)) == 5,
             "Smoke DAG job identities must be distinct")
    _require(all(job.get("workflow_id") == workflow_id and job.get("status") == "COMPLETED"
                 and job.get("kind") in _KINDS for job in jobs), "Smoke DAG contains foreign or incomplete jobs")
    by_kind = {kind: [job for job in jobs if job["kind"] == kind] for kind in _KINDS}
    _require([len(by_kind[kind]) for kind in ("MACRO_PLANNING_REQUEST", "MANIFEST_GENERATOR", "CHAPTER_DRAFT", "SYNTHESIS")] == [1, 1, 2, 1],
             "Smoke DAG has incorrect stage counts")
    _require(by_kind["MACRO_PLANNING_REQUEST"][0] == root, "Smoke root disagrees with its DAG record")
    _require(isinstance(root.get("payload"), dict) and root["payload"].get("chapter_count") == 2,
             "Smoke root must request exactly two chapters")
    _require(all(type(job.get("attempts")) is int and 0 <= job["attempts"] <= 1
                 and type(job.get("max_attempts")) is int and job["max_attempts"] == 1 for job in jobs), "Smoke acceptance prohibits retries")
    manifest, synthesis = by_kind["MANIFEST_GENERATOR"][0], by_kind["SYNTHESIS"][0]
    chapters = by_kind["CHAPTER_DRAFT"]
    _require(all(isinstance(job.get("payload"), dict) and type(job["payload"].get("chapter_index")) is int for job in chapters),
             "Smoke chapters require manifest-order indexes")
    chapters = sorted(chapters, key=lambda job: job["payload"]["chapter_index"])
    _require([job["payload"]["chapter_index"] for job in chapters] == [0, 1], "Smoke chapter order is invalid")
    workers = [manifest, *chapters, synthesis]
    receipts, unverified, unverified_effort = [], set(), set()
    for job in workers:
        _require(job.get("attempts") == 1 and job.get("parent_job_id") == workflow_id, "Smoke worker must have one owned execution attempt")
        output, receipt = job.get("output"), job.get("receipt")
        _require(isinstance(output, dict) and isinstance(receipt, dict), "Smoke job lacks structured output or native receipt")
        assignment = verify_routing_assignment(job, routing_policy)
        provider, model = assignment["provider"], assignment["model"]
        routing = job["routing"]
        _require(type(routing.get("dispatches")) is int and routing["dispatches"] == 1
                 and type(routing.get("max_dispatches")) is int and routing["max_dispatches"] == 1,
                 "Smoke acceptance prohibits multiple native dispatches")
        _require(receipt.get("provider") == provider and receipt.get("subscription_verified") is True,
                 "Smoke worker provider or subscription mode is unverified")
        _require("execution_kind" not in receipt, "Emulator/test receipt markers cannot establish native smoke acceptance")
        _require(type(receipt.get("pid")) is int and 0 < receipt["pid"] <= 0xFFFFFFFF
                 and type(receipt.get("exit_code")) is int and receipt["exit_code"] == 0,
                 "Smoke worker lacks successful native process metadata")
        _require(isinstance(receipt.get("session_id"), str) and 0 < len(receipt["session_id"].strip()) <= 512,
                 "Smoke worker lacks a native session identifier")
        reported = receipt.get("reported_model")
        _require(reported is None or reported == model, "Smoke native model metadata contradicts the configured model")
        _require(provider != "gemini" or reported == model, "Gemini must report the exact configured model")
        if reported is None:
            unverified.add(provider)
        if assignment["reasoning_effort"] is not None and receipt.get("reported_effort") is None:
            unverified_effort.add(provider)
        digest = _digest(output)
        _require(receipt.get("output_sha256") == digest and job.get("output_sha256") == digest,
                 "Smoke output does not match its accepted native receipt digest")
        _require(isinstance(receipt.get("stdout_sha256"), str) and bool(re.fullmatch(r"[a-f0-9]{64}", receipt["stdout_sha256"])),
                 "Smoke receipt lacks its native stdout digest")
        receipts.append({"kind": job["kind"], "provider": provider, "pid": receipt["pid"],
                         "exit_code": 0, "session_id": receipt["session_id"], "requested_model": model,
                         "requested_effort": assignment["reasoning_effort"], "reported_effort": receipt.get("reported_effort"),
                         "routing": assignment,
                         "reported_model": reported, "output_sha256": digest, "stdout_sha256": receipt["stdout_sha256"]})
    _require(isinstance(manifest["output"].get("chapters"), list) and len(manifest["output"]["chapters"]) == 2,
             "Smoke manifest does not describe two chapters")
    chapter_ids = [job.get("chapter_id") for job in chapters]
    _require(all(isinstance(value, str) and value for value in chapter_ids) and len(set(chapter_ids)) == 2,
             "Smoke chapters have duplicate or missing identities")
    _require([entry.get("chapter_id") if isinstance(entry, dict) else None for entry in manifest["output"]["chapters"]] == chapter_ids,
             "Smoke chapter order disagrees with the accepted manifest")
    _require(isinstance(artifacts, list) and len(artifacts) == 3 and all(isinstance(entry, dict) for entry in artifacts),
             "Smoke workflow must contain two chapter artifacts and a synthesis artifact")
    artifact_jobs = [entry.get("job_id") for entry in artifacts]
    _require(all(isinstance(value, str) and value for value in artifact_jobs)
             and len(set(artifact_jobs)) == 3 and set(artifact_jobs) == {job["job_id"] for job in [*chapters, synthesis]},
             "Smoke artifacts have duplicate, missing or foreign owners")
    hashes = {}
    for job in [*chapters, synthesis]:
        output = job["output"]
        text = output.get("artifact_text")
        _require(isinstance(text, str) and bool(text.strip()), "Smoke artifact text is empty")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        artifact = next(item for item in artifacts if item["job_id"] == job["job_id"])
        chapter_id = job.get("chapter_id") or "synthesis"
        uri = f"db://{workflow_id}/{chapter_id}"
        _require(artifact.get("workflow_id") == workflow_id and artifact.get("chapter_id") == chapter_id
                 and artifact.get("artifact_uri") == uri and artifact.get("artifact_text") == text
                 and artifact.get("sha256") == digest and job.get("artifact_sha256") == digest,
                 "Smoke artifact identity, content or digest is invalid")
        if job["kind"] == "CHAPTER_DRAFT":
            _require(output.get("chapter_id") == chapter_id and output.get("artifact_uri") == uri,
                     "Smoke chapter output claims a different owner")
            required = job["payload"].get("requirements")
            traced = output.get("requirements_traced")
            _require(isinstance(required, list) and bool(required) and isinstance(traced, list)
                     and all(isinstance(value, str) for value in required + traced) and set(required) == set(traced),
                     "Smoke chapter requirement tracing is incomplete")
            _require(isinstance(output.get("wbs_tasks_defined"), list) and bool(output["wbs_tasks_defined"]),
                     "Smoke chapter has no WBS tasks")
            hashes[chapter_id] = digest
    _require(synthesis["output"].get("chapter_hashes") == hashes, "Smoke synthesis does not preserve all accepted chapter hashes")
    return {"passed": True, "workflow_id": workflow_id, "process_receipts": receipts,
            "artifact_hashes": {**hashes, "synthesis": synthesis["artifact_sha256"]},
            "unverified_model_providers": sorted(unverified), "model_identity_verified": not unverified,
            "unverified_effort_providers": sorted(unverified_effort), "effort_identity_verified": not unverified_effort}


def _valid_health_sample(private_root: Path, client: ControllerClient, expected_source: Path, started_after: float):
    path = private_root / "supervisor_status.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        return None
    with path.open("r", encoding="utf-8") as stream:
        value = _json(stream.read(65537))
    if not isinstance(value, dict):
        return None
    timestamp, started = value.get("timestamp"), value.get("process_started_at")
    if (type(timestamp) not in (int, float) or type(started) not in (int, float)
            or not math.isfinite(timestamp) or not math.isfinite(started)
            or not -5 <= time.time() - timestamp <= 30 or timestamp < started or started < started_after-1
            or type(value.get("pid")) is not int or value["pid"] <= 0
            or type(value.get("sequence")) is not int or value["sequence"] < 0
            or not isinstance(value.get("instance_id"), str) or not value["instance_id"]
            or not isinstance(value.get("version"), str) or not value["version"]
            or not isinstance(value.get("status"), (dict, str))
            or not isinstance(value.get("source_root"), str)
            or Path(value["source_root"]).resolve() != expected_source):
        return None
    health = client.call("/health")
    if (health.get("service_identity") != "SYSTEM" or not isinstance(health.get("quarantined_slots"), dict)
            or health["quarantined_slots"] or not isinstance(health.get("trip_errors"), (dict, list))
            or health["trip_errors"] or health.get("pid") != value["pid"]
            or health.get("instance_id") != value["instance_id"]
            or health.get("process_started_at") != started
            or health.get("version") != value["version"]
            or not isinstance(health.get("source_root"), str)
            or Path(health["source_root"]).resolve() != expected_source):
        return None
    return (value["instance_id"], value["pid"], started), value["sequence"]


def wait_for_health(private_root: str | Path, client: ControllerClient, expected_source: Path,
                    started_after: float, timeout_seconds: float = 60, heartbeat=lambda: True) -> bool:
    """Require two progressing ticks from the new release and its matching API."""
    if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Health timeout must be finite and positive")
    if type(started_after) not in (int, float) or not math.isfinite(started_after):
        raise ValueError("Startup timestamp must be finite")
    expected = Path(expected_source).resolve()
    deadline, previous = time.monotonic()+timeout_seconds, None
    while time.monotonic() < deadline:
        if not heartbeat():
            return False
        try:
            sample = _valid_health_sample(Path(private_root), client, expected, started_after)
        except (OSError, ValueError, RuntimeError, http.client.HTTPException):
            sample = None
        if sample is not None:
            if previous is not None and previous[0] == sample[0] and sample[1] > previous[1]:
                return True
            previous = sample
        else:
            previous = None
        time.sleep(min(.25, max(0, deadline-time.monotonic())))
    return False


def _save_report(path: Path, report: dict) -> None:
    if not path.is_absolute() or not path.parent.is_dir() or path.is_symlink():
        raise ValueError("Smoke report must be an absolute path inside existing protected supervisor storage")
    descriptor, temporary = tempfile.mkstemp(prefix=".smoke-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _failed_workflow_evidence(workflow: dict) -> dict:
    """Preserve safe root-cause classes without copying task input or output."""
    failures = []
    jobs = workflow.get("jobs")
    if isinstance(jobs, list):
        for job in jobs[:128]:
            if not isinstance(job, dict) or job.get("status") != "FAILED" or job.get("kind") == "MACRO_PLANNING_REQUEST":
                continue
            error = job.get("error")
            if not isinstance(error, str) or not error.strip():
                continue
            # Propagated sibling cancellation is not the originating fault.
            if error.strip().casefold().startswith(("upstream job failed", "upstream workflow failed", "upstream attempt budget", "cancelled by operator")):
                continue
            classification = classify_error(error[:2048])
            failures.append({"kind": job.get("kind") if job.get("kind") in _KINDS else "UNKNOWN",
                             "category": classification["category"], "repairable": classification["repairable"],
                             "diagnostic": classification["diagnostic"]})
            if len(failures) == 8:
                break
    if not failures:
        root = workflow.get("root")
        error = root.get("error") if isinstance(root, dict) else None
        classification = classify_error(error[:2048] if isinstance(error, str) else "The controller reported an unspecified failed workflow")
        failures.append({"kind": "MACRO_PLANNING_REQUEST", "category": classification["category"],
                         "repairable": classification["repairable"], "diagnostic": classification["diagnostic"]})
    # Any environmental blocker prevents another paid code-repair attempt.
    priority = {"auth": 0, "quota": 1, "provider": 2, "resource": 3, "configuration": 4, "compatibility": 5, "code": 6}
    selected = min(failures, key=lambda item: priority[item["category"]])
    return {"failure_category": selected["category"], "failure_repairable": selected["repairable"], "failure_evidence": failures}


def run_smoke_workflow(config: dict, client: ControllerClient, report_file: str | Path, heartbeat=lambda: True) -> dict:
    """Submit one bounded real workflow; cancel unfinished work on any failure."""
    models = _models(config.get("pipeline_providers", {}))
    routing_policy = config.get("pipeline_routing", {})
    configured_routing_policy(routing_policy)
    timeout = config.get("smoke_timeout_seconds", 600)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Smoke timeout must be finite and positive")
    workflow_id = "supervisor-smoke-" + uuid.uuid4().hex
    report = {"passed": False, "workflow_id": workflow_id, "started_at": time.time(), "execution": "authenticated-controller-workflow"}
    submitted, completed = False, False
    try:
        if not heartbeat():
            raise RuntimeError("Supervisor ownership was lost before smoke submission")
        submitted = True  # A request can be accepted even if its response is lost.
        workflow = client.call("/submit", {
            "workflow_id": workflow_id,
            "objective": "Create a concise two-chapter operational checklist for a small research software project. "
                         "Chapter one covers startup verification and chapter two covers shutdown verification. "
                         "Include testable requirements and actionable WBS tasks; do not access external services or modify files.",
            "requirements": ["SUPERVISOR-1", "SUPERVISOR-2"], "chapter_count": 2, "max_attempts": 1, "max_dispatches": 1,
        })
        deadline = time.monotonic()+timeout
        while True:
            _require(workflow.get("workflow_id") == workflow_id, "Controller returned a different smoke workflow")
            completed = workflow.get("status") == "COMPLETED"
            if completed:
                report.update(verify_smoke_workflow(workflow, models, routing_policy))
                break
            if workflow.get("status") == "FAILED":
                report.update(_failed_workflow_evidence(workflow))
                raise RuntimeError("The controller reported a failed smoke workflow")
            if time.monotonic() >= deadline:
                raise TimeoutError("The real smoke workflow exceeded its acceptance deadline")
            if not heartbeat():
                raise RuntimeError("Supervisor ownership was lost during smoke execution")
            time.sleep(min(.25, max(0, deadline-time.monotonic())))
            workflow = client.call("/workflow/" + workflow_id)
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error=redact_diagnostic(str(exc)))
        if "failure_category" not in report:
            classification = classify_error(str(exc))
            # A completed DAG rejected by this independent verifier has a code/
            # protocol contract failure. A timed-out provider run is held until
            # its cause is known, rather than spending on an assumed code bug.
            category = "compatibility" if completed and isinstance(exc, (ValueError, TypeError, KeyError)) else (
                "provider" if isinstance(exc, TimeoutError) else classification["category"])
            report.update(failure_category=category, failure_repairable=category in {"code", "compatibility"})
    finally:
        if submitted and not completed and not report["passed"]:
            try:
                cancelled = client.call("/cancel", {"workflow_id": workflow_id})
                _require(cancelled.get("status") in {"FAILED", "COMPLETED"}, "Controller did not confirm a terminal smoke state")
                report["unfinished_work_cancelled"] = True
            except Exception:
                report["unfinished_work_cancelled"] = False
                report["cancellation_error"] = "Controller cancellation could not be confirmed"
        report["finished_at"] = time.time()
        _save_report(Path(report_file), report)
    return report
