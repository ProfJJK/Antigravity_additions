"""SRS-412-05-FR-004 literature backends for the Mandatory Literature Research Stage (MC-TDD-11).

research.py chunk 41-100 (MC-TDD-11) holds the dossier assembly API; the five HTTP backends do not fit in
that 60-line chunk, so they live here. Each backend queries one real public scientific database with the
standard library only (urllib, json, xml.etree), an explicit timeout and a descriptive User-Agent. Nothing is
cached, mocked or substituted: a network, HTTP or response-format failure raises LiteratureBackendError
naming the backend, and the dossier assembly records it as a failed backend.
"""
from __future__ import annotations

import http.client
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Callable, Protocol

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
NETWORK_ERRORS: tuple[type[BaseException], ...] = (urllib.error.URLError, ConnectionError, TimeoutError,
                                                   http.client.HTTPException)
# Failures while reading an unexpected response body.
FORMAT_ERRORS: tuple[type[BaseException], ...] = (ValueError, ET.ParseError, KeyError, TypeError, AttributeError)
_ATOM = "{http://www.w3.org/2005/Atom}"


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


class LiteratureBackend(Protocol):
    """A pluggable scientific database: ``name`` plus ``search(query, limit) -> list[LiteratureHit]``."""
    name: str
    search: Callable[[str, int], list[LiteratureHit]]


def _check_query(name: str, query: str, limit: int) -> None:
    if not isinstance(query, str) or not query.strip():
        raise ValueError(f"{name}: query must be a non-empty string, got {query!r}")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"{name}: limit must be an int in 1..{MAX_LIMIT}, got {limit!r}")


class HttpBackend:
    """Shared HTTP GET for the backends; ``base_url`` is configurable so a backend can point at any server."""
    name: str = "http"
    default_url: str = ""

    def __init__(self, base_url: str | None = None, timeout: float = HTTP_TIMEOUT_SEC) -> None:
        self.base_url = (base_url or self.default_url).rstrip("/")
        self.timeout = float(timeout)

    def fetch(self, url: str, headers: dict[str, str] | None = None, not_found_ok: bool = False) -> bytes | None:
        """GET url; returns the body, or None for HTTP 404 when not_found_ok. Failures raise LiteratureBackendError."""
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
        body = self.fetch(url, headers, not_found_ok)
        if body is None:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise LiteratureBackendError(self.name, f"response from {url} is not JSON: {body[:200]!r}") from exc

    def malformed(self, exc: BaseException) -> LiteratureBackendError:
        return LiteratureBackendError(self.name, f"unexpected response layout: {type(exc).__name__}: {exc}")


class ArxivBackend(HttpBackend):
    """arXiv export API (Atom XML): every query word must appear in the record (all:w1 AND all:w2 ...)."""
    name = "arxiv"
    default_url = ARXIV_URL

    def search(self, query: str, limit: int) -> list[LiteratureHit]:
        _check_query(self.name, query, limit)
        terms = " AND ".join(f"all:{word}" for word in query.split())
        params = urllib.parse.urlencode({"search_query": terms, "start": 0, "max_results": limit})
        body = self.fetch(f"{self.base_url}?{params}")
        try:
            hits = []
            for entry in ET.fromstring(body).findall(f"{_ATOM}entry")[:limit]:
                abs_url = entry.findtext(f"{_ATOM}id").strip().replace("http://", "https://", 1)
                title = " ".join(entry.findtext(f"{_ATOM}title").split())
                hits.append(LiteratureHit(self.name, "arXiv:" + abs_url.rsplit("/abs/", 1)[1], title, abs_url))
            return hits
        except FORMAT_ERRORS as exc:
            raise self.malformed(exc) from exc


