For manuscript Markdown and YAML syntax, run `papper guide syntax`. For reusable formatting and build settings, run `papper guide style`. These commands list topics with brief descriptions; read only the relevant topic using `papper guide syntax <topic>` or `papper guide style <topic>`. Parent topics list their subtopics. Use `--full` only when you need the complete guide.

## Style Metadata

If the user wants to change reusable style behavior, update `style.yml`. For settings that apply only to the Markdown being built, use a `papper-style` mapping in its YAML header; it accepts the same configuration as `style.yml` and takes priority over it. Ordinary header fields override Pandoc metadata. Run `papper guide style configuration-placement-and-precedence` for details.

## Pandoc Markdown Syntax

**Cross-references:**

- Figures: `![caption](path){#fig:label}` → `[@fig:label]`
- Tables: `: Caption {#tbl:label}` → `[@tbl:label]`
- Equations: `$$ math $$ {#eq:label}` → `[@eq:label]`
- Sections: `# Title {#sec:label}` → `[@sec:label]`
- Citations: `[@key]` (parenthetical), `[@key1; @key2]` (multiple)

**Tables:** Please prefer to use pipe_tables which is identical to PHP Markdown Extra tables.

```markdown
| **Method** | **Accuracy (%)** |
|:----------:|:----------------:|
| Baseline   | 78.3             |
| **Proposed** | **92.4**       |

: Performance comparison. {#tbl:results}
```

- Bold only for highlighting best results in comparison tables
- Alignment: `:--` left, `:--:` center, `--:` right
- For borderless DOCX tables, including aligned blocks of text, add
  `custom-style="TableNoBorder"` to the table caption attributes.
  For a layout without a visible caption, use `: {custom-style="TableNoBorder"}`.
- For advanced DOCX table formatting (cell merging, metadata), run `papper guide syntax advanced-table-formatting` and select the relevant subtopic.

**Subfigures:** Run `papper guide syntax subfigure-layouts` for details on handling multi-panel figures and subfigure cross-references.

**Pseudocode/Algorithms:**
```markdown
Write pseudocode as a one-column pipe table. Use bold control words such as `**for**` and `**if**`. Use a `tbl:` label for table cross-references; there is no separate algorithm numbering.

| **Algorithm: Library borrowing workflow** |
|---|
| **Input:** Request list $R$ |
| **for** each request **do** |
| \ \ Check availability |
| **end for** |

: Library borrowing workflow. {#tbl:algorithm}
```

## If User want to Change Citation Styles

1. Visit [Zotero Style Repository](https://www.zotero.org/styles) and find a CSL file for user required target journal or preferred citation style.
2. Download CSL file and save to some location, e.g., `pandoc/csl-style-downloaded.csl`
3. **Update `style.yml`** (run `papper guide style changing-citation-styles` for configuration details):
   ```yaml
   pandocMetadata:
     csl: pandoc/csl-style-downloaded.csl
   ```
## Academic Writing Rules

**Critical Don'ts:**
1. No subsections under Introduction — must be coherent narrative
2. No bold as pseudo-headings — no `**Label**: content...` patterns
3. No short bullet lists — use narrative paragraphs (3-7 sentences)
4. No `@fig:label shows...` paragraph openers — lead with narrative, reference at end
5. No separate "Related Work" section — merge into unified Introduction
6. Minimize `###` headings — prefer narrative flow within `##` sections
7. No single-sentence paragraphs (except transitions)
8. Bold only for table best-results and contribution statements

**Style and language preferences:**
- Use formal academic prose with simple, precise, and common research vocabulary
- Avoid contractions such as `it's` or `doesn't`; use full forms instead
- Prefer natural academic flow; remove mechanical transitions and obvious AI-sounding wording
- Do not rewrite for the sake of rewriting; keep passages that are already clear and publication-ready
- Avoid noun possessives for methods, models, or systems when possible; prefer `the performance of X` or similar structures
- Preserve established technical abbreviations such as `LLM` unless expansion is explicitly needed
- Do not add bold or italics for emphasis in body text

**Editing heuristics:**
- Split long or awkward sentences when clarity improves, but preserve technical meaning
- Prefer coherent narrative paragraphs over short bullets
- Avoid opening a paragraph with a bare cross-reference such as `[@fig:case] shows`; state the point first, then attach the reference
- Minimize em dashes; prefer commas, parentheses, or subordinate clauses when suitable
- Replace inflated words such as `leverage`, `delve into`, `pivotal`, `underscore`, and `unveil` with plainer alternatives when possible

## Reminders

- Image paths relative to `.md` location
- Citation keys must match `.bib` entries exactly
- Always preserve technical content when improving structure and flow
- When in doubt, follow conventions of the user's target journal

## Reviewer Replies

Add `reply: manuscript.md` to the reply Markdown YAML header, with the manuscript path relative to the reply file. Use `papper build docx reply.md` or `papper build html reply.md`; Papper detects the reply and reuses manuscript numbering. Read `papper guide syntax reviewer-replies` for line references and reply styles.
