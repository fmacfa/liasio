# Compact sharing and invitation URLs

The current source build generates 43-character URL-safe tokens for `/s/TOKEN/` and `/invite/TOKEN/`. The previous 97-character `uuid.signature` format is still accepted. Both representations refer to the same database record. No migration, new link records or external shortening service is needed.

Copying an existing link again, reading it through the API or exporting its batch returns the compact URL. Saved spreadsheets do not change automatically. API idempotency receipts replay the original response, including an older URL, for their existing 24-hour lifetime. Clients must treat the returned URL as opaque rather than parsing or constructing the token.

## Authentication format

- A compact token encodes a 16-byte UUID followed by the leftmost 16 bytes of HMAC-SHA256, using canonical Base64url without padding (32 bytes become 43 characters).
- Its MAC input is `linkvault:compact:v1:PURPOSE:UUIDHEX`, keyed by the instance's `SECRET_KEY`. `PURPOSE` is `share` or `invite`; they are not interchangeable. UUID hex is lowercase, without hyphens.
- The 128-bit authentication tag follows the truncation guidance in [RFC 2104, section 5](https://www.rfc-editor.org/rfc/rfc2104.html#section-5). It is shorter than the legacy 256-bit tag; this is not merely a change of text encoding. Knowing a database UUID does not provide a valid tag.
- Legacy validation retains the original full HMAC-SHA256 over `linkvault:PURPOSE:UUIDHEX`. It does not accept a truncated legacy signature.
- Validation bounds the token length, restricts the alphabet and compares the canonical signed encoding in constant time. Added padding, alternate padding bits, malformed encodings and modified signatures fail closed.

Tokens are derived on demand, not stored in plaintext in the database. Rotating `SECRET_KEY` invalidates both representations, as before. Keep that key stable across normal upgrades and restores.

## Shared access rules

Password grants, activity deduplication and download accounting use the underlying link UUID. Switching URL representations does not reset a download limit or create another access grant. Expiration, revocation, account suspension, trash state, preview permissions and branding access apply to both formats. Invitations remain bound to their email address and single-use across formats. Existing CSRF checks and rate limits are unchanged.

## Deployment and rollback

Update all application processes together before distributing compact URLs. A previous release without this decoder cannot open the new format. A rollback must retain compact decoding if these URLs have already been shared; keep a compatible build available. Backups and database schemas remain compatible.

Regression coverage includes fixed wire vectors, every bit of the compact UUID/tag, purpose separation, canonical encoding, key rotation, old/new password grants and single-use accounting, previews, branding, invitations, API retries and Excel/CSV exports. The ordinary sharing and batch tests exercise the new default format.
