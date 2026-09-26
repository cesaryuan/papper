-- Apply shared paragraph styles used by the DOCX and HTML writers.
--
-- Equation explanations get Para Where. Body paragraphs immediately after
-- ordinary tables get Para After Table. Pandoc paragraphs do not carry block
-- attributes, so matching paragraphs are wrapped in custom-style Div blocks.

local where_style = "Para Where"
local after_table_style = "Para After Table"
-- Use a word boundary that also treats underscores as part of a word, like Python's \b.
local where_start = "^%s*[Ww][Hh][Ee][Rr][Ee]%f[^%w_]"
local equation_number_text = "^[%s()（）%[%]【】0-9ivxlcdmIVXLCDM.%-–—]*$"

-- Return the rows exposed by Pandoc table sections across AST versions.
local function section_rows(section)
  if section == nil then
    return {}
  end
  return section.rows or section.body or {}
end

-- Return true when an inline list contains a display-math node.
local function inlines_contain_display_math(inlines)
  for _, inline in ipairs(inlines or {}) do
    if inline.t == "Math" and inline.mathtype == "DisplayMath" then
      return true
    end
    if inline.content and inlines_contain_display_math(inline.content) then
      return true
    end
  end
  return false
end

-- Collect MathType formula and tab-stop markers across nested inline nodes.
local function mathtype_tab_equation_markers(inlines)
  local has_inline_math = false
  local has_openxml_tabs = false
  for _, inline in ipairs(inlines or {}) do
    if inline.t == "Math" and inline.mathtype == "InlineMath" then
      has_inline_math = true
    elseif inline.t == "RawInline"
      and inline.format == "openxml"
      and inline.text
      and inline.text:find("<w:tabs>", 1, true)
    then
      has_openxml_tabs = true
    end
    if inline.content then
      local nested_math, nested_tabs = mathtype_tab_equation_markers(inline.content)
      has_inline_math = has_inline_math or nested_math
      has_openxml_tabs = has_openxml_tabs or nested_tabs
    end
  end
  return has_inline_math, has_openxml_tabs
end

-- Return true for MathType's inline formula marker surrounded by Word tab stops.
local function inlines_contain_mathtype_tab_equation(inlines)
  local has_inline_math, has_openxml_tabs = mathtype_tab_equation_markers(inlines)
  return has_inline_math and has_openxml_tabs
end

