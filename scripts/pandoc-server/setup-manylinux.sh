#!/usr/bin/env bash
# Prepare native toolchains in the workflow's manylinux_2_28 container.
# Run with bash after the workflow exports toolchain versions and cache paths.
# Install the pinned ELF repairer, system libraries, Rust, GHC, and Cabal;
# Cargo and Cabal dependencies persist in the workflow-mounted cache.
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

dnf install -y gcc gcc-c++ make perl clang clang-devel llvm-devel pkgconf-pkg-config gmp-devel libffi-devel ncurses-devel numactl-devel zlib-devel
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- \
  -y --profile minimal --default-toolchain "$RUSTUP_TOOLCHAIN"
curl --proto '=https' --tlsv1.2 -sSf https://get-ghcup.haskell.org | \
  BOOTSTRAP_HASKELL_NONINTERACTIVE=1 BOOTSTRAP_HASKELL_MINIMAL=1 sh

# GHCup's Red Hat/unknown-Linux mapping uses the Rocky Linux 8 bindist,
# compatible with this AlmaLinux 8 container's glibc 2.28 and ncurses 6.
ghcup install ghc "$GHC_VERSION" --set
ghcup install cabal "$CABAL_VERSION" --set
ghc --version
cabal --version
cabal update
