# Security policy

**English** · [Français](SECURITY.fr.md)

The latest published 0.5 release receives fixes; older minor releases should be upgraded. This pre-1.0 project has not undergone an independent human security audit. See [maintenance and release policy](docs/MAINTENANCE.md).

## Private disclosure

Use [GitHub private vulnerability reporting](https://github.com/fmacfa/liasio/security/advisories/new). Do not open public issues containing credentials, real sharing URLs, customer files or exploitation instructions against a live installation. Provide the immutable version/commit, an isolated reproduction, expected/observed behavior and impact. Test only systems you are authorized to examine. There is no guaranteed response-time SLA.

## Sensitive boundaries

Review setup, invitations, password recovery, MFA/OIDC, session revocation, group/individual authorization, ownership transfer, upload-only capabilities, path traversal, antivirus, disk/quota admission, immutable versions, link capabilities/passwords/recipient codes, concurrency, API scopes/replays, SSRF-resistant webhooks, approved email queues, private object storage, exports, deletion and recoverability. Deployment scripts and native preview isolation are in scope too.

The [security model](docs/SECURITY_MODEL.md), [0.5 upgrade guide](docs/UPGRADING_0_5.md) and [independent audit brief](docs/INDEPENDENT_AUDIT.md) describe intended properties and review evidence. A passing automated check does not prove the absence of vulnerabilities.

Files are readable by the server/operator; this is not end-to-end encryption. Recipients can re-share content they have received. Revocation blocks future authorizations and cannot erase copies or necessarily stop an already-authorized download. Email acceptance and download authorization are not proof of final receipt. These are documented product limits, not promises of stronger guarantees.
