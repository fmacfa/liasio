# Liasio roadmap

## Delivered in 0.5

Team spaces and groups, folder/space ownership transfer, mandatory team MFA and active-session controls; immutable versions, explicit replacement/restoration and selective downloads; sharing presets; reviewed email drafts/templates/notifications, verified recipients and upload-only client requests; recipient lists through API with Excel/CSV export; private S3, explicit OIDC identity binding, encrypted off-server restic backups, diagnostics, OpenAPI/explorer, browser/network tests and EN/FR/DE documentation. See [CHANGELOG.md](CHANGELOG.md) for details and [upgrade instructions](docs/UPGRADING_0_5.md) before installation.

## Before 1.0

- Independent human security review and remediation/retest, using [the prepared audit brief](docs/INDEPENDENT_AUDIT.md).
- Deployment-specific benchmarks on several VPS sizes and a human recovery exercise onto a separate host. Current automated measurements are bounded test scenarios, not a capacity certification.
- Evaluate usability/accessibility with real users and document operational support expectations.

## Future design work

Passkeys; a separate download tier and distributed upload staging; additional languages and preview formats; optional folder-retention policies independent of link expiry; additional integration events/connectors; and end-to-end encryption with a deliberate key/recovery design. These are not represented as existing 0.5 features. End-to-end encryption would change server-side antivirus and preview capabilities.

## Principles retained

Private-by-default accounts and folders, explicit upload-only capabilities rather than open anonymous storage, no download consumption on page views, no claims of guaranteed receipt, no advertising tracking and documented failure/recovery behavior.
