#!/usr/bin/env bash
# Prepare native toolchains in the workflow's manylinux_2_28 container.
# Run with bash after the workflow exports toolchain versions and cache paths.
# Install the pinned ELF repairer, system libraries, Rust, GHC, and Cabal;
# Cargo/Cabal dependencies and pinned toolchains persist in workflow-mounted caches.
# Building here keeps bundled native dependencies at the glibc 2.28 baseline.
set -euo pipefail

# The manylinux image ships patchelf 0.17.2, whose RPATH rewrite can make
# GHC executables segfault before main with no stderr. Pin a verified repairer.
# manylinux hardlinks files under /opt/_internal. Recreating its pipx venv
# can fail with SameFileError on Activate.ps1, so use a separate uv environment.
# Replace only the PATH entry; leave the image's pipx environment untouched.
UV_TOOL_DIR=/opt/papper-tools UV_TOOL_BIN_DIR=/usr/local/bin \
  uv tool install --force --python /opt/python/cp311-cp311/bin/python 'patchelf==0.19.1.0'
hash -r
patchelf --version

dnf install -y gcc gcc-c++ make perl clang clang-devel llvm-devel pkgconf-pkg-config gmp-devel libffi-devel ncurses-devel numactl-devel zlib-devel fontconfig-devel freetype-devel mesa-libGL-devel
# A restored Rust installation already contains rustup and its Cargo proxy binaries.
if ! command -v rustup >/dev/null 2>&1; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- \
    -y --profile minimal --default-toolchain "$RUSTUP_TOOLCHAIN"
else
  echo '[papper CI] Reusing cached Rust toolchain'
  rustup toolchain install "$RUSTUP_TOOLCHAIN" --profile minimal
fi
if ! command -v ghcup >/dev/null 2>&1; then
  curl --proto '=https' --tlsv1.2 -sSf https://get-ghcup.haskell.org | \
    BOOTSTRAP_HASKELL_NONINTERACTIVE=1 BOOTSTRAP_HASKELL_MINIMAL=1 sh
fi

# GHCup's Red Hat/unknown-Linux mapping uses the Rocky Linux 8 bindist,
# compatible with this AlmaLinux 8 container's glibc 2.28 and ncurses 6.
if ! ghcup whereis ghc "$GHC_VERSION" >/dev/null 2>&1; then
  ghcup install ghc "$GHC_VERSION"
else
  echo '[papper CI] Reusing cached GHC toolchain'
fi
ghcup set ghc "$GHC_VERSION"
# GHCup can report a path even when a restored Cabal symlink has no target.
# Force installation in that case; a dangling versioned link can otherwise
# make GHCup claim the missing executable is already installed.
if ! cabal_path=$(ghcup whereis cabal "$CABAL_VERSION") || [[ ! -x "$cabal_path" ]]; then
  echo '[papper CI] Installing missing or incomplete Cabal toolchain'
  ghcup install cabal --force "$CABAL_VERSION"
else
  echo '[papper CI] Reusing cached Cabal toolchain'
fi
ghcup set cabal "$CABAL_VERSION"
ghc --version
cabal --version
# A fallback cache may predate a changed index-state; refresh it before solving.
if [[ ! -f "$CABAL_DIR/packages/hackage.haskell.org/01-index.tar" || "${HASKELL_CACHE_HIT:-false}" != 'true' ]]; then
  cabal update
else
  echo '[papper CI] Reusing pinned Hackage index'
fi
