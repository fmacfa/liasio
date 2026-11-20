# Updating Liasio from the administration page

Open **My links → Liasio updates** or **Operations → Liasio updates** as an instance administrator. **Check GitHub** reads the latest published stable release from the public `fmacfa/liasio` repository. Click the version number to read the release notes. **Install** queues the displayed version. Reload the page to see the result. Other members can continue sharing normally until the brief maintenance screen appears.

The optional Linux host agent polls for requests about once a minute and checks GitHub daily at 03:00 UTC (or on the first poll after that time). Checks work without a GitHub account or token. Installation is manual by default. **Enable automatic installation** authorizes newer patches in the installed major/minor series, e.g. 0.5.1 → 0.5.2. A switch to 0.6 needs a click on **Install**. A failed patch requires operator review before another attempt; check `journalctl -u liasio-updates.service`. A check failure retains the last successful result.

## Enable the optional server agent

The agent supports the project's single-host Docker Compose installation with its PostgreSQL database, local named file volume and Docker's standard Linux amd64 or arm64 images. The host runs Python 3.11+, Git, curl, Docker Compose v2+ and a recent GitHub CLI supporting `gh attestation verify --bundle --source-digest --signer-digest`. The web container never needs systemd privileges, a Docker socket or a GitHub token.

Use a root-owned deployment directory. The default is `/opt/linkvault`, Compose project `linkvault`. Its `current` symlink points to a Git checkout in `releases/`. Keep the original private environment file in `config/.env`, including a single explicit `LINKVAULT_IMAGE=...` line. The checkout's `.env` and `backups` point to `config/.env` and the root's private `backups/`. An existing CloudPanel override is picked up at `config/compose.cloudpanel.yaml`. Retain the existing Compose project and volume names. Only enable the agent on a layout that matches your installation. Other installations can use the version check and the [operator upgrade procedure](UPGRADING_0_5.md).

From a verified release checkout, as the host administrator:

```sh
install -d -m 0755 /usr/local/lib/liasio-updates
install -m 0644 scripts/managed_update.py vault/release_channel.py /usr/local/lib/liasio-updates/
install -m 0644 docker/liasio-updates.service docker/liasio-updates.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now liasio-updates.timer
systemctl start liasio-updates.service
```

For a custom deployment directory or project, override the service's ExecStart and use `--root /your/path --project project-name tick`. To schedule the same worker with root's cron instead of the systemd timer, use the following entry. Use one scheduler per installation.

```cron
* * * * * /usr/bin/python3 /usr/local/lib/liasio-updates/managed_update.py tick
```

## What happens during an installation

1. The host rechecks the requested stable version on GitHub. It checks the portable image checksum and verifies GitHub's build attestation against the release workflow, tag and exact source commit before loading the image.
2. It rehearses the new release against the last backup in an isolated Compose project when a backup exists. A first installation rehearses against the fresh snapshot. Backups and active transfers have priority.
3. After stopping Liasio's writers, it takes a fresh disk and database backup with the original configuration. It restores the database under a temporary name, applies migrations there, then verifies file hashes, quotas and keys with read-only access to the live original files.
4. It exchanges the database names in one PostgreSQL transaction and health-checks the new application while external traffic sees a maintenance screen. The previous database and configuration are retained. After reopening the site, recovery proceeds forward to protect newly accepted writes.
5. The timer can finish an interrupted update from the host journal. Before reopening, a failed cutover restores the previous database, application and workers. Infrastructure image changes and non-local object storage require the operator workflow. The privileged host helper itself is updated by the host administrator from a reviewed release.

Keep room for a fresh private backup, a second database and the temporary full restore. The backup and verification time increases with stored data. Keep a regular independently verified backup; periodically retire retained `liasio_before_…` databases and old images under your retention policy after checking recoverability.
