# Liasio API v1

[Illustrated English guide](guide/en/automation.md) · [Guide français illustré](guide/fr/automation.md)

Base path: `/api/v1/`. HTTPS in production. Authentication: `Authorization: Bearer lv_<id>.<secret>`. Session cookies and query-string keys are never accepted. Keys belong to one user; all lookups enforce that ownership. Only the web interface can create/revoke keys, after account authentication.

Send JSON with `Content-Type: application/json`, except binary chunks. Use `Accept-Language: en` or `fr` for translated validation messages. Error **codes**, field names, status enums and event names remain stable, untranslated identifiers. Empty optional fields may be omitted. UUID path parameters are canonical UUID strings.

## Routes and permissions

| Method | Path after `/api/v1/` | Permission | Success |
| --- | --- | --- | --- |
| GET | `collections/` | read | 200, paginated collection objects |
| POST | `collections/create/` | files:write | 201, collection object; idempotency key required |
| GET | `collections/{id}/` | read | 200, collection and file metadata |
| POST | `collections/{id}/uploads/start/` | files:write | 201, session; idempotency key required |
| GET | `collections/{id}/uploads/pending/` | read | 200, `{ "sessions": [...] }` |
| GET | `uploads/{id}/status/` | read | 200, session |
| POST | `uploads/{id}/parts/{part}/chunk/` | files:write | 200, `{ "offset": N }` |
| POST | `uploads/{id}/finish/` | files:write | 200, complete state and collection redirect |
| POST | `uploads/{id}/cancel/` | files:write | 200, canceled state |
| GET | `links/` | read | 200, paginated link objects |
| POST | `collections/{id}/links/` | links:write | 201, link; idempotency key required |
| POST | `collections/{id}/link-batches/` | links:write | 201, completed batch with ordered links; idempotency key required |
| GET | `link-batches/` | read | 200, paginated batch summaries; optional `collection_id` filter |
| GET | `link-batches/{id}/` | read | 200, batch, original rows and current links |
| GET | `link-batches/{id}/export/{format}/` | read | 200, private `xlsx` or `csv` attachment |
| GET | `links/{id}/` | read | 200, link object |
| POST | `links/{id}/revoke/` | links:write | 200, link object |

Lists contain `{ "results": [...], "next_offset": 50 }`; request the next page with `?offset=50`. Page size is 50; `next_offset` is null at the end. Offsets are limited to 100,000. Concurrent changes may shift offset pagination. Reading a link does not count as a recipient-page opening.

## Collection and link creation

```sh
# LINKVAULT_API_KEY must already be set privately in your environment.
curl --fail-with-body https://files.example.com/api/v1/collections/create/ \
  -H "Authorization: Bearer $LINKVAULT_API_KEY" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: workflow-2026-0001-collection' \
  --data '{"title":"Client delivery","description":"Final documents","recipient_note":"Thank you."}'
```

Collection fields: `title` required (120 characters), `description` optional (4,000), `recipient_note` optional (300). The response includes `id`, these fields, `created_at` and `files`. File metadata contains `id`, `path`, `size` in bytes, `sha256` and `scanned`; storage paths are never exposed.

After finalizing files, create a link:

```json
{
  "label": "Client delivery",
  "expires_at": "2027-01-20T18:00:00+01:00",
  "max_downloads": 1,
  "allow_previews": false,
  "password": "a-separate-unique-passphrase"
}
```

`label` is required (80 characters). Use either `expires_at` or `expires_in_days` (integer 1–3,650). A duration is resolved to an absolute expiry when the link is created; idempotent retries return that original deadline. Omit both for no expiry. Expiry is optional, must be in the future, ISO 8601 **with timezone**. `max_downloads` is null/omitted for unlimited or an integer from 1 to 100,000. `allow_previews` is a JSON boolean, default false. Password is optional, with no minimum length and a maximum of 128 characters, never returned. Whitespace is significant. An empty collection or unscanned production files cannot be shared.

A link response contains `id`, `collection_id`, `label`, **`url`**, `status` (`active`, `expired`, `revoked`, `exhausted`), `password_protected`, `allow_previews`, `max_downloads`, `downloads` and `expires_at`. Treat the URL as a secret. Revoke is idempotent. Existing link conditions cannot be edited via this API: issue a replacement.

