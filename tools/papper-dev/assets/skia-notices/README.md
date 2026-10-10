# Native SVG renderer notices

Papper links the official rust-skia 0.153.3 native SVG archive. These upstream
license copies accompany that archive in the wheel's runtime. `SOURCES.json`
records the dependency revisions from Skia `m153-0.101.2`; update these copies
when upgrading Skia. The FreeType license option used here is the FreeType
License, retained with its alternative upstream GPL text.

The linked software uses the Independent JPEG Group's JPEG software and
libjpeg-turbo. Wuffs is distributed under its Apache 2.0 license option.
Some notices describe optional native dependencies that may be absent from a
particular platform archive. Dynamic Microsoft CRT notices are staged separately
by the wheel's dependency repair step.
