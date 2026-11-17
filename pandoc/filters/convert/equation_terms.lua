-- Recover thesis-style symbol explanations as editable .equation-terms blocks.
-- For example, | 式中 | $k$ | —— | Kernel size | becomes:
--   ::: {.equation-terms}
--   $k$
--   : Kernel size
--   :::
-- Also accept a vertically merged label, a separate preceding 式中 paragraph,
-- and a separator at the start of the description cell instead of its own column.
-- Run after MathType recovery and table-caption pairing, before cross-references.
-- Every row must match; captioned tables and other merged cells remain tables.

local recovered_count = 0
local separators = {'——', '――', '--', '－－', '—', '―'}

--- Collect rows across Pandoc's header/body/footer partitions in reading order.
local function table_rows(tbl)
  local rows = {}
  for _, row in ipairs(tbl.head.rows) do rows[#rows + 1] = row end
  for _, body in ipairs(tbl.bodies) do
    for _, row in ipairs(body.head) do rows[#rows + 1] = row end
    for _, row in ipairs(body.body) do rows[#rows + 1] = row end
  end
  for _, row in ipairs(tbl.foot.rows) do rows[#rows + 1] = row end
  return rows
end

--- Remove layout whitespace when recognizing a label or a standalone separator.
local function compact(text)
  return text:gsub('%s', ''):gsub('　', ''):gsub(' ', '')
end

--- Identify a label without treating ordinary explanatory prose as a label.
local function is_label(blocks)
  if #blocks ~= 1 or (blocks[1].t ~= 'Para' and blocks[1].t ~= 'Plain') then return false end
  local text = compact(pandoc.utils.stringify(blocks))
  return text == '式中' or text == '式中：' or text == '式中:'
end

--- Distinguish blank Word paragraphs from content, including pictures with no alt text.
local function is_blank(blocks)
  if compact(pandoc.utils.stringify(blocks)) ~= '' then return false end
  local visible = false
  pandoc.Pandoc(blocks):walk({
    Inline = function(inline)
      if inline.t == 'Image' or inline.t == 'Math' or inline.t == 'Note'
        or inline.t == 'RawInline' or inline.t == 'Span' and inline.identifier ~= '' then
        visible = true
      end
    end,
    Block = function(block)
      if block.t ~= 'Plain' and block.t ~= 'Para' then visible = true end
    end,
  })
  return not visible
end

--- Read a single unmerged symbol paragraph and keep recovered MathType symbols inline.
local function symbol_inlines(cell)
  if cell.row_span ~= 1 or cell.col_span ~= 1 or #cell.contents ~= 1
    or is_blank(cell.contents) then return nil end
  local block = cell.contents[1]
  if block.t ~= 'Para' and block.t ~= 'Plain' then return nil end
  return block.content:walk({Math = function(math)
    return pandoc.Math('InlineMath', math.text)
  end})
end

--- Recognize only the separator marks used by these layouts.
local function is_separator(blocks)
  if #blocks ~= 1 or (blocks[1].t ~= 'Para' and blocks[1].t ~= 'Plain') then return false end
  local text = compact(pandoc.utils.stringify(blocks))
  for _, separator in ipairs(separators) do
    if text == separator then return true end
  end
  return false
end

--- Strip a leading separator while retaining formatted description inlines and later blocks.
local function description_without_separator(blocks)
  local first = blocks[1]
  if not first or (first.t ~= 'Para' and first.t ~= 'Plain') then return nil end
  local text = pandoc.utils.stringify(first.content)
  local leading = text:match('^%s*')
  local rest, length = text:sub(#leading + 1), nil
  for _, separator in ipairs(separators) do
    if rest:sub(1, #separator) == separator then
      local following = rest:sub(#separator + 1):match('^%s*')
      length = #leading + #separator + #following
      break
    end
  end
  if not length then return nil end
  -- Word can split a dash and its following space across formatting runs.
  -- Work on a copy so a later nonmatching row cannot modify a retained table.
  local result = pandoc.Blocks(blocks):walk({})
  result[1].content = result[1].content:walk({
    traverse = 'topdown',
    Inline = function(inline)
      if length == 0 then return nil end
      if inline.t == 'Str' then
        local consumed = math.min(length, #inline.text)
        length = length - consumed
        return consumed == #inline.text and {} or pandoc.Str(inline.text:sub(consumed + 1))
      elseif inline.t == 'Space' or inline.t == 'SoftBreak' or inline.t == 'LineBreak' then
        length = length - 1
        return {}
      end
    end,
  })
  if length ~= 0 then return nil end
  if is_blank({result[1]}) then result:remove(1) end
  return not is_blank(result) and result or nil
end

--- Recover a whole layout, allowing vertical merges only in the label column.
local function recover_table(tbl, external_label)
  if #tbl.caption.long > 0 or tbl.caption.short then return nil end
  local columns = #tbl.colspecs
  local separate = columns == (external_label and 3 or 4)
  if not separate and columns ~= (external_label and 2 or 3) then return nil end
  local rows, entries, label_remaining = table_rows(tbl), {}, 0
  if #rows == 0 then return nil end
  for index, row in ipairs(rows) do
    local offset = 0
    if not external_label then
      if label_remaining > 0 then
        label_remaining = label_remaining - 1
      else
        local label = row.cells[1]
        if not label or label.col_span ~= 1 then return nil end
        if not is_label(label.contents) and (index == 1 or not is_blank(label.contents)) then return nil end
        label_remaining, offset = label.row_span - 1, 1
      end
    end
    local expected = external_label and columns or columns - 1 + offset
    if #row.cells ~= expected then return nil end
    local symbol = symbol_inlines(row.cells[offset + 1])
    if not symbol then return nil end
    local description = row.cells[offset + (separate and 3 or 2)]
    if description.row_span ~= 1 or description.col_span ~= 1
      or is_blank(description.contents) then return nil end
    local blocks = description.contents
    if separate then
      local separator = row.cells[offset + 2]
      if separator.row_span ~= 1 or separator.col_span ~= 1
        or not is_separator(separator.contents) then return nil end
    else
      blocks = description_without_separator(blocks)
      if not blocks then return nil end
    end
    entries[#entries + 1] = {symbol, {blocks}}
  end
  if label_remaining ~= 0 then return nil end
  recovered_count = recovered_count + 1
  return pandoc.Div({pandoc.DefinitionList(entries)}, pandoc.Attr(tbl.identifier, {'equation-terms'}))
end

--- Replace matching tables and consume an adjacent standalone label only on success.
function Blocks(blocks)
  local result, index = pandoc.Blocks {}, 1
  while index <= #blocks do
    local block, next_block = blocks[index], blocks[index + 1]
    local recovered
    if is_label({block}) and next_block and next_block.t == 'Table' then
      recovered = recover_table(next_block, true)
      if recovered then index = index + 1 end
    elseif block.t == 'Table' then
      recovered = recover_table(block, false)
    end
    result:insert(recovered or block)
    index = index + 1
  end
  return result
end

--- Report recovered layouts once per imported document.
function Pandoc(document)
  if recovered_count > 0 then
    io.stderr:write('[convert] Recovered ' .. recovered_count .. ' equation-terms block(s)\n')
  end
  return document
end
