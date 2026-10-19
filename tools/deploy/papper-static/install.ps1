# Install a native Papper wheel from a PyPI Simple index without Python or uv.
# Downloads are verified against the index SHA-256 before safe ZIP extraction.
# Detect the public IP country to use CERNET for CN and official PyPI elsewhere.
# Versioned releases and an atomic pointer preserve an existing install on failure.
# Run Get-Help ./install.ps1 or use -Version, -IndexUrl, -InstallDir, -NoModifyPath.
[CmdletBinding()]
param(
    [string]$Version = $(if ($env:PAPPER_VERSION) { $env:PAPPER_VERSION } else { 'latest' }),
    [string]$IndexUrl = $env:PAPPER_INDEX_URL,
    [string]$InstallDir = $(if ($env:PAPPER_INSTALL_DIR) { $env:PAPPER_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA 'Programs\Papper' }),
    [switch]$NoModifyPath = ($env:PAPPER_NO_MODIFY_PATH -eq '1')
)

function Install-PapperNative {
    <# Install and validate a Windows x64 release before updating the active pointer. #>
    param([string]$RequestedVersion, [string]$Index, [string]$Prefix, [bool]$SkipPath)
    $ErrorActionPreference = 'Stop'
    if ([Environment]::OSVersion.Platform -ne 'Win32NT') { throw 'Use install.sh on Linux or macOS' }
    if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64' -or $env:PROCESSOR_ARCHITEW6432) {
        throw 'Use a 64-bit PowerShell on Windows x64; ARM64 wheels are not published'
    }
    if ($RequestedVersion -ne 'latest' -and $RequestedVersion -notmatch '^\d+\.\d+\.\d+$') { throw 'Use a stable X.Y.Z version' }
    $automaticIndex = [string]::IsNullOrWhiteSpace($Index)
    if (-not $automaticIndex) {
        $indexUri = [Uri]($Index.TrimEnd('/') + '/papper/')
        if (-not $indexUri.IsAbsoluteUri -or $indexUri.Scheme -ne 'https') { throw 'The package index must use HTTPS' }
    }
    if (-not [IO.Path]::IsPathRooted($Prefix)) { throw 'InstallDir must be an absolute path' }
    $Prefix = [IO.Path]::GetFullPath($Prefix).TrimEnd('\')
    $marker = Join-Path $Prefix '.papper-installer'
    if ((Test-Path -LiteralPath $Prefix) -and -not (Test-Path -LiteralPath $marker) -and
        (Get-ChildItem -LiteralPath $Prefix -Force | Select-Object -First 1)) {
        throw "Install directory is not empty and is not managed by this installer: $Prefix"
    }
    [IO.Directory]::CreateDirectory($Prefix) | Out-Null
    $lock = $null
    $stage = $null
    $oldTls = [Net.ServicePointManager]::SecurityProtocol
    $oldUpdate = $env:PAPPER_DISABLE_UPDATE_CHECK
    try {
        # FileShare.None prevents concurrent installers; the handle closes after interruption.
        $lock = [IO.File]::Open((Join-Path $Prefix '.install-lock'), 'OpenOrCreate', 'ReadWrite', 'None')
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        [IO.File]::WriteAllText($marker, 'papper-installer-v1')
        $stage = Join-Path $Prefix ('.stage.' + [Guid]::NewGuid().ToString('N'))
        [IO.Directory]::CreateDirectory($stage) | Out-Null
        $Index = Resolve-PapperIndex -Index $Index
        $indexUri = [Uri]($Index.TrimEnd('/') + '/papper/')
        Write-Host "Resolving Papper $RequestedVersion for Windows x64"
        # The request helper validates each redirect before fetching its destination.
        try { $html = Get-PapperHttpsResource -Uri $indexUri }
        catch {
            # CERNET can redirect to a node without this project; automatic selection
            # must not make a healthy official PyPI installation unavailable.
            if (-not $automaticIndex -or $Index -ne 'https://mirrors.cernet.edu.cn/pypi/web/simple') { throw }
            Write-Host 'CERNET index unavailable; falling back to official PyPI'
            $indexUri = [Uri]'https://pypi.org/simple/papper/'
            $html = Get-PapperHttpsResource -Uri $indexUri
        }
        $candidates = @()
        foreach ($anchor in [regex]::Matches($html, '<a\s+[^>]*>', 'IgnoreCase')) {
            if ($anchor.Value -match '\bdata-yanked\b') { continue }
            $href = [regex]::Match($anchor.Value, 'href\s*=\s*["'']([^"'']+)["'']', 'IgnoreCase')
            if (-not $href.Success) { continue }
            $uri = [Uri]::new($indexUri, [Net.WebUtility]::HtmlDecode($href.Groups[1].Value))
            $file = [Uri]::UnescapeDataString(($uri.AbsolutePath -split '/')[-1])
            $match = [regex]::Match($file, '^papper-(\d+\.\d+\.\d+)-py3-none-win_amd64\.whl$')
            if (-not $match.Success -or $uri.Fragment -notmatch '^#sha256=([0-9a-fA-F]{64})$') { continue }
            $sha = $Matches[1].ToLowerInvariant()
            $v = $match.Groups[1].Value
            if ($RequestedVersion -ne 'latest' -and $RequestedVersion -ne $v) { continue }
            $candidates += [pscustomobject]@{ Version = [version]$v; Text = $v; Uri = $uri; Sha = $sha }
        }
        $selected = $candidates | Sort-Object Version | Select-Object -Last 1
        if (-not $selected) { throw 'No matching non-yanked wheel found; try -IndexUrl https://pypi.org/simple or a different version' }
        $wheel = Join-Path $stage 'package.whl'
        Write-Host "Downloading Papper $($selected.Text)"
        Get-PapperHttpsResource -Uri $selected.Uri -OutFile $wheel
        if ((Get-FileHash -LiteralPath $wheel -Algorithm SHA256).Hash.ToLowerInvariant() -ne $selected.Sha) {
            throw 'SHA-256 mismatch; existing installation was preserved'
        }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $releaseDir = Join-Path $stage 'release'
        [IO.Directory]::CreateDirectory($releaseDir) | Out-Null
        $zip = [IO.Compression.ZipFile]::OpenRead($wheel)
        try {
            # Validate all paths before writing any archive entry, including Windows drive paths.
            foreach ($entry in $zip.Entries) {
                $name = $entry.FullName
                if ($name -match '(^[/\\]|:|(^|[/\\])\.\.([/\\]|$))' -or
                    (($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000) { throw "Unsafe archive entry: $name" }
                $destination = [IO.Path]::GetFullPath((Join-Path $releaseDir $name))
                if (-not $destination.StartsWith($releaseDir + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Archive entry escapes extraction directory' }
            }
        } finally { $zip.Dispose() }
        [IO.Compression.ZipFile]::ExtractToDirectory($wheel, $releaseDir)
        $bin = Join-Path $releaseDir 'bin'
        [IO.Directory]::CreateDirectory($bin) | Out-Null
        foreach ($name in @('papper', 'pmt')) {
            $source = Join-Path $releaseDir "papper-$($selected.Text).data\scripts\$name.exe"
            if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Native executable is missing: $name" }
            Move-Item -LiteralPath $source -Destination $bin
        }
        $env:PAPPER_DISABLE_UPDATE_CHECK = '1'
        $reported = & (Join-Path $bin 'papper.exe') --version
        if ($LASTEXITCODE -ne 0 -or $reported -ne "papper $($selected.Text)") { throw "Unexpected executable version: $reported" }
        & (Join-Path $bin 'pmt.exe') --version | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'The pmt alias failed validation' }
        $release = $selected.Text + '-' + $selected.Sha.Substring(0, 16)
        $releases = Join-Path $Prefix 'releases'
        $launchers = Join-Path $Prefix 'bin'
        [IO.Directory]::CreateDirectory($releases) | Out-Null
        [IO.Directory]::CreateDirectory($launchers) | Out-Null
        $target = Join-Path $releases $release
        if (-not (Test-Path -LiteralPath $target)) { Move-Item -LiteralPath $releaseDir -Destination $target }
        foreach ($name in @('papper', 'pmt')) {
            $wrapper = "@echo off`r`nrem Resolve the selected native release without a Python runtime`r`nsetlocal`r`nset /p papper_release=<`"%~dp0..\current`"`r`n`"%~dp0..\releases\%papper_release%\bin\$name.exe`" %*`r`nexit /b %errorlevel%`r`n"
            [IO.File]::WriteAllText((Join-Path $launchers "$name.cmd"), $wrapper, [Text.Encoding]::ASCII)
        }
        $pointer = Join-Path $Prefix 'current'
        $next = Join-Path $stage 'current'
        [IO.File]::WriteAllText($next, $release + "`n", [Text.Encoding]::ASCII)
        # PowerShell 7 coerces a null string argument to an empty path; a real backup
        # path keeps File.Replace atomic on both .NET Framework and modern .NET.
        if (Test-Path -LiteralPath $pointer) { [IO.File]::Replace($next, $pointer, (Join-Path $stage 'previous-current')) }
        else { [IO.File]::Move($next, $pointer) }
        if (-not $SkipPath) {
            $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
            $parts = @($userPath -split ';' | Where-Object { $_ -and $_.TrimEnd('\') -ine $launchers })
            [Environment]::SetEnvironmentVariable('Path', (@($launchers) + $parts -join ';'), 'User')
            $env:Path = $launchers + ';' + $env:Path
            Write-Host 'User PATH configured; open a new terminal for other applications'
        }
        Write-Host "Installed Papper $($selected.Text) in $Prefix"
        Write-Host "Run: & '$launchers\papper.cmd' --help"
    } finally {
        $env:PAPPER_DISABLE_UPDATE_CHECK = $oldUpdate
        [Net.ServicePointManager]::SecurityProtocol = $oldTls
        if ($stage -and [IO.Path]::GetFullPath($stage).StartsWith($Prefix + '\', [StringComparison]::OrdinalIgnoreCase) -and
            (Test-Path -LiteralPath $stage)) { Remove-Item -LiteralPath $stage -Recurse -Force }
        if ($lock) { $lock.Dispose() }
    }
}

function Resolve-PapperIndex {
    <# Respect explicit indexes; otherwise select CERNET only for a confirmed CN exit IP. #>
    param([string]$Index)
    if (-not [string]::IsNullOrWhiteSpace($Index)) {
        Write-Host 'Using explicitly configured package index; skipping IP country lookup'
        return $Index
    }
    foreach ($endpoint in @('https://www.cloudflare.com/cdn-cgi/trace', 'https://ipinfo.io/country')) {
        try {
            $text = Get-PapperHttpsResource -Uri ([Uri]$endpoint) -TimeoutMilliseconds 2000 -MaxRedirects 0
            if ($endpoint -like '*/cdn-cgi/trace') {
                $country = [regex]::Match($text, '(?m)^loc=([A-Z]{2})\r?$').Groups[1].Value
            } else { $country = $text.Trim() }
            if ($country -cnotmatch '^[A-Z]{2}$' -or $country -eq 'XX') { continue }
            if ($country -eq 'CN') {
                Write-Host 'Public IP country: CN; using CERNET PyPI mirror'
                return 'https://mirrors.cernet.edu.cn/pypi/web/simple'
            }
            Write-Host "Public IP country: $country; using official PyPI"
            return 'https://pypi.org/simple'
        } catch { continue }
    }
    Write-Host 'IP country lookup unavailable; using official PyPI'
    return 'https://pypi.org/simple'
}

function Get-PapperHttpsResource {
    <# Follow only HTTPS redirects and stream wheels to disk with a bounded request timeout. #>
    param([Uri]$Uri, [string]$OutFile, [int]$TimeoutMilliseconds = 600000, [int]$MaxRedirects = 10)
    for ($redirect = 0; $redirect -le $MaxRedirects; $redirect++) {
        if ($Uri.Scheme -ne 'https') { throw 'Downloads and redirects must use HTTPS' }
        $request = [Net.HttpWebRequest]::Create($Uri)
        $request.AllowAutoRedirect = $false
        $request.Timeout = $TimeoutMilliseconds
        $request.ReadWriteTimeout = $TimeoutMilliseconds
        $request.UserAgent = 'papper-standalone-installer/1'
        $response = $request.GetResponse()
        try {
            $status = [int]$response.StatusCode
            if ($status -ge 300 -and $status -lt 400) {
                $Uri = [Uri]::new($Uri, $response.Headers['Location'])
                continue
            }
            if ($status -ne 200) { throw "Download failed: HTTP $status" }
            $stream = $response.GetResponseStream()
            if ($OutFile) {
                $file = [IO.File]::Create($OutFile)
                try { $stream.CopyTo($file) } finally { $file.Dispose() }
            } else {
                $reader = New-Object IO.StreamReader($stream)
                try { $reader.ReadToEnd() } finally { $reader.Dispose() }
            }
            return
        } finally { $response.Dispose() }
    }
    throw 'Too many HTTPS redirects'
}

Install-PapperNative -RequestedVersion $Version -Index $IndexUrl -Prefix $InstallDir -SkipPath $NoModifyPath.IsPresent
