#!/bin/sh
# Install Papper's native PyPI wheel without Python or uv. Select a stable release
# from the Simple index, verify its SHA-256, and unpack it into a user-owned prefix.
# Use CERNET only for a confirmed CN public IP, otherwise use official PyPI.
# A small launcher reads an atomically replaced release pointer, so failed downloads
# and validation leave the previous version available. Run with --help for options.
(
set -eu

# Report an actionable failure without continuing with a partially installed release.
die() { printf 'Papper installer: %s\n' "$*" >&2; exit 1; }

# Fetch only HTTPS resources, including redirects, with bounded retries and timeouts.
download() {
    curl --fail --silent --show-error --location --retry 2 --connect-timeout 20 \
        --max-time 600 --proto '=https' --proto-redir '=https' "$1" -o "$2"
}

# Quote a path for a persistent POSIX shell PATH assignment, including apostrophes.
quote_path() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

# Bound optional geolocation requests and accept only an exact country code.
select_index() {
    for endpoint in https://www.cloudflare.com/cdn-cgi/trace https://ipinfo.io/country; do
        response=$(curl --fail --silent --show-error --connect-timeout 2 --max-time 2 \
            --proto '=https' "$endpoint" 2>/dev/null) || continue
        case "$endpoint" in
            */cdn-cgi/trace) country=$(printf '%s\n' "$response" | awk '{sub(/\r$/, "")} /^loc=[A-Z][A-Z]$/ {sub(/^loc=/, ""); print; exit}');;
            *) country=$(printf '%s\n' "$response" | awk 'NF==1 && $1 ~ /^[A-Z][A-Z]$/ {print $1; exit}');;
        esac
        case "$country" in
            CN)
                printf '%s\n' 'Public IP country: CN; using CERNET PyPI mirror' >&2
                printf '%s\n' 'https://mirrors.cernet.edu.cn/pypi/web/simple'
                return;;
            XX|'') continue;;
            [A-Z][A-Z])
                printf 'Public IP country: %s; using official PyPI\n' "$country" >&2
                printf '%s\n' 'https://pypi.org/simple'
                return;;
        esac
    done
    printf '%s\n' 'IP country lookup unavailable; using official PyPI' >&2
    printf '%s\n' 'https://pypi.org/simple'
}

index=${PAPPER_INDEX_URL:-}
version=${PAPPER_VERSION:-latest}
prefix=${PAPPER_INSTALL_DIR:-$HOME/.local/share/papper}
no_path=${PAPPER_NO_MODIFY_PATH:-0}
while [ "$#" -gt 0 ]; do
    case "$1" in
        --version|--index-url|--install-dir)
            [ "$#" -ge 2 ] || die "$1 requires a value"
            case "$1" in --version) version=$2;; --index-url) index=$2;; --install-dir) prefix=$2;; esac
            shift 2;;
        --no-modify-path) no_path=1; shift;;
        -h|--help)
            printf '%s\n' 'Install Papper without Python or uv' \
                'Options: --version X.Y.Z --index-url HTTPS_URL --install-dir ABSOLUTE_PATH --no-modify-path' \
                'Environment: PAPPER_VERSION, PAPPER_INDEX_URL, PAPPER_INSTALL_DIR, PAPPER_NO_MODIFY_PATH=1' \
                'Default index: automatic (CN public IP: CERNET mirror; elsewhere or unknown: official PyPI)'
            exit 0;;
        *) die "Unknown option: $1";;
    esac
