"""SRS-412-05-FR-004 literature backends for the Mandatory Literature Research Stage.

Provides production-ready, zero-mock scientific literature search backends querying real public
databases: Consensus, arXiv, EuropePMC, PubChem, and ChEMBL. Implements timeout-bounded HTTP
queries using Python standard libraries (urllib, json, xml.etree) and optional Antigravity SDK
integration.
"""
from __future__ import annotations

from dataclasses import dataclass
import http.client
import json
import logging
import os
from typing import Any, Callable, Protocol, runtime_checkable
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# Optional Antigravity SDK integration
try:
    from google import antigravity as agy_sdk  # type: ignore
except Exception:
    agy_sdk = None

logger = logging.getLogger("cochem.tdd_pivot.literature")

USER_AGENT: str = "CoChem-ResearchStage/4.1.2 (SRS-412-05-FR-004 research dossier; python-urllib)"
HTTP_TIMEOUT_SEC: float = 30.0
MAX_RESPONSE_BYTES: int = 4_000_000
MAX_LIMIT: int = 25
CONSENSUS_API_KEY_ENV: str = "CONSENSUS_API_KEY"

ARXIV_URL: str = "https://export.arxiv.org/api/query"
EUROPEPMC_URL: str = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
PUBCHEM_URL: str = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
CHEMBL_URL: str = "https://www.ebi.ac.uk/chembl/api/data/molecule/search.json"
CONSENSUS_URL: str = "https://api.consensus.app/v1/search"

# Network and HTTP failures of one request (HTTPError is a URLError; RemoteDisconnected is a ConnectionError).
NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    urllib.error.URLError,
    ConnectionError,
    TimeoutError,
    http.client.HTTPException,
    ValueError,
)

# Failures while reading an unexpected response body.
FORMAT_ERRORS: tuple[type[BaseException], ...] = (
    ValueError,
    ET.ParseError,
    KeyError,
    TypeError,
    AttributeError,
)

_ATOM: str = "{http://www.w3.org/2005/Atom}"


@dataclass(frozen=True)
class LiteratureHit:
    """One record found by a backend: database name, database identifier, title and a resolvable URL."""
    source: str
    identifier: str
    title: str
    url: str


class LiteratureBackendError(RuntimeError):
    """A backend could not deliver results (network, HTTP status, response format or missing credential)."""

    def __init__(self, backend: str, message: str) -> None:
        super().__init__(f"{backend}: {message}")
        self.backend = backend


class MissingCredentialError(LiteratureBackendError):
    """The backend needs an API key that is not available in its environment variable."""


@runtime_checkable
class LiteratureBackend(Protocol):
    """A pluggable scientific database: ``name`` plus ``search(query, limit) -> list[LiteratureHit]``."""
    name: str
    search: Callable[[str, int], list[LiteratureHit]]