-- Return visible non-math text from inline content for equation-table checks.
local function visible_inline_text(inlines)
  local parts = {}
  for _, inline in ipairs(inlines or {}) do
    if inline.t == "Str" then
      parts[#parts + 1] = inline.text or ""
    elseif inline.t == "Space" or inline.t == "SoftBreak" or inline.t == "LineBreak" then
      parts[#parts + 1] = " "
    elseif inline.content then
      parts[#parts + 1] = visible_inline_text(inline.content)
    end
  end
  return table.concat(parts)
end

-- Return true when one block contains display math in its nested content.
local function block_contains_display_math(block)
  if block == nil then
    return false
  end
  if block.t == "Para" or block.t == "Plain" then
    return inlines_contain_display_math(block.content)
  end
  if block.t == "Div" or block.t == "BlockQuote" or block.t == "Figure" then
    for _, child in ipairs(block.content or {}) do
      if block_contains_display_math(child) then
        return true
      end
    end
    return false
  end
  if block.t == "Table" then
    for _, row in ipairs(section_rows(block.head)) do
      for _, cell in ipairs(row.cells or {}) do
        for _, child in ipairs(cell.contents or {}) do
          if block_contains_display_math(child) then
            return true
          end
        end
      end
    end
    for _, body in ipairs(block.bodies or {}) do
      for _, row in ipairs(section_rows(body)) do
        for _, cell in ipairs(row.cells or {}) do
          for _, child in ipairs(cell.contents or {}) do
            if block_contains_display_math(child) then
              return true
            end
          end
        end
      end
    end
    for _, row in ipairs(section_rows(block.foot)) do
      for _, cell in ipairs(row.cells or {}) do
        for _, child in ipairs(cell.contents or {}) do
          if block_contains_display_math(child) then
            return true
          end
        end
      end
    end
  end
  return false
end

-- Return all rows in document order for one table-layout equation check.
local function table_rows(table_block)
  local rows = {}
  for _, row in ipairs(section_rows(table_block.head)) do
    rows[#rows + 1] = row
  end
  for _, body in ipairs(table_block.bodies or {}) do
    for _, row in ipairs(section_rows(body)) do
      rows[#rows + 1] = row
    end
  end
  for _, row in ipairs(section_rows(table_block.foot)) do
    rows[#rows + 1] = row
  end
  return rows
end

-- Return the visible non-math text contained by all cells in one table.
local function table_visible_text(table_block)
  local parts = {}
  for _, row in ipairs(table_rows(table_block)) do
    for _, cell in ipairs(row.cells or {}) do
      for _, block in ipairs(cell.contents or {}) do
        if block.t == "Para" or block.t == "Plain" then
          parts[#parts + 1] = visible_inline_text(block.content)
        end
      end
    end
  end
  return table.concat(parts, " ")
end

-- Match the punctuation-only equation layout shape used by the DOCX path.
local function is_equation_layout_table(table_block)
  local rows = table_rows(table_block)
  if #rows ~= 1 or #(rows[1].cells or {}) < 3 then
    return false
  end
  if not block_contains_display_math(table_block) then
    return false
  end
  return table_visible_text(table_block):match("^[%s()（）%[%]【】0-9ivxlcdmIVXLCDM.%-–—]*$") ~= nil
end

-- Return true when a block starts with the standalone word "where".
local function starts_with_where(block)
  if block == nil or (block.t ~= "Para" and block.t ~= "Plain") then
    return false
  end
  return pandoc.utils.stringify(block.content or ""):match(where_start) ~= nil
end

-- Match the DOCX paragraph rule: display math plus only an optional equation label.
local function is_equation_paragraph(block)
  if block == nil or (block.t ~= "Para" and block.t ~= "Plain") then
    return false
  end
  if inlines_contain_mathtype_tab_equation(block.content) then
    return true
  end
  if not block_contains_display_math(block) then
    return false
  end

  local visible_text = visible_inline_text(block.content)
  return visible_text:find("\t", 1, true) ~= nil
    or visible_text:match(equation_number_text) ~= nil
end

-- Return whether a block is an equation that can precede a where paragraph.
local function is_equation_block(block)
  if block == nil then
    return false, false
  end
  if block.t == "Table" then
    return is_equation_layout_table(block), true
  end
  if block.t == "Para" or block.t == "Plain" then
    return is_equation_paragraph(block), false
  end
  if block.t == "Div" then
    for _, child in ipairs(block.content or {}) do
      local is_equation, child_uses_table_layout = is_equation_block(child)
      if is_equation then
        if child_uses_table_layout then
          return true, true
        end
        if inlines_contain_mathtype_tab_equation(child.content) then
          return true, false
        end
        -- pandoc-crossref wraps tableEqns output in an equation Div before the
        -- DOCX writer turns it into a layout table. MathType's tab template is
        -- identified separately above and keeps its tab-layout spacing.
        return true, (block.identifier or ""):match("^eq:") ~= nil
      end
    end
  end
  return false, false
end

-- Wrap one paragraph in the custom style consumed by each target writer.
local function style_paragraph(paragraph, style, extra_attributes)
  local attributes = { ["custom-style"] = style }
  for key, value in pairs(extra_attributes or {}) do
    attributes[key] = value
  end
  return pandoc.Div({ paragraph }, pandoc.Attr("", {}, attributes))
end

-- Recursively process nested block lists while preserving immediate adjacency.
local function process_blocks(blocks)
  local previous = nil
  for index, block in ipairs(blocks or {}) do
    if block.t == "Div" or block.t == "BlockQuote" then
      block.content = process_blocks(block.content)
    elseif block.t == "Figure" then
      block.content = process_blocks(block.content)
    elseif block.t == "BulletList" or block.t == "OrderedList" then
      for item_index, item in ipairs(block.content or {}) do
        block.content[item_index] = process_blocks(item)
      end
    elseif block.t == "Table" then
      for _, row in ipairs(section_rows(block.head)) do
        for _, cell in ipairs(row.cells or {}) do
          cell.contents = process_blocks(cell.contents)
        end
      end
      for _, body in ipairs(block.bodies or {}) do
        for _, row in ipairs(section_rows(body)) do
          for _, cell in ipairs(row.cells or {}) do
            cell.contents = process_blocks(cell.contents)
          end
        end
      end
      for _, row in ipairs(section_rows(block.foot)) do
        for _, cell in ipairs(row.cells or {}) do
          cell.contents = process_blocks(cell.contents)
        end
      end
    end

    local follows_equation, table_layout = is_equation_block(previous)
    if starts_with_where(block) and follows_equation then
      local attributes = table_layout and { ["where-layout"] = "table" } or nil
      blocks[index] = style_paragraph(block, where_style, attributes)
    elseif previous ~= nil and previous.t == "Table" and block.t == "Para" then
      blocks[index] = style_paragraph(block, after_table_style)
    end
    previous = block
  end
  return blocks
end

-- Apply the filter once at block-list level so adjacency remains explicit.
function Pandoc(document)
  document.blocks = process_blocks(document.blocks)
  return document
end
