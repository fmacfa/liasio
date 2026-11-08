# Projects reviewed and design decisions

**English** · [Français](COMPARISON.fr.md)

Documentary review dated October 21, 2026, based on official READMEs and documentation. This is neither a security audit of the projects listed nor a comparative performance test.

| Project | What interests us | Decision for Liasio |
| --- | --- | --- |
| [Pingvin Share](https://github.com/stonith404/pingvin-share) | Quick sharing, expiry, passwords, visitor limits, reverse shares, ClamAV and local/S3 storage | Keep the workflow simple; distinguish a visit from download authorization. The original README says the project is archived and points to a fork; do not depend on this code. |
| [ProjectSend](https://github.com/projectsend/projectsend) | Client/group management, logs, themes, quotas, directory trees and ZIP downloads | Separate accounts and their resources; give collections a presentation suited to professional exchanges. |
| [Gokapi](https://github.com/Forceu/Gokapi) | Time/download expiry, roles, file requests, deduplication, encryption options and S3 | Put expiry, quotas and consumption at the center of the model; plan file requests and large volumes for later work. |
| [Send](https://github.com/timvisee/send) | End-to-end encryption and ephemeral sharing; community continuation of Firefox Send | Document the trust model candidly. The Liasio v0.1 server reads files to scan them and assemble ZIPs; E2EE would require a separate architecture. |

<a id="apport-visé"></a>
## Intended contribution

Liasio combines a collection kept in a library, multiple links with independent conditions, per-account presentation and an activity log that names exactly what it measures. These choices meet our needs; listing them here does not imply they are absent from the other projects.

The **Storage** page completes the workflow: inspect real files, sort the largest, measure recoverable space and confirm deletion that also revokes the affected access.

Verification targets difficult functional contracts: cross-account authorization, quota races, simultaneous single-use access, revocation before delivery, antivirus failure and rejection of unauthenticated uploads before reading their bodies.

<a id="licences-et-provenance"></a>
## Licenses and provenance

ProjectSend's README specifies GPLv2 or later, Gokapi's AGPL3 and Send's MPL 2.0. We reused no code, screenshots or graphic assets from these projects. Liasio's interfaces and implementation are new and distributed under MIT; third-party libraries retain their own licenses.

<a id="références-de-sécurité-utilisées"></a>
## Security references used

- [Django — security](https://docs.djangoproject.com/en/5.2/topics/security/): escaping, CSRF, hosts and HTTPS.
- [Django — CSRF and referrer policy](https://docs.djangoproject.com/en/5.2/ref/csrf/#removing-the-referer-header): retain same-site checks while preventing links leaking to third-party sites.
- [OWASP — File Upload Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html): private storage, sizes, names and defense in depth.
- [OWASP — Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html): adaptive Argon2id hashing.
- [ClamAV — protocol and scanning](https://docs.clamav.net/manual/Usage/Scanning.html): streaming scans and daemon-port protection.
- [ClamAV — Docker](https://docs.clamav.net/manual/Installing/Docker.html): memory, signatures and image versions.
- [Caddy — request body limits](https://caddyserver.com/docs/caddyfile/directives/request_body): proxy-level upload limits.
