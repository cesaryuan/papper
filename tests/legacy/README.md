# Frozen Python historical archive

The former Python implementation is retained here as a historical archive.
Active tests must not import or execute it. It is excluded from test discovery,
development installations and production wheels, and never called by the Rust
CLI or Server.

Keep behavioral changes in `crates/`. The changes in this reference are limited
to locating shared source resources after the move. Python dependencies in the
`dev` group only drive artifact inspection. Native contract tests run Rust
executables and the retained Haskell/Lua/C# components, checking independent
user-visible behavior and output snapshots.
