# Editor configuration contract

`papper config describe --format json` is the versioned configuration interface
for editor integrations. It writes one UTF-8 JSON value to stdout and uses stderr
for errors. It does not discover a project, load a document engine, create files,
or start the automatic update checker. The default format is also JSON.

The export contains:

| Member | Meaning |
| --- | --- |
| `contractVersion` | Wire-format version, currently 1; independent of the Papper release version |
| `papperVersion` | Version of the executable that supplied the definitions |
| `capabilities` | Available configuration queries; currently `config.describe` |
| `schemas` | Inline JSON Schema structures for style files, Pandoc metadata, manuscript frontmatter and DOCX text-style properties |
| `rules` | Editor constraints expressed as paths relative to a configuration domain |
| `templates.styleProject` | Exact commented project template, suitable for a user-requested merge |
| `defaults.standard`, `defaults.chinese` | Parsed bundled YAML layers, not effective manuscript settings |

Version 1 uses inline properties and schema-valued `additionalProperties` for
dynamic keys. Word style names are arbitrary; author sequences use `items`.
The structures describe preferred authoring forms and are not a complete validator
for every historical input that the runtime accepts. Pandoc metadata is extensible.
Consumers must preserve explicit `false`, zero, `null` and empty strings when
editing or displaying configuration.

Editor annotations are:

- `x-papper-suggestions`: YAML insertion strings, including quotes where needed;
  these are suggestions, not a restriction on legal values.
- `x-papper-value-aliases`: historical enum values mapped to canonical values.
- `x-papper-section`: suggested navigation category; unknown categories should
  fall back to a generic section.
- `x-papper-targets`: output formats in which a setting applies.

An `atMostOne` rule lists paths that must not be configured together. A `style`
rule applies to a root style layer and its reply/per-manuscript counterparts. A
`docxTextStyle` rule applies inside each named style. Consumers should evaluate
paths through exact canonical field names, including nested indentation fields.

Owned fields are enumerated from `SettingField::ALL`; their shared `SettingShape`
defines preferred types and nullable behavior. Enum choices come from the runtime
enums, numeric limits and SVG sizing constraints from metadata definitions, and
typography names/types and indentation conflicts from the style domain. DOCX
formatting calls the same typography parsers as the core and HTML path.

`configuration_help.json` contains only descriptions and optional suggestions,
loaded through typed Rust help structures that reject unexpected members. It
does not determine accepted names, validation types, enums or runtime rules. Papper-owned
objects reject unknown keys; Pandoc metadata and user-defined Word style names
remain extensible. All owned keys require exact canonical spelling.
Templates and language defaults are embedded directly from their authored YAML
files. When changing public configuration, update the runtime, editor help and
user guide together, then regenerate downstream offline snapshots. Adding a field
without help makes the export fail explicitly rather than silently omit it.

Consumers should cache per executable change, validate a complete export before
replacing working data, and reject unsupported contract versions. An older CLI or
failed export should leave offline definitions usable. Never apply a template to
a user's configuration as a side effect of refreshing the contract.

This command does not resolve project/manuscript precedence, report runtime
MathType availability, or extract reference-DOCX styles. Effective reference
export remains available through `papper build docx --export-reference-doc`.
