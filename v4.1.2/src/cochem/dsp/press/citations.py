"""Citation Grounding and DOI Resolver (MC-DSP-17, MC-DSP-18)."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

DOI_REGEX = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Za-z0-9]+$")


def validate_citation_dois(dois: list[str]) -> bool:
    """Validates list of DOIs against official DOI format."""
    return all(bool(DOI_REGEX.match(doi)) for doi in dois)


def resolve_openalex_metadata(doi: str) -> dict[str, Any]:
    """Resolves scholarly metadata (title, authors, year) for a DOI via OpenAlex API."""
    if not DOI_REGEX.match(doi):
        raise ValueError(f"Invalid DOI format: '{doi}'")

    url = f"https://api.openalex.org/works/https://doi.org/{doi}"
    headers = {
        "User-Agent": "CoChem-DOIResolver/4.1.2 (mailto:cochem@research.org)",
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        title = data.get("title") or data.get("display_name") or f"Document referenced by DOI {doi}"
        authors: list[str] = []
        for authorship in data.get("authorships") or []:
            author_obj = (authorship.get("author") if isinstance(authorship, dict) else None) or {}
            name = author_obj.get("display_name", "").strip()
            if name:
                authors.append(name)

        year = data.get("publication_year") or 2026
        primary_location = data.get("primary_location") or {}
        source = primary_location.get("source") or {}
        publisher = source.get("host_organization_name") or data.get("publisher") or "Scholarly Publisher"

        return {
            "doi": doi,
            "title": title,
            "authors": authors or ["Anonymous"],
            "year": year,
            "publisher": publisher,
            "resolved": True,
            "source": "openalex",
        }


def resolve_crossref_metadata(doi: str) -> dict[str, Any]:
    """Resolves scholarly metadata (title, authors, year) for a DOI via CrossRef API."""
    if not DOI_REGEX.match(doi):
        raise ValueError(f"Invalid DOI format: '{doi}'")

    url = f"https://api.crossref.org/works/{doi}"
    headers = {
        "User-Agent": "CoChem-DOIResolver/4.1.2 (mailto:cochem@research.org)",
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        message = data.get("message") or {}
        title_list = message.get("title") or []
        title = title_list[0] if (title_list and isinstance(title_list, list)) else f"Document referenced by DOI {doi}"
        authors: list[str] = []
        for author in message.get("author") or []:
            if isinstance(author, dict):
                given = author.get("given", "") or ""
                family = author.get("family", "") or ""
                name = f"{given} {family}".strip()
                if name:
                    authors.append(name)

        created_info = message.get("created") or {}
        date_parts_outer = created_info.get("date-parts") if isinstance(created_info, dict) else None
        date_parts = (date_parts_outer[0] if date_parts_outer and len(date_parts_outer) > 0 else None) or [2026]
        year = date_parts[0] if date_parts and len(date_parts) > 0 else 2026
        publisher = message.get("publisher") or "Scholarly Publisher"

        return {
            "doi": doi,
            "title": title,
            "authors": authors or ["Anonymous"],
            "year": year,
            "publisher": publisher,
            "resolved": True,
            "source": "crossref",
        }


def resolve_doi_metadata(doi: str, provider: str = "auto") -> dict[str, Any]:
    """Resolves scholarly metadata (title, authors, year) for a DOI from OpenAlex and CrossRef APIs."""
    if not DOI_REGEX.match(doi):
        raise ValueError(f"Invalid DOI format: '{doi}'")

    last_error: str | None = None

    if provider == "openalex":
        try:
            return resolve_openalex_metadata(doi)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError, KeyError, IndexError, AttributeError, TypeError) as err:
            last_error = str(err)
    elif provider == "crossref":
        try:
            return resolve_crossref_metadata(doi)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError, KeyError, IndexError, AttributeError, TypeError) as err:
            last_error = str(err)
    else:  # "auto": Try OpenAlex first, fall back to CrossRef
        try:
            return resolve_openalex_metadata(doi)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError, KeyError, IndexError, AttributeError, TypeError) as err:
            last_error = str(err)

        try:
            return resolve_crossref_metadata(doi)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError, KeyError, IndexError, AttributeError, TypeError) as err:
            last_error = str(err)

    # Offline or unreachable across scholarly APIs: return structured fallback metadata
    prefix = doi.split("/")[0]
    return {
        "doi": doi,
        "title": f"Document referenced by DOI {doi}",
        "authors": [f"Registered under DOI Prefix {prefix}"],
        "year": 2026,
        "publisher": f"Registrant {prefix}",
        "resolved": False,
        "source": "offline",
    }
