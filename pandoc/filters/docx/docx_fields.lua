-- Share empty-span Word fields between body paragraphs and page stories.
-- For example, **[]{t="pageNum"}** retains bold on a dynamic PAGE field;
-- []{t="date" format="yyyy-MM-dd"} inserts the current date as a DATE field.

local fields = {}
local formats = {
  decimal = 'Arabic', upperRoman = 'ROMAN', lowerRoman = 'roman',
  upperLetter = 'ALPHABETIC', lowerLetter = 'alphabetic',
  decimalEnclosedDash = 'ArabicDash', ordinal = 'Ordinal',
  cardinalText = 'CardText', ordinalText = 'OrdText', hex = 'Hex',
  Arabic = 'Arabic', ROMAN = 'ROMAN', roman = 'roman',
  ALPHABETIC = 'ALPHABETIC', alphabetic = 'alphabetic',
  ArabicDash = 'ArabicDash', Ordinal = 'Ordinal', CardText = 'CardText',
  OrdText = 'OrdText', Hex = 'Hex',
}
local first_values = {
  Arabic = '1', ROMAN = 'I', roman = 'i', ALPHABETIC = 'A', alphabetic = 'a',
  ArabicDash = '- 1 -', Ordinal = '1st', CardText = 'one', OrdText = 'first', Hex = '1',
}

--- Validate empty directives and reject misspelled attributes instead of silently ignoring them
function fields.validate(span, allowed, setting)
  if #span.content ~= 0 then error('DOCX ' .. span.attributes.t .. ' spans must be empty') end
  for key, _ in pairs(span.attributes) do
    if key ~= 't' and not allowed[key] then
      error('Unknown ' .. (setting or 'DOCX') .. ' ' .. span.attributes.t .. ' attribute: ' .. key)
    end
  end
end

--- Produce a cached date using the supported Word date-picture tokens
local function date_result(picture)
  local tokens = {
    {'yyyy', '%Y'}, {'MMMM', '%B'}, {'dddd', '%A'}, {'MMM', '%b'}, {'ddd', '%a'},
    {'yy', '%y'}, {'MM', '%m'}, {'dd', '%d'}, {'M', '%m'}, {'d', '%d'},
  }
  local result, position = {}, 1
  while position <= #picture do
    local matched = false
    for _, token in ipairs(tokens) do
      if picture:sub(position, position + #token[1] - 1) == token[1] then
        local value = os.date(token[2])
        if #token[1] == 1 then value = value:gsub('^0', '') end
        result[#result + 1] = value
        position = position + #token[1]
        matched = true
        break
      end
    end
    if not matched then
      local char = picture:sub(position, position)
      if char:match('[%a"\\\r\n]') then error('Unsupported DOCX date format: ' .. picture) end
      result[#result + 1] = char
      position = position + 1
    end
  end
  if #result == 0 then error('DOCX date format must not be empty') end
  return table.concat(result)
end

--- Preserve enclosing Markdown typography until native finalization creates complex fields
function fields.span(span, setting)
  local kind = span.attributes.t
  if kind ~= 'pageNum' and kind ~= 'date' and kind ~= 'currentDate' then return nil end
  fields.validate(span, {format = true}, setting)
  if kind == 'pageNum' then
    local name = span.attributes.format
    local switch = name and formats[name] or ''
    if name and not formats[name] then error('Unsupported ' .. (setting or 'DOCX') .. ' pageNum format: ' .. name) end
    local record = pandoc.json.encode({switch = switch, result = first_values[switch] or '1'})
    return pandoc.Span({pandoc.Str('PMT_FOOTER_PAGE:' .. record)},
      pandoc.Attr('', {}, {['custom-style'] = 'page number'}))
  end
  local picture = span.attributes.format or 'yyyy-MM-dd'
  return pandoc.Span({pandoc.Str('PMT_DOCX_FIELD:' .. pandoc.json.encode({
    instruction = ' DATE \\@ "' .. picture .. '" ', result = date_result(picture),
  }))}, pandoc.Attr('', {}, {['custom-style'] = 'Papper Date Field'}))
end

--- Parse single-line page stories as inlines and multiline stories as block Markdown
function fields.story(content)
  local parsed = pandoc.read('---\nfooter: ' .. pandoc.json.encode(content) .. '\n---\n', 'markdown')
  local value = parsed.meta.footer
  local blocks = pandoc.utils.type(value) == 'Inlines' and {pandoc.Para(value)} or value or {}
  return pandoc.Pandoc(blocks):walk({
    Span = function(span)
      local result = fields.span(span)
      if span.attributes.t and not result then error('Unsupported DOCX header/footer directive: ' .. span.attributes.t) end
      return result
    end,
    Note = function() error('DOCX header/footer does not support footnotes or endnotes') end,
    Cite = function() error('DOCX header/footer does not support bibliography citations') end,
  }).blocks
end

return fields