def _check_query(name: str, query: str, limit: int) -> None:
    """Validate query string and result limit bounds."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError(f"{name}: query must be a non-empty string, got {query!r}")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"{name}: limit must be an int in 1..{MAX_LIMIT}, got {limit!r}")


class HttpBackend:
    """Shared HTTP GET for the backends; base_url is configurable for testing or alternate endpoints."""
    name: str = "http"
    default_url: str = ""

    def __init__(self, base_url: str | None = None, timeout: float = HTTP_TIMEOUT_SEC) -> None:
        self.base_url = (base_url or self.default_url).rstrip("/")
        self.timeout = float(timeout)

    def fetch(self, url: str, headers: dict[str, str] | None = None, not_found_ok: bool = False) -> bytes | None:
        """GET url; returns the body bytes, or None for HTTP 404 when not_found_ok. Failures raise LiteratureBackendError."""
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and not_found_ok:
                return None
            raise LiteratureBackendError(self.name, f"HTTP {exc.code} {exc.reason} from {url}") from exc
        except NETWORK_ERRORS as exc:
            raise LiteratureBackendError(self.name, f"{type(exc).__name__}: {exc} ({url})") from exc
        if len(body) > MAX_RESPONSE_BYTES:
            raise LiteratureBackendError(self.name, f"response from {url} exceeds {MAX_RESPONSE_BYTES} bytes")
        return body

    def fetch_json(self, url: str, headers: dict[str, str] | None = None, not_found_ok: bool = False) -> Any:
        """GET url and parse as JSON. Returns None if 404 and not_found_ok."""
        body = self.fetch(url, headers=headers, not_found_ok=not_found_ok)
        if body is None:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise LiteratureBackendError(self.name, f"response from {url} is not JSON: {body[:200]!r}") from exc

    def malformed(self, exc: BaseException) -> LiteratureBackendError:
        """Wrap response parsing errors into a LiteratureBackendError."""
        return LiteratureBackendError(self.name, f"unexpected response layout: {type(exc).__name__}: {exc}")


class ArxivBackend(HttpBackend):
    """arXiv export API (Atom XML): query keywords joined by AND (all:w1 AND all:w2 ...)."""
    name: str = "arxiv"
    default_url: str = ARXIV_URL

    def search(self, query: str, limit: int) -> list[LiteratureHit]:
        _check_query(self.name, query, limit)
        terms = " AND ".join(f"all:{word}" for word in query.split())
        params = urllib.parse.urlencode({"search_query": terms, "start": 0, "max_results": limit})
        body = self.fetch(f"{self.base_url}?{params}")
        if body is None:
            return []
        try:
            hits: list[LiteratureHit] = []
            root = ET.fromstring(body)
            for entry in root.findall(f"{_ATOM}entry")[:limit]:
                id_elem = entry.findtext(f"{_ATOM}id")
                abs_url = id_elem.strip().replace("http://", "https://", 1) if id_elem else ""
                title_elem = entry.findtext(f"{_ATOM}title")
                title = " ".join(title_elem.split()) if title_elem else "Untitled"
                identifier = "arXiv:" + abs_url.rsplit("/abs/", 1)[1] if "/abs/" in abs_url else abs_url
                hits.append(LiteratureHit(self.name, identifier, title, abs_url))
            return hits
        except FORMAT_ERRORS as exc:
            raise self.malformed(exc) from exc


class EuropePmcBackend(HttpBackend):
    """Europe PMC REST search (JSON, lite result type)."""
    name: str = "europepmc"
    default_url: str = EUROPEPMC_URL

    def search(self, query: str, limit: int) -> list[LiteratureHit]:
        _check_query(self.name, query, limit)
        params = urllib.parse.urlencode({"query": query, "format": "json", "pageSize": limit, "resultType": "lite"})
        data = self.fetch_json(f"{self.base_url}?{params}")
        if data is None:
            return []
        try:
            results = data.get("resultList", {}).get("result", [])
            return [
                LiteratureHit(
                    self.name,
                    f"{r.get('source', 'MED')}:{r.get('id', '')}",
                    " ".join(str(r.get("title", "")).split()),
                    f"https://europepmc.org/article/{r.get('source', 'MED')}/{r.get('id', '')}",
                )
                for r in results[:limit]
            ]
        except FORMAT_ERRORS as exc:
            raise self.malformed(exc) from exc


class PubChemBackend(HttpBackend):
    """PubChem PUG REST compound-name lookup; unknown name returns empty list."""
    name: str = "pubchem"
    default_url: str = PUBCHEM_URL

    def search(self, query: str, limit: int) -> list[LiteratureHit]:
        _check_query(self.name, query, limit)
        encoded_name = urllib.parse.quote(query.strip(), safe="")
        endpoint = f"{self.base_url}/compound/name/{encoded_name}/property/Title,IUPACName,MolecularFormula/JSON"
        data = self.fetch_json(endpoint, not_found_ok=True)
        if data is None:
            return []
        try:
            properties = data.get("PropertyTable", {}).get("Properties", [])
            return [
                LiteratureHit(
                    self.name,
                    f"CID:{int(p['CID'])}",
                    str(p.get("Title") or p.get("IUPACName") or p.get("MolecularFormula", "")),
                    f"https://pubchem.ncbi.nlm.nih.gov/compound/{int(p['CID'])}",
                )
                for p in properties[:limit]
            ]
        except FORMAT_ERRORS as exc:
            raise self.malformed(exc) from exc


class ChemblBackend(HttpBackend):
    """ChEMBL molecule full-text search (JSON)."""
    name: str = "chembl"
    default_url: str = CHEMBL_URL

    def search(self, query: str, limit: int) -> list[LiteratureHit]:
        _check_query(self.name, query, limit)
        params = urllib.parse.urlencode({"q": query, "limit": limit, "only": "molecule_chembl_id,pref_name"})
        data = self.fetch_json(f"{self.base_url}?{params}")
        if data is None:
            return []
        try:
            molecules = data.get("molecules", [])
            return [
                LiteratureHit(
                    self.name,
                    str(m.get("molecule_chembl_id", "")),
                    str(m.get("pref_name") or m.get("molecule_chembl_id", "")),
                    f"https://www.ebi.ac.uk/chembl/explore/compound/{m.get('molecule_chembl_id', '')}",
                )
                for m in molecules[:limit]
            ]
        except FORMAT_ERRORS as exc:
            raise self.malformed(exc) from exc


class ConsensusBackend(HttpBackend):
    """Consensus academic search API (GET /v1/search, header x-api-key).

    The key is read from the CONSENSUS_API_KEY environment variable. If missing,
    raises MissingCredentialError.
    """
    name: str = "consensus"
    default_url: str = CONSENSUS_URL

    def search(self, query: str, limit: int) -> list[LiteratureHit]:
        _check_query(self.name, query, limit)
        api_key = os.environ.get(CONSENSUS_API_KEY_ENV, "").strip()
        if not api_key:
            raise MissingCredentialError(
                self.name,
                f"environment variable {CONSENSUS_API_KEY_ENV} is not set; "
                "the Consensus API requires an API key (x-api-key header)",
            )
        params = urllib.parse.urlencode({"query": query, "page_size": limit})
        data = self.fetch_json(f"{self.base_url}?{params}", headers={"x-api-key": api_key})
        if data is None:
            return []
        try:
            results = data.get("results", [])
            return [
                LiteratureHit(
                    self.name,
                    f"doi:{r.get('doi')}" if r.get("doi") else str(r.get("url", "")),
                    " ".join(str(r.get("title", "")).split()),
                    str(r.get("url") or (f"https://doi.org/{r.get('doi')}" if r.get("doi") else "")),
                )
                for r in results[:limit]
            ]
        except FORMAT_ERRORS as exc:
            raise self.malformed(exc) from exc


def default_backends(timeout: float = HTTP_TIMEOUT_SEC) -> list[HttpBackend]:
    """The five SRS-412-05-FR-004 databases in SRS order: Consensus, arXiv, EuropePMC, PubChem, ChEMBL."""
    return [
        ConsensusBackend(timeout=timeout),
        ArxivBackend(timeout=timeout),
        EuropePmcBackend(timeout=timeout),
        PubChemBackend(timeout=timeout),
        ChemblBackend(timeout=timeout),
    ]
