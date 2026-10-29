# Independent audit brief — before 1.0

**Status: not performed.** This document prepares an external human review. Automated tests, dependency scans and previous agent-assisted reviews are not an independent audit or a guarantee of security.

## Scope and environment

Audit the immutable v0.5 release commit and container digests, including migrations and upgrade compatibility. Use an isolated PostgreSQL instance, fictional accounts/files, a mock SMTP/IdP/S3 service and a networkless preview worker. Do not test an existing customer installation without explicit authorization. Preserve a reproducible environment and disclose issues through [SECURITY.md](../SECURITY.md).

## Required trust-boundary checks

| Boundary | Properties to challenge | Automated evidence |
| --- | --- | --- |
| Team spaces | Group administrators cannot browse arbitrary folders; grants and ownership transfers serialize with writes; quotas follow ownership | `test_workspaces`, `test_team_access` |
| Versioned files | Replacements do not change pinned shares; current/history/trash stay accounted; restoration and purge cannot cross owners | `test_versions` |
| Public sharing | Capability, password and email-code gates apply to page, image and download; invalid selection never consumes a use | `test_mail`, `test_previews`, `test_security` |
| Upload-only requests | Browser-scoped sessions, lifetime byte/count limits, revocation, malformed paths, checksum/scan failure and disk admission | `test_file_requests`, `test_reliability` |
| Email delivery | Explicit approval, encrypted capabilities at rest, bounded/fair dispatch, retries and current authorization | `test_mail` |
| Enterprise identity | Exact issuer, signed ID token, audience, expiry, nonce, state, PKCE, explicit account binding and MFA | `test_oidc` |
| Object storage | Private originals, no direct URLs, bounded streaming, missing-object failure, portable recovery | `test_object_storage` |
| API / webhooks | Scopes, revoked credential replay, idempotency races, SSRF/DNS rebinding, owner fairness and signatures | `test_automation`, `test_batch_api`, webhook/security tests |
| Operational recovery | Fresh/upgrade setup, original keys, backup consistency, offsite encryption/retention, empty-target restore | container CI, `test_offsite_backup` |

Review both source-to-sink paths and runtime behavior. Run concurrency tests on PostgreSQL; SQLite alone is not evidence of row-lock behavior. Verify SMTP/identity-provider integration against the deployment's actual configuration. Consider dependency and native decoder vulnerabilities, intentional recipient re-sharing, disk exhaustion, interrupted operations and worker death.

## Expected external deliverables

Record exact scope, exclusions, reviewer independence, findings with severity and reproducible evidence, remediation commits, retest results and remaining risks. A signed or attributable report may be published only with the reviewer and maintainer's agreement. Keep validated exploitable details private until remediation/release coordination is complete. Track open items before declaring a 1.0 readiness decision.
