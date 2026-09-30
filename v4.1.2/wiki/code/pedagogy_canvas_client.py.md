# src/cochem/dsp/pedagogy/canvas_client.py

`python
"""Canvas LMS REST API Client (MC-DSP-25)."""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from typing import Any


class CanvasClient:
    """Interacts with Canvas LMS REST API using OAuth2 Bearer tokens."""

    def __init__(self, base_url: str = "https://canvas.instructure.com", token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(
        self, path: str, method: str = "GET", data: dict[str, Any] | None = None, max_retries: int = 3
    ) -> Any:
        if not self.token:
            raise PermissionError("Authentication Bearer token is required to access Canvas LMS API")

        url = f"{self.base_url}{path}"
        req_body = json.dumps(data).encode("utf-8") if data is not None else None
        headers = self._headers()
        if req_body is not None:
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=req_body, headers=headers, method=method)

        for attempt in range(max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    raw_bytes = resp.read()
                    return json.loads(raw_bytes.decode("utf-8")) if raw_bytes else {}
            except urllib.error.HTTPError as http_err:
                # Handle LMS API Rate Limit (HTTP 429 / 503) with jittered exponential backoff per SRS Ch07 Sec 8
                if http_err.code in (429, 503) and attempt < max_retries:
                    backoff = (2 ** attempt) + random.uniform(0.1, 0.5)
                    time.sleep(backoff)
                    continue
                raise RuntimeError(f"Canvas API HTTP error {http_err.code}: {http_err.reason}") from http_err
            except urllib.error.URLError as url_err:
                raise ConnectionError(f"Failed to connect to Canvas host {self.base_url}: {url_err.reason}") from url_err

    def get_course(self, course_id: int) -> dict[str, Any]:
        """Queries course metadata from Canvas API endpoint."""
        if not isinstance(course_id, int) or course_id <= 0:
            raise ValueError(f"Course ID must be a positive integer, got: {course_id}")
        return self._request(f"/api/v1/courses/{course_id}")

    def list_assignments(self, course_id: int) -> list[dict[str, Any]]:
        """Queries assignment list for a course."""
        if not isinstance(course_id, int) or course_id <= 0:
            raise ValueError(f"Course ID must be a positive integer, got: {course_id}")
        res = self._request(f"/api/v1/courses/{course_id}/assignments")
        return res if isinstance(res, list) else []

    def submit_grade(
        self, course_id: int, assignment_id: int, student_id: int, score: float, comment: str = ""
    ) -> dict[str, Any]:
        """Submits student grade and evaluation commentary."""
        if course_id <= 0 or assignment_id <= 0 or student_id <= 0:
            raise ValueError("Course, assignment, and student IDs must be positive integers")
        payload = {
            "submission": {
                "posted_grade": score,
            },
            "comment": {
                "text_comment": comment
            }
        }
        return self._request(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/{student_id}",
            method="PUT",
            data=payload,
        )

`