class EuropePmcBackend(HttpBackend):
    """Europe PMC REST search (JSON, lite result type)."""
    name = "europepmc"
    default_url = EUROPEPMC_URL

    def search(self, query: str, limit: int) -> list[LiteratureHit]:
        _check_query(self.name, query, limit)
        params = urllib.parse.urlencode({"query": query, "format": "json", "pageSize": limit, "resultType": "lite"})
        data = self.fetch_json(f"{self.base_url}?{params}")
        try:
            return [LiteratureHit(self.name, f"{r['source']}:{r['id']}", " ".join(str(r["title"]).split()),
                                  f"https://europepmc.org/article/{r['source']}/{r['id']}")
                    for r in data["resultList"]["result"][:limit]]
        except FORMAT_ERRORS as exc:
            raise self.malformed(exc) from exc


class PubChemBackend(HttpBackend):
    """PubChem PUG REST compound-name lookup; an unknown name (HTTP 404 PUGREST.NotFound) is zero hits."""
    name = "pubchem"
    default_url = PUBCHEM_URL

    def search(self, query: str, limit: int) -> list[LiteratureHit]:
        _check_query(self.name, query, limit)
        name = urllib.parse.quote(query.strip(), safe="")
        data = self.fetch_json(f"{self.base_url}/compound/name/{name}/property/Title,IUPACName,MolecularFormula/JSON",
                               not_found_ok=True)
        if data is None:
            return []
        try:
            return [LiteratureHit(self.name, f"CID:{int(p['CID'])}",
                                  str(p.get("Title") or p.get("IUPACName") or p["MolecularFormula"]),
                                  f"https://pubchem.ncbi.nlm.nih.gov/compound/{int(p['CID'])}")
                    for p in data["PropertyTable"]["Properties"][:limit]]
        except FORMAT_ERRORS as exc:
            raise self.malformed(exc) from exc


class ChemblBackend(HttpBackend):
    """ChEMBL molecule full-text search (JSON)."""
    name = "chembl"
    default_url = CHEMBL_URL

    def search(self, query: str, limit: int) -> list[LiteratureHit]:
        _check_query(self.name, query, limit)
        params = urllib.parse.urlencode({"q": query, "limit": limit, "only": "molecule_chembl_id,pref_name"})
        data = self.fetch_json(f"{self.base_url}?{params}")
        try:
            return [LiteratureHit(self.name, str(m["molecule_chembl_id"]), str(m["pref_name"] or m["molecule_chembl_id"]),
                                  f"https://www.ebi.ac.uk/chembl/explore/compound/{m['molecule_chembl_id']}")
                    for m in data["molecules"][:limit]]
        except FORMAT_ERRORS as exc:
            raise self.malformed(exc) from exc


class ConsensusBackend(HttpBackend):
    """Consensus academic search API (GET /v1/search, header x-api-key). The key is read only from the
    CONSENSUS_API_KEY environment variable at search time; without it MissingCredentialError is raised."""
    name = "consensus"
    default_url = CONSENSUS_URL

    def search(self, query: str, limit: int) -> list[LiteratureHit]:
        _check_query(self.name, query, limit)
        key = os.environ.get(CONSENSUS_API_KEY_ENV, "").strip()
        if not key:
            raise MissingCredentialError(self.name, f"environment variable {CONSENSUS_API_KEY_ENV} is not set; the "
                                                    "Consensus API requires an API key (x-api-key header)")
        params = urllib.parse.urlencode({"query": query, "page_size": limit})
        data = self.fetch_json(f"{self.base_url}?{params}", headers={"x-api-key": key})
        try:
            return [LiteratureHit(self.name, f"doi:{r['doi']}" if r.get("doi") else str(r["url"]),
                                  " ".join(str(r["title"]).split()), str(r.get("url") or f"https://doi.org/{r['doi']}"))
                    for r in data["results"][:limit]]
        except FORMAT_ERRORS as exc:
            raise self.malformed(exc) from exc


def default_backends(timeout: float = HTTP_TIMEOUT_SEC) -> list[HttpBackend]:
    """The five SRS-412-05-FR-004 databases in SRS order: Consensus, arXiv, EuropePMC, PubChem, ChEMBL."""
    return [ConsensusBackend(timeout=timeout), ArxivBackend(timeout=timeout), EuropePmcBackend(timeout=timeout),
            PubChemBackend(timeout=timeout), ChemblBackend(timeout=timeout)]
