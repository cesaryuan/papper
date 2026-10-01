#!/usr/bin/env bash
# Prepare the native toolchains inside cibuildwheel's manylinux_2_28 container.
# GHC and Cabal are pinned by the workflow; compiled Haskell dependencies and
# Cargo outputs persist under /host. Building here lets auditwheel repair the
# worker's system-library dependencies without raising the glibc baseline.
# Invoke through CIBW_BEFORE_ALL_LINUX, after CIBW_ENVIRONMENT_LINUX is applied.
set -euo pipefail

# The cibuildwheel image ships patchelf 0.17.2, whose RPATH rewrite can make
# GHC executables segfault before main with no stderr. Pin a verified repairer.
pipx install --force 'patchelf==0.19.1.0'
patchelf --version

dnf install -y gcc gcc-c++ make perl pkgconf-pkg-config gmp-devel libffi-devel ncurses-devel numactl-devel zlib-devel
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
