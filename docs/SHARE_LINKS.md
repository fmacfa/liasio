# Short sharing URLs

Sharing URLs use an opaque 22-character code after `/s/`. The previous 43-character signed tokens and 97-character `uuid.signature` format still work. All three representations refer to the same link record. No external shortening service is involved. On `https://linkvault.arc-del.com`, the full URL is 55 characters, including the trailing slash, instead of 76 or 130.

Copying an existing link again, reading it through the API or exporting its batch returns the short URL. Saved spreadsheets do not change automatically. API idempotency receipts replay the original response, including an older URL, for their existing 24-hour lifetime. Clients must treat the returned URL as opaque rather than parsing or constructing the token. Invitation URLs continue to use 43-character tokens and accept their previous 97-character format; share codes cannot be used as invitations.

## Opaque 22-character codes

- A code encodes the leftmost 16 bytes of HMAC-SHA256 using canonical Base64url without padding (128 bits become 22 characters).
- Its MAC input is `linkvault:alias:v1:share:UUIDHEX`, keyed by the instance's `SECRET_KEY`. UUID hex is lowercase without hyphens. This separate namespace prevents confusion with either previous format or invitation tokens.
- Unlike the 43-character format, the UUID is not included in the URL. A unique indexed `short_code_digest` column stores SHA-256 of the code to locate the underlying link. Neither knowing its UUID nor reading this digest provides the bearer code.
- After looking up the digest, validation also recomputes the HMAC under the current key and compares it in constant time. A stale or corrupted lookup cannot grant access to a different record.
- Validation restricts the length, alphabet and final padding bits before any database lookup. Unknown, modified and noncanonical codes fail closed. Codes retain 128 bits of unpredictability; the shorter URL removes the embedded UUID, rather than further truncating the 128-bit value. See [RFC 2104, section 5](https://www.rfc-editor.org/rfc/rfc2104.html#section-5).

## Previous signed formats

- A compact token encodes a 16-byte UUID followed by the leftmost 16 bytes of HMAC-SHA256, using canonical Base64url without padding (32 bytes become 43 characters).
- Its MAC input is `linkvault:compact:v1:PURPOSE:UUIDHEX`, keyed by the instance's `SECRET_KEY`. `PURPOSE` is `share` or `invite`; they are not interchangeable. UUID hex is lowercase, without hyphens.
- The 128-bit authentication tag follows the truncation guidance in [RFC 2104, section 5](https://www.rfc-editor.org/rfc/rfc2104.html#section-5). It is shorter than the legacy 256-bit tag; this is not merely a change of text encoding. Knowing a database UUID does not provide a valid tag.
- Legacy validation retains the original full HMAC-SHA256 over `linkvault:PURPOSE:UUIDHEX`. It does not accept a truncated legacy signature.
- Validation bounds the token length, restricts the alphabet and compares the canonical signed encoding in constant time. Added padding, alternate padding bits, malformed encodings and modified signatures fail closed.

All codes and tokens are derived on demand, never stored in plaintext in the database. Rotating `SECRET_KEY` invalidates all three representations. Keep that key stable across normal upgrades and restores.

After an intentional key rotation, run `python manage.py refresh_share_urls` using the new key before distributing new short URLs. The Docker application entrypoint runs this automatically after migrations. The command updates only changed digests in bounded batches; it does not change UUIDs, access rules or counters, and never prints tokens. Restart all application and worker processes with the same key. Until the lookup is refreshed, copying a link returns a working 43-character token under the current key, without writing to the database during reads. Old URLs remain invalid after key rotation.

## Shared access rules

Password grants, activity deduplication and download accounting use the underlying link UUID. Switching URL representations does not reset a download limit or create another access grant. Expiration, revocation, account suspension, trash state, preview permissions and branding access apply to all three formats. Invitations remain bound to their email address and single-use across their two formats. Existing CSRF checks, rate limits and `/s/` access-log redaction remain in place.

## Deployment and rollback

Back up the database, files and original keys before upgrading. Migration `0010` adds the nullable unique digest field and populates it for existing links in bounded batches. It does not create new share links or change their existing fields. Individual and batch creation populate the lookup for new links. Apply migrations and run `refresh_share_urls` for a manual deployment; Docker does both at startup. The digest column is included in normal database backups.

Update all application processes together before distributing short URLs. Older releases can still write to the additive schema, but cannot open 22-character URLs. A rollback must retain the short-code decoder and digest column once these URLs have been shared; keep a compatible build available. A new build can repair null lookups left by an older writer with `refresh_share_urls`.

Regression coverage includes fixed wire vectors, every bit of both newer formats, purpose separation, canonical encoding, unique lookup collisions, corrupted lookups, key rotation and refresh, migration of existing records, password grants and single-use accounting across formats, previews, branding, invitations, API retries and Excel/CSV exports.
