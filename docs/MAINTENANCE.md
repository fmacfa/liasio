# Maintenance and release policy

Liasio is an MIT community project with no response-time or availability SLA. The latest published minor release receives fixes; older minor releases should be upgraded. Maintainers review dependency/container updates, run the full CI and publish versioned images. Pin immutable image digests for reproducible deployments, while arranging reviewed updates so a pinned image does not silently age.

Before a release: apply schema changes only through migrations; test SQLite and PostgreSQL; test real browser desktop/mobile flows, interrupted uploads, antivirus and preview isolation; run dependency/static analysis, locale/document checks, a full restore and image attestation validation. Record the exact source and build digests, the scope of measured load tests, upgrade instructions and known limitations. Never claim an independent audit based on these automated checks.

Operators should check service alerts and backup failures daily, review dependencies and restore readiness regularly, and rehearse restoration on a separate empty host. Schedule frequency according to data change rate and acceptable recovery time. Monitor actual peak RAM, disk, queue delay, scanning and download latency before increasing limits; there is no certified concurrent-user capacity. The README hardware figures are sizing allowances, not benchmark guarantees.

API v1 field additions preserve existing clients. Breaking contract changes require an explicit migration/deprecation plan. Original `linkvault` deployment identifiers and signing namespaces remain stable despite the Liasio rename. Sharing revocation prevents future authorization; it cannot recall previously downloaded files or guarantee stopping an already-authorized response.

Before 1.0: complete the independent human review, publish deployment-specific capacity measurements on multiple VPS sizes and a human disaster-recovery exercise. Passkeys, additional languages, end-to-end encryption, distributed staging and a separate download tier remain future design work rather than hidden promises in 0.5.
