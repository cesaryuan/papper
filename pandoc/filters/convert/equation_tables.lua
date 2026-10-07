-- Flatten Word's equation layout tables into one inline math paragraph per row.
-- Run after mtef_parser.lua has recovered MathType previews as DisplayMath, and
-- before crossrefs.lua assigns equation identifiers. A two-cell formula/label
-- row, or a three-cell blank/formula/label row, is treated as equation layout.
-- Example (table cells shown with vertical separators):
--   | $$x^2+y^2$$ | (7) | -> $x^2+y^2$ (7)
--   | blank | $$E=mc^2$$ | []{#_Ref123 .anchor}(3) |
--     -> $E=mc^2$ []{#_Ref123 .anchor}(3)
-- Inline math keeps the label on the same line; its bookmark is preserved.
-- A descriptive label such as "energy balance" is retained after the math.
-- Adjacent Word equation tables can merge into a multi-row table; every row
-- must qualify before the entire table is replaced, in its original row order.
-- Only simple, unmerged cells qualify. Captioned tables, a
-- nonblank leading cell, multiple formulas or structured label content remain
-- tables, preserving authored data instead of assuming every table is layout.
-- Run directly with an already-decoded document:
--   pandoc input.md -t markdown -L pandoc/filters/convert/equation_tables.lua

--- Collect all rows in reading order, including equations promoted to headers.
local function table_rows(table_element)
  local rows = {}
  for _, row in ipairs(table_element.head.rows) do rows[#rows + 1] = row end
  for _, body in ipairs(table_element.bodies) do
    for _, row in ipairs(body.head) do rows[#rows + 1] = row end
    for _, row in ipairs(body.body) do rows[#rows + 1] = row end
  end
  for _, row in ipairs(table_element.foot.rows) do rows[#rows + 1] = row end
  return rows
end

--- Read plain inline content from a cell without swallowing nested structures.
local function cell_inlines(cell)
  if cell.row_span ~= 1 or cell.col_span ~= 1 then return nil end
  if #cell.contents == 0 then return {} end
  if #cell.contents ~= 1 then return nil end
  local block = cell.contents[1]
  if block.t ~= 'Plain' and block.t ~= 'Para' then return nil end
  return block.content
end

--- Test whether a cell is visually blank, ignoring only whitespace inlines.
local function is_blank(inlines)
  if not inlines then return false end
  for _, inline in ipairs(inlines) do
    if inline.t ~= 'Space' and inline.t ~= 'SoftBreak' and inline.t ~= 'LineBreak' then
      if inline.t ~= 'Str' or inline.text:match('%S') then return false end
    end
  end
  return true
end

--- Return a single converted display equation from one cell.
local function equation(inlines)
  if not inlines then return nil end
  local math = nil
  for _, inline in ipairs(inlines) do
    if inline.t == 'Math' and inline.mathtype == 'DisplayMath' and not math then
      math = inline
    elseif inline.t ~= 'Space' and inline.t ~= 'SoftBreak' and inline.t ~= 'LineBreak' then
      return nil
    end
  end
  return math
end

--- Keep a label cell only when it consists of ordinary inline text or links.
local function is_simple_label(inlines)
  if not inlines or is_blank(inlines) then return false end
  for _, inline in ipairs(inlines) do
    if inline.t ~= 'Str' and inline.t ~= 'Space' and inline.t ~= 'SoftBreak'
      and inline.t ~= 'LineBreak' and inline.t ~= 'Span' and inline.t ~= 'Link' then
      return false
    end
  end
  return true
end

--- Convert one simple formula/label row without modifying the source cells.
local function equation_paragraph(row)
  if #row.cells ~= 2 and #row.cells ~= 3 then return nil end
  local formula_index = #row.cells == 3 and 2 or 1
  if formula_index == 2 and not is_blank(cell_inlines(row.cells[1])) then return nil end
  local math = equation(cell_inlines(row.cells[formula_index]))
  local label = cell_inlines(row.cells[formula_index + 1])
  if not math or not is_simple_label(label) then return nil end
  -- Layout tables need inline output so the formula and its label stay together.
  local result = { pandoc.Math('InlineMath', math.text), pandoc.Space() }
  for _, inline in ipairs(label) do result[#result + 1] = inline end
  return pandoc.Para(result)
end

--- Replace an equation layout table only if every row can be safely flattened.
function Table(table_element)
  if #table_element.caption.long > 0 or table_element.caption.short then return nil end
  local rows = table_rows(table_element)
  if #rows == 0 then return nil end
  local paragraphs = {}
  for _, row in ipairs(rows) do
    local paragraph = equation_paragraph(row)
    -- Merged Word layouts may contain ordinary data; keep the whole table when
    -- any row fails recognition so earlier equations cannot be lost or detached.
    if not paragraph then return nil end
    paragraphs[#paragraphs + 1] = paragraph
  end
  return paragraphs
end
