#!/usr/bin/env python3
"""Run one bounded, model-discovered proof against an OpenAI-compatible endpoint."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI


MAX_ERROR_CHARS = 4_000


class EndpointProofError(RuntimeError):
    """The endpoint could not satisfy the public serving proof."""


def run_proof(client: Any, *, preferred_model: str | None = None) -> dict[str, Any]:
    """Discover a model ID, then return one deterministic NL-to-SQL completion."""
    model_ids = sorted(str(model.id) for model in client.models.list().data)
    if preferred_model:
        if preferred_model not in model_ids:
            raise EndpointProofError("VF_ENDPOINT_MODEL was not returned by /v1/models")
        model = preferred_model
    elif len(model_ids) == 1:
        model = model_ids[0]
    else:
        raise EndpointProofError(
            "VF_ENDPOINT_MODEL is required when /v1/models returns zero or multiple models"
        )
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        max_tokens=64,
        messages=[
            {"role": "system", "content": "Return only executable SQLite SQL."},
            {
                "role": "user",
                "content": "Schema: CREATE TABLE users (id INTEGER, name TEXT); List every name.",
            },
        ],
    )
    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise EndpointProofError("endpoint returned an empty completion")
    return {
        "completion": content,
        "model": model,
        "usage": response.usage.model_dump() if response.usage else None,
    }


def main() -> int:
    load_dotenv()
    base_url = os.environ.get("VF_ENDPOINT_BASE_URL", "").strip()
    api_key = os.environ.get("VF_ENDPOINT_API_KEY", "").strip()
    if not base_url or not api_key:
        print(
            json.dumps({"error": "VF_ENDPOINT_BASE_URL and VF_ENDPOINT_API_KEY are required"}),
            file=sys.stderr,
        )
        return 2
    client = OpenAI(
        base_url=base_url,
        api_key=api_key,
        max_retries=0,
        timeout=20.0,
    )
    try:
        result = run_proof(client, preferred_model=os.environ.get("VF_ENDPOINT_MODEL"))
    except Exception as error:
        message = str(error).replace(api_key, "<redacted>")[:MAX_ERROR_CHARS]
        print(
            json.dumps({"error": message, "error_type": type(error).__name__}, sort_keys=True),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
