# Private S3 storage and OIDC

These are optional server-side settings. Keep the local backend and password login unless you need them. Both are covered by isolated automated integration tests; the tests do not certify a particular cloud provider or identity-provider configuration.

## Private S3-compatible originals

Create a dedicated private bucket. Enable **Block Public Access**, disable public policies/ACLs, and use a service identity limited to `GetObject`, `PutObject`, `DeleteObject` and `ListBucket` for that bucket. Use versioning/lifecycle rules to cover accidental operator deletion and incomplete multipart uploads. Bucket retention must match your deletion policy; object-store versioning can retain copies after Liasio deletes a file.

```dotenv
STORAGE_BACKEND=s3
S3_BUCKET=your-private-bucket
S3_REGION=eu-central-1
# Optional for a compatible provider. HTTPS required in production.
S3_ENDPOINT=https://objects.example.com
S3_ADDRESSING_STYLE=path
S3_ENCRYPTION=AES256
AWS_ACCESS_KEY_ID=configure-privately
AWS_SECRET_ACCESS_KEY=configure-privately
```

AWS IAM roles are also supported through the SDK credential provider chain. For KMS use `S3_ENCRYPTION=aws:kms`, `S3_KMS_KEY_ID`, and the corresponding KMS permissions. Never put these credentials in a public issue or repository. Settings follow the [django-storages S3 backend](https://django-storages.readthedocs.io/en/latest/backends/amazon-S3.html); Liasio overrides access to stream through application authorization and never returns signed/public original URLs.

All uploads are scanned before storage. Objects are immutable, encrypted by the storage provider and carry an opaque internal key. Passwords, OTPs, expiry and download limits remain enforced by Liasio. Each download streams into a locally reserved archive or original-file response. Local POSIX storage is still required for upload staging, previews, temporary downloads, locks and restore tests. One application host is supported; S3 alone does not make staging safe across multiple hosts.

### Move existing originals without changing links

Back up first and stop all writers. Configure S3 settings/credentials while keeping `STORAGE_BACKEND=local`:

```sh
docker compose run --rm --no-deps app python manage.py copy_storage s3
docker compose run --rm --no-deps app python manage.py copy_storage s3 --apply
```

The first command is a dry run. The second preserves every blob key and verifies size/SHA-256, including trash and superseded versions. It never removes source files. Only after verification set `STORAGE_BACKEND=s3`, restart the same release and run `verify_storage`. Retain the old volume until a complete backup/restore has passed. To move back, use `copy_storage local --local-directory /absolute/empty/files --apply`, verify, then point local storage at that directory.

Automatic orphan sweeping covers the local file volume only. On S3, failed writes can leave orphan objects; investigate using database keys and your provider inventory before deleting anything. Never apply a bucket-wide deletion or lifecycle age rule to all active originals.

## OIDC enterprise login

Register a confidential web client with your trusted provider:

- Redirect URI: `https://your-domain/oidc/callback/`, exactly.
- Authorization Code flow, PKCE S256, scopes `openid profile email`.
- HTTPS discovery, token and key endpoints; asymmetric ID-token signatures.
- No implicit flow, public registration or automatic administrator provisioning.

```dotenv
OIDC_ISSUER=https://identity.example.com/realms/your-team
OIDC_CLIENT_ID=your-client-id
OIDC_CLIENT_SECRET=configure-privately
```

Use the issuer's exact spelling, including a trailing slash if its discovery document contains one. Liasio verifies issuer, audience, signature, expiry, state and nonce, using [Authlib's Django integration](https://docs.authlib.org/en/latest/oauth2/client/web/django.html).

Members first join through an invitation and set a local password. In **My account → Enterprise sign-in**, they re-enter that password and, if enabled, a current MFA/recovery code, then authenticate with the provider. The verified `(issuer, subject)` is bound to that account; an identical email address never links accounts automatically. Each provider identity can belong to only one account. Future SSO logins still require Liasio TOTP when enabled, and suspended users remain blocked.

Unlinking requires local reauthentication. Keep password login and recovery available in case the provider is unavailable. Liasio stores the identity binding, not access/refresh/ID tokens. Logging out closes the Liasio session, not the provider session; disconnect other devices from **Active sessions** or the provider's controls.
