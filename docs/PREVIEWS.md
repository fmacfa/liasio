# File previews

Liasio 0.3 can show a gallery before the recipient downloads the folder. Select **Afficher les aperçus des images et PDF** when creating a link. It is checked for new links. Links created before the upgrade stay disabled until their owner chooses **Activer les aperçus** in **Mes liens**. The owner can turn them off again there.

## What recipients see

- JPEG, PNG, WebP and GIF: a reduced still image, up to 1600 × 1600 pixels, preserving aspect ratio. Animated files show their first frame.
- PDF: a rasterized image of its first page. The viewer does not open the original PDF, run PDF scripts or expose attachments, links or additional pages.
- Other formats, files larger than 20 MiB, images above 24 megapixels, malformed files and failed renders: filename, format and size, with a fallback card. The original remains available through the normal ZIP download if the link is valid.
- The lightbox supports previous/next buttons, arrow keys, Escape, focus return and mobile layouts. Without JavaScript, an eligible preview link opens the generated JPEG directly.

An authorized preview **reveals content and can be copied**. It is not a DRM mechanism. Opening previews does not consume a download or create download events. Single-use links still permit preview views before their one authorized ZIP download. Disable previews when content should not be visible before downloading. Expired, exhausted or revoked links cannot request further previews; bytes already received cannot be recalled.

## Authorization and data boundaries

Every image request checks the signed sharing token, its preview setting, the owner's active state, expiry, revocation, remaining uses, password grant and the file's membership in the collection. Files in trash are excluded. Mandatory antivirus also applies to preview eligibility. The complete access check runs again after rendering, before returning any image bytes. Responses use `image/jpeg`, `nosniff`, `same-origin` resource policy and `no-store, private`; originals are never exposed by the preview route.

Only JPEG derivatives cross back to the application. A fresh RGB canvas removes source EXIF, XMP, comments and color profiles. The original upload is unchanged. Parsing remains a security-sensitive native-code operation: keep Pillow, pypdfium2/PDFium, base images and the host updated.

## Deployment

Use the 0.3 Compose file. The `preview` service uses the same application image, but receives **no `.env`, credentials, database connection or original-file volume**. It has no network, a read-only root filesystem, no Linux capabilities, a non-root UID, one CPU, 512 MiB memory and a 96 MiB temporary filesystem. Its only persistent mount is the private Unix socket shared with the application. It accepts one bounded input at a time and deletes request scratch files afterwards.

Each render starts a disposable decoder process with an eight-second wall timeout, five CPU seconds, a 384 MiB Linux address-space limit and a 2 MiB output limit. The application serializes generation across its workers and applies request/account rate limits and temporary disk admission. A missing or busy renderer produces an unavailable preview; the upload and ZIP download paths continue working.

The hardening update adds a per-request **Landlock** filesystem policy before importing the native decoders. The child may read its Python/native runtime and read/write its own temporary request directory. It cannot create, replace, truncate or remove entries in the broker's socket directory, even though the two processes use the same UID. On ABI 6+ it also cannot signal processes outside its sandbox. The container network and resource limits remain necessary. Production requires **Landlock ABI 3+**, normally Linux 6.2+ with Landlock enabled; unsupported kernels or denied sandbox syscalls fail closed for previews, with no unsandboxed production fallback. Verify real image and PDF rendering after upgrading. See the [kernel Landlock documentation](https://docs.kernel.org/userspace-api/landlock.html).

For a prebuilt upgrade, set `LINKVAULT_IMAGE=ghcr.io/fmacfa/linkvault:0.3.0` in the existing `.env`, then:

```sh
docker compose pull app maintenance preview
docker compose up -d --no-build
```

Preserve the existing database, file volumes and keys. The application applies the preview-setting migration at startup. The socket volume contains no originals and need not be backed up. An older Compose file must be updated to include the renderer and its volume before previews can work.

`PREVIEWS_ENABLED=false` disables preview eligibility globally. Compose provides `PREVIEW_SOCKET=/run/linkvault-preview/render.sock` to the application. A custom production deployment must provide an equivalently isolated renderer on that Unix socket; production never falls back to local decoding. With `DEBUG=true` only, local development can invoke the bounded decoder subprocess directly. That development mode does **not** provide Docker's filesystem/network sandbox.

## Cache, deletion and tests

Derivatives are private, regenerable files under `data/tmp/previews`. They expire after 24 hours and are bounded to 256 MiB and 256 entries. Failed render markers expire after one hour to prevent repeated expensive parsing. The cache is not charged to individual file quotas and is excluded from backups. Disk monitoring includes its physical footprint. Permanent file/account erasure clears corresponding entries; maintenance expires the remainder. Cache hits always require fresh authorization.

`vault/tests/test_previews.py` covers access controls, password grants, revocation during rendering, single-use counters, cache bounds and purge, metadata stripping, format/size rejection, local production-fallback refusal and real JPEG/PDF conversion through the Unix protocol. Container CI additionally checks the deployed sidecar's real conversion and its isolation settings. This is focused regression coverage, not a new independent security audit.
