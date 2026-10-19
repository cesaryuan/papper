# Installer download hosting

[Development](../DEVELOPMENT.md) · [Release packaging](RELEASING.md)

`https://papper.cesaryuan.top/` is the static download entry point for Papper.
The service uses the existing NPMplus instance on the `zouter-hk` SSH host;
it does not require a second web server or an application backend. Native
wheels continue to be published to PyPI through the existing release workflow.

Cloudflare holds the DNS zone, but the Papper A record uses **DNS only** and
points directly to this server. The previously proxied record returned HTTP 521
even after origin HTTPS worked. Other records in the zone were not changed.
The original Papper record is backed up privately on the host at
`/root/npmplus/data/backups/papper-dns-before-20261019.json`.

## Published content

The landing page source is
[`tools/deploy/papper-static/index.html`](../tools/deploy/papper-static/index.html).
The standalone installers are [`install.sh`](../tools/deploy/papper-static/install.sh)
and [`install.ps1`](../tools/deploy/papper-static/install.ps1). They select a
platform wheel from a Simple index, verify its SHA-256, and never need
Python, uv, or administrator permissions. Automatic source selection queries
Cloudflare trace, then IPinfo if needed, with a two-second timeout per lookup.
Only a confirmed `CN` public IP uses `https://mirrors.cernet.edu.cn/pypi/web/simple`;
other countries and failed lookups use official PyPI. An unavailable automatically
selected CERNET project index falls back to official PyPI. Explicit index settings
skip geolocation and retain their normal failure behavior. Proxy/VPN exits affect
the country reported by the services. Keep the scripts and landing page in
the same release when publishing them.

The document root is `/root/npmplus/data/static/papper` on the host and
`/data/static/papper` inside the existing container. Only `/`, `/index.html`,
`/install.sh`, and `/install.ps1` are allowed. Other paths return 404, directory
listing is disabled, and methods other than GET and HEAD are rejected.
Keep secrets, backups, and unpublished files outside the document root.

The host sets `Cache-Control: no-cache`, requires HTTPS, and uses a separate
Let's Encrypt certificate managed by NPMplus. Certificate issuance and renewal
use the existing Cloudflare DNS challenge integration with a 120-second
propagation interval. No DNS credential belongs in this repository.

## Publish the landing page and installers

From the repository root, upload to a temporary name, then publish the complete
set with same-directory renames. The temporary upload paths are not public:

```powershell
scp tools/deploy/papper-static/index.html zouter-hk:/root/npmplus/data/static/papper/index.html.next
scp tools/deploy/papper-static/install.sh zouter-hk:/root/npmplus/data/static/papper/install.sh.next
scp tools/deploy/papper-static/install.ps1 zouter-hk:/root/npmplus/data/static/papper/install.ps1.next
ssh zouter-hk "chmod 644 /root/npmplus/data/static/papper/*.next && mv /root/npmplus/data/static/papper/index.html.next /root/npmplus/data/static/papper/index.html && mv /root/npmplus/data/static/papper/install.sh.next /root/npmplus/data/static/papper/install.sh && mv /root/npmplus/data/static/papper/install.ps1.next /root/npmplus/data/static/papper/install.ps1"
curl.exe --noproxy '*' -fsS --resolve papper.cesaryuan.top:443:140.150.236.210 https://papper.cesaryuan.top/
curl.exe --noproxy '*' -fsS --resolve papper.cesaryuan.top:443:140.150.236.210 https://papper.cesaryuan.top/install.sh | sh -s -- --help
curl.exe --noproxy '*' -fsS --resolve papper.cesaryuan.top:443:140.150.236.210 https://papper.cesaryuan.top/install.ps1 -o $env:TEMP\papper-install.ps1
```

Updating static files does not need a container restart or Nginx reload. Do not
upload wheel files here: installers fetch the wheel from the PyPI Simple index and
validate its published digest. If a new script is published, check that the old
release remains available by running the installer with an isolated
`PAPPER_INSTALL_DIR` before replacing the public files.

