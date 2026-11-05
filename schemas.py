"""Tool schemas — what the LLM sees. Frozen on day 1 (2026-11-05, red-blue pivot)."""

ARCHITECT_LEARN = {
    "name": "architect_learn",
    "description": (
        "Learn a third-party HTTP API by compiling its machine-readable spec "
        "(OpenAPI 3 JSON, Swagger 2, Postman collection v2.1) into a validated, "
        "persistent manifest. Give it a documentation or spec URL. If no spec can "
        "be found, it reports that honestly and invents nothing — paste or point "
        "to a real spec URL. The result is a DRAFT manifest; call architect_commit "
        "to activate it. Do not use for APIs you already know well — plain HTTP "
        "calls are fine there."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Spec URL (openapi.json, swagger.json, api-docs, postman collection) or a documentation page URL where a spec link may be discoverable.",
            },
            "api_name": {
                "type": "string",
                "description": "Optional short name for the API (e.g. 'openweather'). Derived from the host if omitted.",
            },
        },
        "required": ["url"],
    },
}

ARCHITECT_COMMIT = {
    "name": "architect_commit",
    "description": (
        "Activate a DRAFT manifest learned by architect_learn. Interactive: reviews "
        "base_url, auth binding and endpoint provenance before activation. Drafts "
        "are NOT callable until committed. Binds the API's auth to a configured "
        "secret slot (api_key_1 or api_key_2 in plugin settings) when the user asks "
        "for it — never paste API keys inside the chat."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "api_name": {
                "type": "string",
                "description": "Name/slug of the draft API to activate (see architect_list).",
            },
            "secret_binding": {
                "type": "string",
                "description": "Optional: 'api_key_1' or 'api_key_2' — which configured secret slot authenticates this API. Only with the user's explicit choice.",
            },
        },
        "required": ["api_name"],
    },
}

ARCHITECT_LIST = {
    "name": "architect_list",
    "description": (
        "List every API learned so far with its activation state (draft/active), "
        "endpoint count and provenance summary. Manifests persist between sessions; "
        "you do not need to re-learn an API another session already learned."
    ),
    "parameters": {"type": "object", "properties": {}},
}

API_CALL = {
    "name": "api_call",
    "description": (
        "Execute a call against a COMMITTED, learned API. Parameters are validated "
        "against the compiled manifest BEFORE the request goes out: an invalid "
        "parameter fails locally with a precise message instead of a server 400. "
        "Non-GET endpoints are destructive and require interactive approval. "
        "Responses are returned as JSON."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "api_name": {
                "type": "string",
                "description": "Name/slug of a committed API (see architect_list).",
            },
            "endpoint": {
                "type": "string",
                "description": "Endpoint id or method+path from the manifest (e.g. 'get-current-weather' or '/weather?').",
            },
            "params": {
                "type": "object",
                "description": "Parameter values keyed by the endpoint's parameter names.",
            },
            "raw_query_body": {
                "type": "object",
                "description": "Optional JSON request body for POST/PUT/PATCH endpoints.",
            },
        },
        "required": ["api_name", "endpoint"],
    },
}
