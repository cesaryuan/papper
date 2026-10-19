# Install, upgrade, and remove Papper

[English README](../README.md) · [中文 README](../README.zh-CN.md)

The standalone installer downloads a native wheel from PyPI or a compatible
mirror, verifies the SHA-256 advertised by that index over HTTPS, and extracts
Papper without a Python interpreter, uv, or administrator permissions. It installs
both `papper` and the compatibility alias `pmt`. The wheel also contains the
runtime resources needed for document builds; they are unpacked automatically on
first use. The first build can take longer while those resources are cached.

## Install

Windows x64, in 64-bit Windows PowerShell 5.1 or PowerShell 7:

```powershell
irm https://papper.cesaryuan.top/install.ps1 | iex
```

Linux x86_64 with glibc 2.28 or newer, or macOS 14+ on Apple Silicon:

```sh
curl -fsSL https://papper.cesaryuan.top/install.sh | sh
```

Open a new terminal, then run `papper --version` and `papper doctor`. Linux needs
`curl`, `unzip`, and either `sha256sum` or `shasum`, alongside standard shell tools.
Alpine/musl, Linux ARM64, Intel Macs, and Windows ARM64 are not currently supported
by these native wheels. Windows launchers are `.cmd` files that invoke the native
executables with your arguments.

The defaults are `%LOCALAPPDATA%\Programs\Papper` on Windows and
`~/.local/share/papper` on Linux/macOS. The installer adds the installation's `bin`
directory to the user PATH on Windows, or to `.zshrc`, `.bashrc`, or `.profile`
according to your shell on Unix. It never requires `sudo`.

You can download and inspect the scripts before running them:

```powershell
irm https://papper.cesaryuan.top/install.ps1 -OutFile install.ps1
# Review install.ps1, then run it in this process only
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

```sh
curl -fsSL https://papper.cesaryuan.top/install.sh -o install.sh
# Review install.sh, then run it
sh install.sh
```

## Choose a version, mirror, or directory

By default, the installer queries Cloudflare's HTTPS `/cdn-cgi/trace` endpoint
for the public exit IP's country, falling back to `https://ipinfo.io/country` if
the first lookup fails or returns no usable country. Each lookup request has a
two-second timeout. Only country code `CN` selects the CERNET mirror at
`https://mirrors.cernet.edu.cn/pypi/web/simple`; other countries and failed
lookups use `https://pypi.org/simple`. An unavailable automatically selected
CERNET project index also falls back to official PyPI. A proxy or VPN affects
the detected exit location; the script does not inspect system language or timezone.

An explicit `-IndexUrl`, `--index-url`, or `PAPPER_INDEX_URL` skips geolocation
entirely and takes precedence over automatic selection. Explicit indexes are not
automatically replaced on failure. Set a **Simple index base URL**, without
the trailing `/papper/`. Mirrors may lag behind PyPI shortly after a release.
Only HTTPS indexes and wheel downloads are accepted.
The latest non-yanked stable `X.Y.Z` wheel for your platform is selected by default.

PowerShell parameters work with a downloaded script:

```powershell
.\install.ps1 -Version 0.11.3 -IndexUrl https://pypi.org/simple -InstallDir "$HOME\Apps\Papper" -NoModifyPath
```

Shell parameters work with either a downloaded or piped script:

```sh
sh install.sh --version 0.11.3 --index-url https://pypi.org/simple --install-dir "$HOME/apps/papper" --no-modify-path
curl -fsSL https://papper.cesaryuan.top/install.sh | sh -s -- --version 0.11.3
```

Both installers also accept the same environment variables:

| Variable | Purpose |
| --- | --- |
| `PAPPER_VERSION` | Exact stable version, or `latest` (default) |
| `PAPPER_INDEX_URL` | Explicit HTTPS Simple index base URL; skips automatic IP country detection |
| `PAPPER_INSTALL_DIR` | Absolute installation directory |
| `PAPPER_NO_MODIFY_PATH` | Set to `1` to leave PATH configuration untouched |

For example, use official PyPI with the one-line commands:

```powershell
$env:PAPPER_INDEX_URL = 'https://pypi.org/simple'
irm https://papper.cesaryuan.top/install.ps1 | iex
```

```sh
curl -fsSL https://papper.cesaryuan.top/install.sh | PAPPER_INDEX_URL=https://pypi.org/simple sh
```

Keep `PAPPER_INSTALL_DIR` set, or pass the same directory parameter, when upgrading
a custom installation. `-NoModifyPath` / `--no-modify-path` also means you must
configure PATH yourself or invoke the launcher by its full path.

## Upgrade, downgrade, or switch from uv

Rerun the same installer to upgrade to the latest stable release. Set the version
to reinstall or downgrade. Downloads, checksums, archive paths, and both executable
entry points are checked before changing the active release. A failed download or
checksum check leaves the active release available. Earlier releases stay in the
`releases` subdirectory; the installer does not prune them automatically.

If you installed with uv, continue using `uv tool upgrade papper`, or first run
`uv tool uninstall papper` and then use the standalone installer. Multiple
installations on PATH can cause the wrong version to run. Diagnose this with
`Get-Command papper -All` on PowerShell or `command -v papper` on Unix.
Older Papper versions may still mention uv in their update notification; for a
standalone installation, rerunning its installer is the correct upgrade method.

The installer refuses to overwrite a nonempty directory it does not manage.
If an interrupted Unix install leaves `.install-lock`, verify no installer is
running before removing that empty directory and retrying. Windows releases its
file lock when the installer process exits.

## Remove

Delete the installation directory and remove its `bin` entry from PATH. For the
default Windows directory:

```powershell
$papperRoot = Join-Path $env:LOCALAPPDATA 'Programs\Papper'
$papperBin = Join-Path $papperRoot 'bin'
$remaining = [Environment]::GetEnvironmentVariable('Path', 'User') -split ';' |
    Where-Object { $_ -and $_.TrimEnd('\') -ine $papperBin }
[Environment]::SetEnvironmentVariable('Path', ($remaining -join ';'), 'User')
Remove-Item -LiteralPath $papperRoot -Recurse -Force
```

For the default Unix directory:

```sh
rm -rf "$HOME/.local/share/papper"
```

Remove the `# Papper standalone installer` comment and its following PATH line
from the shell startup file reported during installation, then open a new terminal.
For a custom directory, use that directory in the removal commands instead.
Your manuscript folders and `~/.papper` runtime caches are separate from the
installation directory and are retained. Delete caches separately only if wanted.