The current NPMplus allowlist is deliberately limited to `/`, `/index.html`,
`/install.sh`, and `/install.ps1`. If the allowlist is changed, update it through
the managed NPMplus host and keep directory listing disabled. The scripts are
served with `Content-Type: text/plain` using a managed server-level MIME override
(`types { text/html html; text/plain sh ps1; }`). Preserve this override so
`Invoke-RestMethod | Invoke-Expression` receives text on both PowerShell versions.
HTTPS, `nosniff`, and wheel SHA-256 verification are also retained.

Before publishing, run `sh -n` and the PowerShell parser, install into an isolated
directory on Windows and Linux, rerun an installation as an upgrade in both
Windows PowerShell 5.1 and PowerShell 7, and build a DOCX from a freshly initialized
project. Check that an unavailable version and a deliberately incorrect checksum
leave the previous command working, and that a traversal ZIP is rejected before
extraction. Mirror fixtures should include relative links, an older `0.9.x` after
a newer `0.11.x`, and a yanked release with a larger version number. Use isolated
HOME/user PATH settings when testing persistent PATH changes. Native macOS smoke
validation requires an Apple Silicon Mac; Linux cannot validate its executable.
For source selection, check CN, non-CN, malformed/unknown country replies, a
failed primary lookup with a working backup, both lookups failing, explicit
index settings bypassing all lookups, and automatic CERNET HTTP 404 recovery.
Use mocked HTTPS country responses for these branches, then smoke-test real
official and CERNET downloads; there is no production override for country data.

## Manage the service

The site appears as `papper.cesaryuan.top` in the existing NPMplus management UI,
using the `path` scheme with document root `/data/static/papper` and no PHP
handler. Change managed site options through NPMplus; generated Nginx host
configuration files are overwritten when the site is updated.
The initial host ID is `3`, and its certificate ID is `5`. NPMplus currently
uses short-lived certificates and checks renewal every three hours. The initial
certificate expires on 2026-10-25 UTC; renewal monitoring matters even though
renewal is automatic. The container publishes HTTPS port 443, without a public
HTTP port 80; use explicit `https://` URLs. DNS challenge renewal does not need
port 80.

Check configuration and container health over SSH:

```powershell
ssh zouter-hk "docker exec npmplus nginx -t"
ssh zouter-hk "docker ps --format '{{.Names}} {{.Status}}'"
```

Provisioning backups are kept outside the document root. Configuration snapshots
are under `/root/npmplus/backups/papper-*`, with the initial snapshot location in
`/root/npmplus/backups/papper-latest-backup-path`. A consistent SQLite snapshot is
under `/root/npmplus/data/backups/papper-*.sqlite`; these database backups contain
credentials and must stay private.

To remove only this site, disable or delete its host entry in NPMplus. Remove its
certificate separately only after confirming that no other host uses it. Restoring
the entire database affects every managed host and should be reserved for recovery
with the container stopped and a fresh backup of the current database.

## Host access and firewall

SSH on `zouter-hk` requires an authorized public key. Password and keyboard
interactive authentication are disabled by
`/etc/ssh/sshd_config.d/00-papper-key-only.conf`.

The host uses the enabled `papper-firewall.service` systemd unit to load
`/etc/papper-firewall.nft`. UFW is inactive. The firewall permits the observed
public services on TCP 22, 81, 443, and 64914, and UDP 41641 and 64915, for both
IPv4 and IPv6. It preserves established responses, ICMP/ICMPv6, loopback, and the
existing trusted Tailscale network. Docker-published traffic is filtered before
DNAT, so publishing an additional container port does not automatically expose it.

To add a new public service, intentionally update the TCP or UDP set in the
firewall configuration, validate it, then reload this dedicated service:

```powershell
ssh zouter-hk "nft --check --file /etc/papper-firewall.nft"
ssh zouter-hk "systemctl reload papper-firewall.service"
ssh zouter-hk "nft list table inet papper_firewall"
```

Retain a working SSH session and arrange a timed rollback before changing access
rules. Reloading the dedicated service atomically replaces its own table. Avoid
flushing the complete ruleset or enabling the stock nftables service with a
configuration that flushes Docker and Tailscale tables.
