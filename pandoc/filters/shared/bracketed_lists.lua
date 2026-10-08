-- Convert consecutive handwritten `[number] item` lines into an ordered list.
--
-- Pandoc's OrderedList AST cannot represent square-bracket delimiters. The
-- wrapper class therefore records the authored marker style for output-specific
-- styling while the list itself remains semantically ordered. DOCX item styles
-- carry that marker into Papper's package editor, which sets native brackets.

local list_class = 'pmt-bracketed-list'
local docx_item_style = 'Papper Bracketed List Item'

--- Split one inline sequence at physical or soft Markdown line breaks.
local function split_lines(inlines)
  local lines, current = {}, {}
  for _, inline in ipairs(inlines or {}) do
    if inline.t == 'SoftBreak' or inline.t == 'LineBreak' then
      lines[#lines + 1] = current
      current = {}
    else
      current[#current + 1] = inline
    end
  end
  lines[#lines + 1] = current
  return lines
end

--- Remove insignificant whitespace around a line without changing inline content.
local function trim_inlines(inlines)
  local result = pandoc.List(inlines)
  while #result > 0 and result[1].t == 'Space' do
    result:remove(1)
  end
  while #result > 0 and result[#result].t == 'Space' do
    result:remove(#result)
  end
  return result
end

--- Parse one line and return its numeric marker and item content.
local function parse_item(line)
  local content = trim_inlines(line)
  if #content < 2 or content[1].t ~= 'Str' then
    return nil
  end

  local number = content[1].text:match('^%[(%d+)%]$')
  if number == nil or content[2].t ~= 'Space' then
    return nil
  end
  content:remove(1)
  content:remove(1)
  return tonumber(number), trim_inlines(content)
end

--- Parse a paragraph-like block only when every physical line is a marker item.
local function parse_block(block)
  if block.t ~= 'Para' and block.t ~= 'Plain' then
    return nil
  end

  local items = {}
  for _, line in ipairs(split_lines(block.content)) do
    local number, content = parse_item(line)
    if number == nil then
      return nil
    end
    items[#items + 1] = { number = number, content = content }
  end
  return items
end

--- Convert one validated run into a styled ordered list.
local function make_list(items)
  local list_items = pandoc.List()
  for _, item in ipairs(items) do
    local paragraph = pandoc.Plain(item.content)
    if FORMAT == 'docx' then
      -- Plain always uses Compact in Pandoc's DOCX writer, ignoring custom-style.
      -- Para preserves the marker style and still receives native list numbering.
      paragraph = pandoc.Div({ pandoc.Para(item.content) }, pandoc.Attr('', {}, {
        ['custom-style'] = docx_item_style,
      }))
    end
    list_items:insert(pandoc.List { paragraph })
  end
  local attributes = pandoc.ListAttributes(
    items[1].number,
    'Decimal',
    'Period'
  )
  local ordered = pandoc.OrderedList(list_items, attributes)
  return pandoc.Div({ ordered }, pandoc.Attr('', { list_class }))
end

--- Replace consecutive marker items while leaving non-consecutive text untouched.
local function convert_blocks(blocks)
  local result, index = pandoc.List(), 1
  while index <= #blocks do
    local parsed = parse_block(blocks[index])
    if parsed == nil then
      result:insert(blocks[index])
      index = index + 1
    else
      local items, next_index = {}, index
      while next_index <= #blocks do
        local candidate = parse_block(blocks[next_index])
        if candidate == nil then
          break
        end
        local expected = #items == 0 and candidate[1].number or items[#items].number + 1
        for _, item in ipairs(candidate) do
          if item.number ~= expected then
            candidate = nil
            break
          end
          expected = expected + 1
        end
        if candidate == nil then
          break
        end
        for _, item in ipairs(candidate) do
          items[#items + 1] = item
        end
        next_index = next_index + 1
      end

      if #items >= 2 then
        result:insert(make_list(items))
        index = next_index
      else
        result:insert(blocks[index])
        index = index + 1
      end
    end
  end
  return result
end

return {
  Blocks = convert_blocks,
}
