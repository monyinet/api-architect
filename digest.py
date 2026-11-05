"""API-Architect Digest v1 — the frozen contract between ingestion, the LLM step and the executor.

Contract rules (red-blue-plugin-pivot.md, 2026-11-05):
1. Every Endpoint MUST carry Provenance; an endpoint without a doc source is
   not invented — it is dropped (anti-hallucination guarantee for the demo).
2. Destructive is decided by METHOD, never by spec metadata (the spec can lie).
3. Manifests persist secret *names* only (secret_binding), never values.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

ENGINE_VERSION = "0.1.0"

_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
_LOCATIONS = ("path", "query", "header", "body", "cookie")
_AUTH_TYPES = ("none", "bearer", "api_key_header", "api_key_query", "basic")


def slugify(text: str, max_len: int = 40) -> str:
    """ASCII, filesystem-safe slug. Output contains only [a-z0-9-] — this makes
    path traversal in manifest filenames impossible by construction (vector V4)."""
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text[:max_len].strip("-") or "api"


@dataclass
class Provenance:
    source: str  # "openapi-path-item" | "postman-request" | "html-anchor" |
    detail: str = ""  # pointer the user can audit: spec path, section, anchor

    def valid(self) -> bool:
        return bool(str(self.source or "").strip())


@dataclass
class Param:
    name: str
    location: str  # path | query | header | body | cookie
    type: str = "string"
    required: bool = False
    description: str = ""
    enum: Optional[List[str]] = None

    def valid(self) -> bool:
        return bool(self.name.strip()) and self.location in _LOCATIONS


@dataclass
class Endpoint:
    id: str
    method: str
    path: str
    summary: str = ""
    params: List[Param] = field(default_factory=list)
    request_example: Optional[Dict[str, Any]] = None
    provenance: Optional[Provenance] = None
    operation_id: Optional[str] = None  # spec's operationId / postman request name (exact-match address)

    def valid(self) -> bool:
        if self.provenance is None or not self.provenance.valid():
            return False
        if not self.id.strip() or self.method not in _METHODS or not self.path.strip():
            return False
        return all(p.valid() for p in self.params)

    def is_destructive(self) -> bool:
        """Non-GET is treated as destructive BECAUSE of the method, not the spec."""
        return self.method != "GET"


@dataclass
class Auth:
    type: str = "none"  # none | bearer | api_key_header | api_key_query | basic
    location: str = ""  # header name (api_key_header) or query param name (api_key_query)
    secret_binding: str = ""  # api_key_1 | api_key_2 — the declared secret slot, set at commit. Name only.

    def valid(self) -> bool:
        if self.type not in _AUTH_TYPES:
            return False
        if self.type in ("api_key_header", "api_key_query") and not self.location.strip():
            return False
        return True


@dataclass
class Digest:
    api_name: str
    source_url: str
    base_url: str
    auth: Auth = field(default_factory=Auth)
    detection: Dict[str, Any] = field(
        default_factory=lambda: {"mode": "unknown", "spec_url": "", "confidence": 0.0}
    )
    endpoints: List[Endpoint] = field(default_factory=list)
    generated_at: str = ""
    engine_version: str = ENGINE_VERSION
    warnings: List[str] = field(default_factory=list)

    def valid(self) -> bool:
        if not self.api_name.strip() or not self.base_url.strip():
            return False
        if not (self.base_url.startswith("http://") or self.base_url.startswith("https://")):
            return False
        return self.auth.valid() and all(e.valid() for e in self.endpoints)

    def to_json(self, state: str = "draft") -> str:
        payload = asdict(self)
        payload["state"] = state
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @staticmethod
    def from_json(text: str) -> "Digest":
        raw = json.loads(text)
        endpoints = []
        for e in raw.get("endpoints", []):
            prov = e.get("provenance")
            endpoints.append(
                Endpoint(
                    id=e.get("id", ""),
                    method=e.get("method", ""),
                    path=e.get("path", ""),
                    summary=e.get("summary", ""),
                    params=[Param(**p) for p in e.get("params", [])],
                    request_example=e.get("request_example"),
                    provenance=Provenance(source=prov.get("source", ""), detail=prov.get("detail", "")) if prov else None,
                    operation_id=e.get("operation_id"),
                )
            )
        auth = raw.get("auth", {}) or {}
        return Digest(
            api_name=raw.get("api_name", ""),
            source_url=raw.get("source_url", ""),
            base_url=raw.get("base_url", ""),
            auth=Auth(type=auth.get("type", "none"), location=auth.get("location", ""), secret_binding=auth.get("secret_binding", "")),
            detection=raw.get("detection", {"mode": "unknown", "spec_url": "", "confidence": 0.0}),
            endpoints=endpoints,
            generated_at=raw.get("generated_at", ""),
            engine_version=raw.get("engine_version", ENGINE_VERSION),
            warnings=raw.get("warnings", []),
        )
