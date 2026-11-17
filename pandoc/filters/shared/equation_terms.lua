-- Render optional thesis-style equation explanations as borderless layout tables.
-- For example, ::: {.equation-terms} containing `$k$` followed by `: Kernel size`
-- becomes a row with "式中", the right-aligned symbol, "——", and its description.
-- Ordinary definition lists and prose remain unchanged. LaTeX keeps a definition
-- list introduced by "式中" because Word table styles do not apply there.

-- Validate the explicit block before transforming it so malformed input is never dropped.
local function term_entries(div)
  local entries = {}
  for _, block in ipairs(div.content) do
    if block.t ~= "DefinitionList" then
      error("equation-terms: expected a definition list; write a symbol followed by ': Description'")
    end
    for _, item in ipairs(block.content) do
      if #item[1] == 0 or #item[2] ~= 1 or #item[2][1] == 0 then
        error("equation-terms: each symbol must have exactly one nonempty description")
      end
      entries[#entries + 1] = item
    end
  end
  if #entries == 0 then
    error("equation-terms: add at least one symbol followed by ': Description'")
  end
  return entries
end

-- Keep authored math and rich description blocks intact in an unnumbered four-column table.
local function terms_table(entries)
  local rows = {}
  for index, item in ipairs(entries) do
    rows[#rows + 1] = {
      index == 1 and { pandoc.Plain({ pandoc.Str("式中") }) } or {},
      { pandoc.Plain(item[1]) },
      { pandoc.Plain({ pandoc.Str("——") }) },
      item[2][1],
    }
  end
  local tbl = pandoc.utils.from_simple_table(pandoc.SimpleTable(
    {},
    { pandoc.AlignLeft, pandoc.AlignRight, pandoc.AlignCenter, pandoc.AlignLeft },
    { 0, 0, 0, 0 },
    {},
    rows
  ))
  tbl.attributes["custom-style"] = "TableNoBorder"
  tbl.attributes["custom-text-style"] = "式中"
  tbl.classes:insert("equation-terms-layout")
  tbl.attributes.autofit = "window"
  tbl.attributes.cell_margin = "0cm"
  if FORMAT:match("html") then
    -- The named paragraph style inherits body justification, which overrides
    -- HTML cell alignment. Apply local alignment to the actual cell paragraphs.
    local alignments = { "left", "right", "center", "left" }
    for _, body in ipairs(tbl.bodies) do
      for _, row in ipairs(body.body) do
        for index, cell in ipairs(row.cells) do
          -- Match Word's top-aligned symbols when a description spans several lines.
          cell.attributes.style = "vertical-align: top;"
          if index < 4 then
            -- Auto layout expands these narrow, nonwrapping columns to their
            -- actual content width, leaving the remaining space for descriptions.
            cell.attributes.style = cell.attributes.style
              .. " width: 1%; white-space: nowrap; padding-right: 5pt;"
          end
          cell.contents = {
            pandoc.Div(cell.contents, pandoc.Attr("", {}, { alignment = alignments[index] })),
          }
        end
      end
    end
  end
  return tbl
end

-- Convert only opted-in blocks; retaining the wrapper preserves authored anchors and classes.
function Div(div)
  if not div.classes:includes("equation-terms") then
    return nil
  end
  local entries = term_entries(div)
  if FORMAT:match("docx") or FORMAT:match("html") then
    div.content = { terms_table(entries) }
  else
    div.content:insert(1, pandoc.Para({ pandoc.Str("式中") }))
  end
  return div
end
