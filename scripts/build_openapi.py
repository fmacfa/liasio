#!/usr/bin/env python3
"""Build the checked-in API contract without importing application secrets or DB state."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UUID = {"type": "string", "format": "uuid"}
TEXT = {"type": "string"}
NULL_DATE = {"type": ["string", "null"], "format": "date-time"}
CONDITIONS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "expires_in_days": {"type": ["integer", "null"], "minimum": 1, "maximum": 3650},
        "expires_at": NULL_DATE,
        "max_downloads": {"type": ["integer", "null"], "minimum": 1, "maximum": 100000},
        "password": {"type": "string", "maxLength": 128, "writeOnly": True},
        "allow_previews": {"type": "boolean", "default": True},
        "follow_updates": {"type": "boolean", "default": False},
        "revision_id": UUID,
        "preset_id": UUID,
        "allowed_emails": {
            "type": "array",
            "maxItems": 100,
            "items": {"type": "string", "format": "email"},
        },
    },
    "description": "Omit expiry/download limits or use null for unlimited. A date and duration are mutually exclusive. Password may be empty. follow_updates and revision_id are mutually exclusive. Recipient verification requires configured SMTP.",
}
LINK = {
    "type": "object",
    "properties": {
        "id": UUID,
        "collection_id": UUID,
        "label": TEXT,
        "url": {"type": "string", "format": "uri"},
        "status": TEXT,
        "password_protected": {"type": "boolean"},
        "allow_previews": {"type": "boolean"},
        "follow_updates": {"type": "boolean"},
        "revision_id": {"type": ["string", "null"], "format": "uuid"},
        "allowed_emails": CONDITIONS["properties"]["allowed_emails"],
        "expires_at": NULL_DATE,
        "max_downloads": {"type": ["integer", "null"]},
        "downloads": {"type": "integer"},
    },
}
COLLECTION_INPUT = {
    "type": "object",
    "required": ["title"],
    "properties": {
        "title": {"type": "string", "maxLength": 120},
        "description": {"type": "string", "maxLength": 4000},
        "recipient_note": {"type": "string", "maxLength": 300},
    },
}
COLLECTION = {
    "type": "object",
    "properties": {
        **COLLECTION_INPUT["properties"],
        "id": UUID,
        "created_at": {"type": "string", "format": "date-time"},
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": UUID,
                    "path": TEXT,
                    "size": {"type": "integer"},
                    "sha256": TEXT,
                    "scanned": {"type": "boolean"},
                },
            },
        },
    },
}
PART = {
    "type": "object",
    "required": ["path", "size", "fingerprint"],
    "properties": {
        "path": {"type": "string", "maxLength": 500},
        "size": {"type": "integer", "minimum": 0},
        "fingerprint": {
            "type": "string",
            "pattern": "^[a-f0-9]{64}$",
            "description": "Client resume identifier, unchanged on retry. Browser uses SHA-256 of UTF-8 size:lastModified: followed by the first 64 KiB and last 64 KiB (overlap allowed). Each transmitted block has a separately verified SHA-256 checksum.",
        },
    },
}
UPLOAD = {
    "type": "object",
    "properties": {
        "id": UUID,
        "state": TEXT,
        "size": {"type": "integer"},
        "expires_at": NULL_DATE,
        "replace_existing": {"type": "boolean"},
        "chunk_bytes": {"type": "integer"},
        "parts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    **PART["properties"],
                    "id": UUID,
                    "offset": {"type": "integer"},
                },
            },
        },
    },
}
BATCH = {
    "type": "object",
    "properties": {
        "id": UUID,
        "collection_id": UUID,
        "name": TEXT,
        "count": {"type": "integer"},
        "state": TEXT,
        "created_at": NULL_DATE,
        "completed_at": NULL_DATE,
        "view_url": TEXT,
        "api_url": TEXT,
        "exports": {
            "type": ["object", "null"],
            "properties": {"xlsx": TEXT, "csv": TEXT},
        },
        "headers": {"type": "array", "items": TEXT},
        "source_rows": {"type": "array", "items": {"type": "array", "items": TEXT}},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {**LINK["properties"], "position": {"type": "integer"}},
            },
        },
    },
}


def page(schema):
    return {
        "type": "object",
        "properties": {
            "results": {"type": "array", "items": schema},
            "next_offset": {"type": ["integer", "null"]},
        },
    }


def build():
    paths = {}

    def endpoint(
        path,
        method,
        title,
        scope,
        output,
        *,
        body=None,
        example=None,
        status=200,
        query=(),
        binary=None,
    ):
        import re

        parameters = [
            {
                "name": name,
                "in": "path",
                "required": True,
                "schema": {"type": "string", "enum": ["xlsx", "csv"]}
                if name == "format"
                else UUID,
            }
            for name in re.findall(r"\{([^}]+)\}", path)
        ]
        parameters += [
            {
                "name": name,
                "in": "query",
                "schema": UUID
                if name == "collection_id"
                else {"type": "integer", "minimum": 0, "maximum": 100000},
            }
            for name in query
        ]
        if method == "post" and path.endswith(
            ("/create/", "/links/", "/link-batches/", "/start/")
        ):
            parameters.append(
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "schema": {
                        "type": "string",
                        "minLength": 8,
                        "maxLength": 128,
                        "pattern": "^[A-Za-z0-9_-]{8,128}$",
                    },
                    "description": "Creation requires this header. Replay lasts 24h; reusing with different input fails.",
                }
            )
        operation = {
            "summary": title,
            "operationId": method + "_" + re.sub(r"[^a-z0-9]+", "_", path).strip("_"),
            "tags": [scope],
            "description": "Required scope: "
            + scope
            + ". Only currently accessible resources are returned.",
            "parameters": parameters,
            "responses": {
                str(status): {
                    "description": "Success",
                    "content": {"application/json": {"schema": output}},
                },
                **{
                    str(code): {"description": description}
                    for code, description in [
                        (400, "Invalid input"),
                        (401, "Missing, expired or revoked API key"),
                        (403, "Missing scope"),
                        (404, "Resource inaccessible"),
                        (409, "Idempotency mismatch or changed upload offset"),
                        (429, "Rate limit"),
                        (503, "Capacity or storage unavailable"),
                    ]
                },
            },
        }
        if body:
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {"schema": body, "example": example or {}}
                },
            }
        if binary:
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/octet-stream": {
                        "schema": {"type": "string", "format": "binary"}
                    }
                },
            }
            operation["parameters"] += [
                {
                    "name": "Upload-Offset",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "integer", "minimum": 0},
                },
                {
                    "name": "Upload-Checksum",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                },
            ]
        paths.setdefault(path, {})[method] = operation

    endpoint(
        "/collections/",
        "get",
        "List accessible collections",
        "read",
        page(COLLECTION),
        query=("offset",),
    )
    endpoint(
        "/collections/create/",
        "post",
        "Create a collection",
        "files:write",
        COLLECTION,
        body=COLLECTION_INPUT,
        example={"title": "Client documents"},
        status=201,
    )
    endpoint(
        "/collections/{id}/", "get", "Read collection and files", "read", COLLECTION
    )
    endpoint(
        "/collections/{id}/links/",
        "post",
        "Create a controlled share link",
        "links:write",
        LINK,
        body={
            **CONDITIONS,
            "required": ["label"],
            "properties": {
                **CONDITIONS["properties"],
                "label": {"type": "string", "maxLength": 80},
            },
        },
        example={
            "label": "A",
            "expires_in_days": 30,
            "max_downloads": 3,
            "password": "",
            "allow_previews": True,
        },
        status=201,
    )
    endpoint(
        "/links/", "get", "List manageable links", "read", page(LINK), query=("offset",)
    )
    endpoint("/links/{id}/", "get", "Read a link", "read", LINK)
    endpoint("/links/{id}/revoke/", "post", "Revoke a link", "links:write", LINK)
    endpoint(
        "/collections/{id}/link-batches/",
        "post",
        "Create an atomic batch",
        "links:write",
        BATCH,
        body={
            "type": "object",
            "required": ["name"],
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string", "maxLength": 80},
                "names": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 200,
                    "items": {"type": "string", "maxLength": 80},
                },
                "count": {"type": "integer", "minimum": 1, "maximum": 200},
                "conditions": CONDITIONS,
                "recipients": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 200,
                    "items": {
                        "type": "object",
                        "required": ["name", "email"],
                        "additionalProperties": False,
                        "properties": {
                            "name": {"type": "string", "maxLength": 80},
                            "email": {"type": "string", "format": "email"},
                        },
                    },
                },
                "verify_recipients": {
                    "type": "boolean",
                    "default": False,
                    "description": "Require an email code for each recipient's own link. Requires recipients and SMTP.",
                },
            },
            "oneOf": [
                {
                    "required": [key],
                    "not": {
                        "anyOf": [
                            {"required": [other]}
                            for other in ("names", "count", "recipients")
                            if other != key
                        ]
                    },
                }
                for key in ("names", "count", "recipients")
            ],
        },
        example={
            "name": "Client batch",
            "names": ["A", "B"],
            "conditions": {"expires_in_days": 30, "max_downloads": 3, "password": ""},
        },
        status=201,
    )
    endpoint(
        "/link-batches/",
        "get",
        "List My batches",
        "read",
        page(BATCH),
        query=("offset", "collection_id"),
    )
    endpoint(
        "/link-batches/{id}/", "get", "Read a batch and its named links", "read", BATCH
    )
    endpoint(
        "/link-batches/{id}/export/{format}/", "get", "Export Excel or CSV", "read", {}
    )
    paths["/link-batches/{id}/export/{format}/"]["get"]["responses"]["200"][
        "content"
    ] = {
        mime: {"schema": {"type": "string", "format": "binary"}}
        for mime in (
            "text/csv",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    endpoint(
        "/sharing-presets/",
        "get",
        "List private sharing presets",
        "read",
        {
            "type": "object",
            "properties": {"results": {"type": "array", "items": {"type": "object"}}},
        },
    )
    endpoint(
        "/collections/{id}/uploads/start/",
        "post",
        "Reserve a resumable upload",
        "files:write",
        UPLOAD,
        body={
            "type": "object",
            "required": ["files"],
            "properties": {
                "files": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 100,
                    "items": PART,
                },
                "replace_existing": {"type": "boolean", "default": False},
            },
        },
        status=201,
    )
    endpoint(
        "/collections/{id}/uploads/pending/",
        "get",
        "List resumable uploads",
        "read",
        {
            "type": "object",
            "properties": {"sessions": {"type": "array", "items": UPLOAD}},
        },
    )
    endpoint("/uploads/{id}/status/", "get", "Read durable offsets", "read", UPLOAD)
    endpoint(
        "/uploads/{id}/parts/{part_id}/chunk/",
        "post",
        "Upload one checksummed block",
        "files:write",
        {"type": "object", "properties": {"offset": {"type": "integer"}}},
        binary=True,
    )
    endpoint(
        "/uploads/{id}/finish/",
        "post",
        "Scan and finalize upload",
        "files:write",
        {"type": "object", "properties": {"state": TEXT, "redirect": TEXT}},
    )
    endpoint(
        "/uploads/{id}/cancel/",
        "post",
        "Cancel upload and release quota",
        "files:write",
        {"type": "object", "properties": {"state": TEXT}},
    )
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Liasio API",
            "version": "0.5.0",
            "description": "Self-hosted file sharing. API keys are sent only as Authorization: Bearer. Browser cookies are never API credentials. A download event means authorization, not confirmed receipt.",
        },
        "servers": [{"url": "/api/v1"}],
        "security": [{"bearerAuth": []}],
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "Liasio API key",
                }
            }
        },
        "paths": paths,
    }


if __name__ == "__main__":
    (ROOT / "docs/openapi.json").write_text(
        json.dumps(build(), ensure_ascii=False, indent=2) + "\n"
    )
