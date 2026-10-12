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
| `capabilities` | Available queries/extensions: `config.describe`, `elementAttributes` |
| `schemas` | Inline JSON Schema structures for style files, Pandoc metadata, manuscript frontmatter and DOCX text-style properties |
| `rules` | Editor constraints expressed as paths relative to a configuration domain |
| `elementAttributes` | Markdown element attribute help, choices, aliases and discriminator-specific properties |
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

## Manuscript element attributes

The additive `elementAttributes` member uses syntax contexts as mapping keys:
`table` for table-caption attribute blocks, and `emptySpan` for `[]{...}`.
Each context provides a description, output `targets` and a `properties` mapping.
Properties contain `description`, `valueType` (`string`, `boolean`, `integer` or
`length`), optional exhaustive `values`, free-form `suggestions`, accepted key
`aliases`, and optional output `targets` overriding the context targets. All
inserted attribute values are strings; editors must quote and escape them for
Pandoc Markdown rather than reuse YAML insertion strings.

An element may declare a `discriminator` key and a `variants` mapping. For
`emptySpan`, the discriminator is `t`; each variant describes its own accepted
properties and optional aliases (`currentDate` is an alias of `date`). Editors
must resolve exact discriminator values regardless of attribute order. Unknown
types must not receive another type's options. Prefer canonical keys in new
completions, recognize authored aliases for help and duplicate suppression, and
retain legitimate underscore/hyphen attribute names. The camelCase restriction
on Papper configuration fields does not apply to these Markdown attributes.

The authored `pandoc/filters/shared/element-attributes.json` catalog is embedded
through typed Rust structures for export and loaded by shipped Lua filters for
native directive property validation and DOCX table metadata transport.
`docxMetadata` is an internal transport flag; it does not itself promise that a
property changes rendering. Value parsers and native rendering remain explicit
domain code. When changing supported attributes, update the catalog, runtime
implementation and manuscript guide together, then refresh editor snapshots.

Older version-one exports can omit this member. Consumers should retain their
working/offline attribute catalog in that case, validate a supplied catalog
before installing it, and refresh attribute help using the same executable cache
as configuration help. No separate build or engine query is needed.
