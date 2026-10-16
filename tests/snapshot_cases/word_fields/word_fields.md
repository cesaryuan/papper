---
title: Custom Word fields
papperSettings:
  mathtype: false
  docxHeader:
    content: 'Pages: **[]{t="field" instruction="NUMPAGES" result="1"}** · Chapter: []{t="field" instruction="STYLEREF 1" result="Heading pending"}'
  docxFooter:
    content: 'Page []{t="field" instruction="PAGE" result="1"} of []{t="field" instruction="NUMPAGES" result="1"}'
---

# Authored instructions

Total pages: **[]{t="field" instruction="NUMPAGES" result="1"}**.

Current heading: []{t="field" instruction="STYLEREF 1" result="Heading pending"}.

Figure number: []{t="field" instruction='SEQ Example \\* ARABIC' result="1"}.

Quoted picture: []{t="field" instruction='DATE \\@ "yyyy-MM-dd"' result="2000-01-02"}.

Chinese instruction: *[]{t="field" instruction='SEQ 自定义 \\* ARABIC' result="一"}*.

XML characters: []{t="field" instruction='IF 1 < 2 "A & B" "C > D"' result="A & B"}.

No initial result: []{t="field" instruction="NUMPAGES"}.

Identical instruction and result: []{t="field" instruction="PAGE" result="PAGE"}.

Spaces in result: before[]{t="field" instruction="NUMPAGES" result="&#32;1&#32;"}after.

[]{t="sectionBreak" header='Section pages: []{t="field" instruction="NUMPAGES" result="1"} · Chapter: []{t="field" instruction="STYLEREF 1" result="Heading pending"}' footer='Page []{t="field" instruction="PAGE" result="1"}'}

# Next section

Current heading: []{t="field" instruction="STYLEREF 1" result="Heading pending"}.

Section headers and footers also support custom fields.
