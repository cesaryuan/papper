-- Flatten one-row equation layout tables after mtef_parser.lua has produced Math.
-- Only a two-cell formula/label or three-cell blank/formula/label row qualifies.

--- Return the only row in a table, including a row Pandoc promoted to the head.
local function only_row(table_element)
  local rows = {}
  for _, row in ipairs(table_element.head.rows) do rows[#rows + 1] = row end
  for _, body in ipairs(table_element.bodies) do
    for _, row in ipairs(body.head) do rows[#rows + 1] = row end
    for _, row in ipairs(body.body) do rows[#rows + 1] = row end
  end
  for _, row in ipairs(table_element.foot.rows) do rows[#rows + 1] = row end
  return #rows == 1 and rows[1] or nil
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

--- Replace a confirmed layout table with one normal equation paragraph.
function Table(table_element)
  if #table_element.caption.long > 0 or table_element.caption.short then return nil end
  local row = only_row(table_element)
  if not row or (#row.cells ~= 2 and #row.cells ~= 3) then return nil end
  local formula_index = #row.cells == 3 and 2 or 1
  if formula_index == 2 and not is_blank(cell_inlines(row.cells[1])) then return nil end
  local math = equation(cell_inlines(row.cells[formula_index]))
  local label = cell_inlines(row.cells[formula_index + 1])
  if not math or not is_simple_label(label) then return nil end
  local result = { math, pandoc.Space() }
  for _, inline in ipairs(label) do result[#result + 1] = inline end
  return pandoc.Para(result)
end