## Saved link batches

The [English walkthrough](guide/en/bulk-links-api.md), [French walkthrough](guide/fr/bulk-links-api.md) and [Python command-line client](../examples/batches.py) cover Codex/terminal commands, server-side website integration, reads and downloads. API batches immediately appear in **My links → My batches**. API and browser creation use the same records, atomic completion, ownership checks, file/antivirus rules and value-only export code.

POST `collections/{id}/link-batches/` with a required `Idempotency-Key` and:

```json
{
  "name": "October clients",
  "names": ["A", "B"],
  "conditions": {"expires_in_days": 7, "max_downloads": 1}
}
```

- `name`: required batch name, 1–80 characters on a single line.
- Supply exactly one of `names` (1–200 strings, each 1–80 characters on a single line) or `count` (integer 1–200, generating stable English labels `Link 001`, etc.). Names are trimmed; duplicate names remain separate links in their original order. Control characters, invalid Unicode and unknown request fields are rejected.
- Optional `conditions` accepts only `expires_at`, `expires_in_days`, `max_downloads`, `password`, `allow_previews`, with the same strict types and bounds as single-link creation. Without conditions: no expiry/password, unlimited downloads, previews false. Conditions apply independently to each link. Date and duration are mutually exclusive. The password is hashed once outside owner locks and is never returned/exported.
- JSON only, at most 256 KiB per request. The API does not accept workbook or CSV uploads on this endpoint. The original names become a `Name` source column, included in exports.

Both creation and detail responses contain `id` (batch UUID), `collection_id`, `name`, `state` (`complete`, `draft`, `expired`), `count` (source row count), ISO 8601 `created_at`/nullable `completed_at`, `view_url`, `api_url`, `exports`, `headers`, `source_rows` and `items`. Each item is a link object as described above, plus its **1-based `position`**, ordered like `names`. Its `id` is a link UUID, and its `label` is the current name. URLs in `exports.xlsx` / `exports.csv` require Bearer authentication; `view_url` requires a browser login. None of these management URLs grants anonymous access.

List responses omit `headers`, `source_rows` and `items`, use the usual 50-entry pagination, and include both API and browser batches. Optional `?collection_id=UUID` must refer to an owned, non-trashed collection. Drafts and expired drafts have an empty `items` list and `exports: null`; trying to export either returns 404. New API batches are completed directly, without a draft confirmation step. Trashed or deleted collections make their batches unavailable.

Exports contain original source columns, current link labels, URLs and UTC dates/status/counters. They use the same formula-neutralization rules as browser exports. Renaming a link in the interface is reflected by subsequent reads/exports without changing its URL. Reads and exports do not count as recipient opens/downloads. A saved export is a snapshot and never updates itself. No password is included. List deletion, renaming and link revocation remain available in the interface; the existing API also supports individual link revocation.

The shared limits are 10 preparations/hour/account (20/IP), 100 saved batches/account including trashed collections, and 60 exports/hour/account (120/IP). Browser imports have an additional preparse guard. Creating a batch uses one encrypted API receipt, in addition to general API limits. An idempotent replay precedes batch quota, preparation budget and date validation, so it still succeeds after the original link expiry; it does not create more links or events. Concurrent retries are serialized by the owner lock and recheck current token validity. A failure rolls back batch, links, associations, events, webhook deliveries and receipt together. Each created link emits its usual `link.created`, without private list data; no additional batch event or email is sent.

For a website, call from its authenticated backend with a server-side API key, authorize that site's user and collection, limit requests and do not expose credentials or unrestricted share URLs to browsers. Cross-origin browser calls are not enabled. Webhook-triggered workflows call this same API; outgoing webhooks are notifications, not incoming commands.

## Resumable file protocol

1. Create a collection.
2. POST a manifest to `collections/{id}/uploads/start/`:

```json
{"files":[{"path":"Design/notes.txt","size":18,"fingerprint":"64-lowercase-hex-characters-of-a-client-file-fingerprint"}]}
```

The fingerprint must actually be 64 lowercase hexadecimal characters; the example value above describes the field, it is not a valid checksum. The example client uses the full file SHA-256. Size is an integer in bytes (zero is allowed). Paths are relative POSIX paths; no traversal, hidden components, prohibited extensions, case-insensitive duplicates or file/directory collisions.

