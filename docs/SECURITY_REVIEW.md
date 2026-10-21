# Pre-release source review — 0.2.0

On 21 October 2026, Codex Security reviewed the complete pre-remediation working tree: 119 tracked or unignored files, including deployment, documentation and test fixtures. The process used a separate baseline reviewer, an architecture reviewer, a focused resource reviewer and parent validation. It was an offline automated source review, **not an independent human audit, penetration test or certification**. Runtime secrets, private data, Git history and external deployment settings were excluded.

Three source-backed issues were identified and corrected before publishing 0.2.0:

| Issue | Assessed severity | Correction and regression evidence |
| --- | --- | --- |
| A stale password-form save could overwrite concurrent suspension, MFA or quota changes | High | Both password forms lock/reload the user, reject changed credentials, session/reset authority or MFA version, and write only the password field. Deterministic interleaving tests cover both forms, suspension, counters, enrollment and consumed second factors. |
| Classic multipart files reached temporary disk before capacity/quota admission | Medium | Admission now happens before multipart/CSRF parsing, with a per-account concurrency cap and actual-byte enforcement. Tests prove an exhausted account or insufficient disk is rejected before the temporary file handler starts. |
| Parallel share requests could prepare full ZIPs without shared disk admission | Medium | A global temporary-byte budget covers ZIP creation and streaming, together with upload reservations. Tests verify pre-allocation refusal, no use consumption on admission failure, release after response close/build failure, abandoned-worker recovery and simultaneous admissions. |

The final download-use update remains atomic. The review did not establish extra authorized uses of a single-use link or cross-account file access. Its two resource findings concerned temporary allocation before that authorization.

`vault/tests/test_admission.py` contains the new regression cases. The full local suite passes 86 tests; two opt-in live antivirus tests run separately in the container CI job. CI also repeats the application tests against PostgreSQL, exercises real ClamAV, transfers four concurrent 256 MiB files with complete digest verification, and restores a real backup in disposable PostgreSQL/file volumes. Consult the checks for the exact release commit for their results.

The local load probe is reproducible through `scripts/load_test.py`; its limits and environment are recorded in [RELIABILITY.md](RELIABILITY.md). Capacity admission assumes one host and a local filesystem with POSIX lock support. It cannot reserve space against unrelated host processes or replace infrastructure monitoring.

The detailed pre-fix scan is retained as a local Codex Security artifact. Its original findings describe the reviewed snapshot; this document records subsequent corrections. No claim is made that automated review establishes the absence of all vulnerabilities. Please report new vulnerabilities using [SECURITY.md](../SECURITY.md).
