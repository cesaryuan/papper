# Frozen Python differential reference

The former Python implementation is retained here only to run the existing
test suite and compare native Rust outputs with established behavior. It is
excluded from production wheels and never called by the Rust CLI or Server.

Keep behavioral changes in `crates/`. The changes in this reference are limited
to locating shared source resources after the move. Python dependencies belong
to the `dev` dependency group. Native contract tests run Rust executables and
the retained Haskell/Lua/C# components, comparing existing snapshots unchanged.

Remove this reference once every remaining Python-only regression has an
equivalent native behavioral assertion and native platform CI is established.