3. Manifest order is preserved in new sessions and the recipient file tree; relative paths are preserved in the ZIP. Empty folders have no manifest entry. Save the returned session `id`, `expires_at`, `chunk_bytes`, and each part’s `id`, `path`, `size`, `offset`, `fingerprint`. The server reserves quota before receiving bytes. At most five pending sessions per owner; 100 files per collection, 1 GiB per file and 5 GiB per collection by default. Trash and pending imports count toward these constraints.
4. For each part, POST raw bytes (1–`chunk_bytes`, default 4 MiB) to its `chunk/` route. Send `Content-Type: application/octet-stream`, exact `Content-Length`, `Upload-Offset` and `Upload-Checksum` (lowercase SHA-256 of this chunk). Read the returned next `offset`. For a zero-byte file, skip chunks.
5. On HTTP 409, use the response `offset` and resend from there; this handles a successful chunk whose response was lost. Status also reports durable offsets. Chunks are individually verified; the server computes its own final SHA-256. The client fingerprint is a resume hint, not a trusted malware or integrity attestation.
6. POST `{}` to `finish/`. The server checks completeness, antivirus, quotas and link immutability before committing the files. The response is `{ "state": "complete", "redirect": "/collections/…/" }`. Repeating finish does not duplicate files. Then create a link.
7. POST `{}` to `cancel/` to release an unfinished session. Canceling a completed session does not delete its files. Expired sessions are cleaned by maintenance after 24 hours.

Do not add files to a collection with active links. API and browser imports share the same admission, scanning, path validation and quota code. There is no URL-fetch import, direct public blob route, base64 JSON upload or automatic file deletion API.

## Idempotency and retries

Four creation routes require `Idempotency-Key`: collections, upload sessions, individual links and link batches. Use 8–128 letters, digits, `_` or `-`. Within one API key, an identical **path and raw request body** returns the original successful response for 24 hours, with `Idempotency-Replayed: true`. Reusing the key for different bytes/path returns 409 `idempotency_conflict`. Keep JSON serialization stable. Receipts are encrypted because responses may contain share URLs. A replay is a historical creation result: read the resource again for its current status, including after deleting a list.

Receipts are not permanent deduplication. Persist resource IDs and use status/read routes after 24 hours; do not blindly resubmit old creates. Per owner: 600 API requests/minute and 1,000 retained creation receipts/day. An additional IP ceiling is 1,200 requests/minute; upload starts/finalizations also have a 30/hour/IP guard. HTTP 429 includes `Retry-After`. Retry transient 429/502/503/504 with bounded backoff and the same idempotency key; never automatically repeat a new creation with a new key.

Errors generally use `{ "error": { "code": "…", "message": "…" } }`; form validation returns `error.fields`. Chunk offset conflicts additionally include top-level `offset`. Statuses: 400 invalid request, 401 invalid/missing key, 403 missing scope, 404 unavailable/not owned, 409 conflict, 413 request too large, 429 throttled, 503 unavailable storage. Reverse proxies may return their own error pages. Never log Authorization headers, passwords or whole link responses.

## Outgoing webhook contract

Managed through **Automations**, separately from API keys. See the [illustrated setup, verification and retry guide](guide/en/automation.md).

```json
{
  "id": "7582182b-93f0-433a-a721-75b08908730c",
  "type": "link.created",
  "created_at": "2026-10-22T10:30:00+00:00",
  "data": {"collection_id": "UUID", "link_id": "UUID"}
}
```

Collection/upload events contain only `collection_id`; link events also contain `link_id`. The test event contains `message: "Liasio test"`. Verify HMAC-SHA256 over `timestamp + "." + exact raw body` using the signing secret bytes, reject timestamps outside ±300 seconds, and deduplicate `id` durably. [Receiver verifier](../examples/verify_webhook.py) · [Upload client](../examples/upload.py).

Security rationale: [OWASP SSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html). Each attempt revalidates all DNS answers, pins a public address while verifying TLS for the original hostname, ignores environment proxies and never follows redirects. A 20-second deadline covers DNS, connection, TLS and response headers. No response body is read or retained. Protect database and encryption keys in backups.