done
case "$index" in ''|https://*) ;; *) die 'The package index must use HTTPS';; esac
case "$prefix" in /*) ;; *) die 'The install directory must be an absolute path';; esac
case "$prefix" in *'
'*) die 'Newlines are not supported in install paths';; esac
if [ "$version" != latest ]; then
    printf '%s\n' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || die 'Use a stable X.Y.Z version'
fi
for command in curl unzip awk sed sort mktemp; do
    command -v "$command" >/dev/null 2>&1 || die "Required command is missing: $command"
done
if command -v sha256sum >/dev/null 2>&1; then hash_command=sha256sum
elif command -v shasum >/dev/null 2>&1; then hash_command=shasum
else die 'Install sha256sum or shasum to verify downloads'; fi

case "$(uname -s):$(uname -m)" in
    Linux:x86_64)
        platform=manylinux_2_28_x86_64
        libc=$(getconf GNU_LIBC_VERSION 2>/dev/null || true)
        printf '%s\n' "$libc" | awk '{split($2,v,"."); exit !(v[1]>2 || (v[1]==2 && v[2]>=28))}' \
            || die 'Linux requires glibc 2.28 or newer; musl/Alpine is not supported';;
    Darwin:arm64)
        platform=macosx_14_0_arm64
        [ "$(sw_vers -productVersion | cut -d. -f1)" -ge 14 ] || die 'macOS 14 or newer is required';;
    *) die "No supported native wheel for $(uname -s)/$(uname -m); supported: Linux x86_64 and macOS arm64";;
esac

mkdir -p "$prefix"
prefix=$(cd "$prefix" && pwd -P)
[ -f "$prefix/.papper-installer" ] || [ -z "$(ls -A "$prefix")" ] \
    || die "Install directory is not empty and is not managed by this installer: $prefix"
mkdir "$prefix/.install-lock" 2>/dev/null || die "Another installation is running; if interrupted, remove $prefix/.install-lock"
stage=
# Clean only this invocation's temporary directory and lock, never installed releases.
cleanup() {
    [ -z "$stage" ] || rm -rf "$stage"
    rmdir "$prefix/.install-lock" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf '%s\n' 'papper-installer-v1' > "$prefix/.papper-installer"
stage=$(mktemp -d "$prefix/.stage.XXXXXXXX")
automatic_mirror=0
if [ -z "$index" ]; then
    index=$(select_index)
    [ "$index" != https://mirrors.cernet.edu.cn/pypi/web/simple ] || automatic_mirror=1
else
    printf '%s\n' 'Using explicitly configured package index; skipping IP country lookup'
fi
index=${index%/}/papper/
printf 'Resolving Papper %s for %s\n' "$version" "$platform"
if ! download "$index" "$stage/index.html"; then
    # A CERNET node can lack this project's index, so recover only automatic selection.
    [ "$automatic_mirror" = 1 ] || die 'Could not download the configured package index'
    printf '%s\n' 'CERNET index unavailable; falling back to official PyPI'
    index=https://pypi.org/simple/papper/
    download "$index" "$stage/index.html"
fi
# Simple API links may be relative and unordered; ignore yanked and prerelease wheels.
awk -v platform="$platform" -v wanted="$version" -v sq="'" '
    BEGIN { RS="<" }
    /^a[[:space:]]/ && $0 !~ /data-yanked/ {
        tag=$0; sub(/>.*/, "", tag)
        if (!match(tag, /href[[:space:]]*=[[:space:]]*/)) next
        value=substr(tag, RSTART+RLENGTH); quote=substr(value,1,1)
        if (quote!="\"" && quote!=sq) next
        value=substr(value,2); end=index(value,quote); if (!end) next
        url=substr(value,1,end-1); gsub(/&amp;/,"\\&",url)
        name=url; sub(/#.*/,"",name); sub(/^.*\//,"",name)
        suffix="-py3-none-" platform ".whl"
        if (substr(name,length(name)-length(suffix)+1)!=suffix) next
        v=name; sub(/^papper-/,"",v); sub(/-py3-none-.*/,"",v)
        if (v !~ /^[0-9]+\.[0-9]+\.[0-9]+$/ || (wanted!="latest" && v!=wanted)) next
        hash=url; if (hash !~ /#sha256=/) next; sub(/^.*#sha256=/,"",hash)
        if (length(hash)!=64 || hash ~ /[^0-9a-fA-F]/) next
        print v "|" url "|" tolower(hash)
    }' "$stage/index.html" | sort -t. -k1,1n -k2,2n -k3,3n > "$stage/candidates"
selected=$(tail -n 1 "$stage/candidates")
[ -n "$selected" ] || die "No matching non-yanked wheel found; try --index-url https://pypi.org/simple or a different version"
version=${selected%%|*}; rest=${selected#*|}; url=${rest%|*}; expected=${rest##*|}
origin=$(printf '%s' "$index" | sed 's|\(https://[^/]*\).*|\1|')
case "$url" in
    https://*) ;;
    //*) url=https:$url;;
    /*) url=$origin$url;;
    *://*) die 'Wheel URL must use HTTPS';;
    *) url=$index$url;;
esac
printf 'Downloading Papper %s\n' "$version"
download "${url%%#*}" "$stage/package.whl"
if [ "$hash_command" = sha256sum ]; then actual=$(sha256sum "$stage/package.whl" | awk '{print $1}')
else actual=$(shasum -a 256 "$stage/package.whl" | awk '{print $1}'); fi
[ "$actual" = "$expected" ] || die 'SHA-256 mismatch; existing installation was preserved'
# Reject traversal and symlinks before extracting even a checksum-verified archive.
unzip -Z1 "$stage/package.whl" > "$stage/entries"
awk '/^\// || /\\/ || /(^|\/)\.\.(\/|$)/ {exit 1}' "$stage/entries" || die 'Unsafe archive path'
unzip -Z -l "$stage/package.whl" > "$stage/attributes"
if grep -Eq '^l' "$stage/attributes"; then die 'Archive symlinks are not supported'; fi
mkdir "$stage/release"
unzip -q "$stage/package.whl" -d "$stage/release"
scripts="$stage/release/papper-$version.data/scripts"
mkdir "$stage/release/bin"
for name in papper pmt; do
    [ -f "$scripts/$name" ] || die "Native executable is missing: $name"
    mv "$scripts/$name" "$stage/release/bin/$name"
    chmod 755 "$stage/release/bin/$name"
done
reported=$(PAPPER_DISABLE_UPDATE_CHECK=1 "$stage/release/bin/papper" --version) || die 'Downloaded executable cannot run on this machine'
[ "$reported" = "papper $version" ] || die "Unexpected executable version: $reported"
PAPPER_DISABLE_UPDATE_CHECK=1 "$stage/release/bin/pmt" --version >/dev/null || die 'The pmt alias failed validation'
release="$version-$(printf %.16s "$expected")"
mkdir -p "$prefix/releases" "$prefix/bin"
if [ ! -d "$prefix/releases/$release" ]; then mv "$stage/release" "$prefix/releases/$release"; fi
for name in papper pmt; do
    cat > "$stage/$name" <<'LAUNCHER'
#!/bin/sh
# Resolve the selected release relative to this user-owned launcher.
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd) || exit 1
IFS= read -r release < "$root/current" || exit 1
exec "$root/releases/$release/bin/COMMAND" "$@"
LAUNCHER
    sed "s/COMMAND/$name/" "$stage/$name" > "$prefix/bin/.$name.next"
    chmod 755 "$prefix/bin/.$name.next"
    mv -f "$prefix/bin/.$name.next" "$prefix/bin/$name"
done
printf '%s\n' "$release" > "$stage/current"
mv -f "$stage/current" "$prefix/current"
if [ "$no_path" != 1 ]; then
    case "${SHELL:-}" in */zsh) profile=$HOME/.zshrc;; */bash) profile=$HOME/.bashrc;; *) profile=$HOME/.profile;; esac
    path_line="export PATH=$(quote_path "$prefix/bin"):\$PATH"
    if ! grep -Fqx "$path_line" "$profile" 2>/dev/null; then
        printf '\n# Papper standalone installer\n%s\n' "$path_line" >> "$profile"
    fi
    printf 'PATH configured in %s; open a new terminal to use papper\n' "$profile"
fi
printf 'Installed Papper %s in %s\n' "$version" "$prefix"
printf 'Run now: "%s/bin/papper" --help\n' "$prefix"
)
