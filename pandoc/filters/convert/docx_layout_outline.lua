-- Recover the numeric outline actually used by Word headings, e.g. 第 %1 章.
-- Unused numbering definitions and ordinary numbered lists do not determine
-- the Markdown heading format.

local directory = pandoc.path.directory(PANDOC_SCRIPT_FILE)
local xml = dofile(pandoc.path.join {directory, 'docx_layout_xml.lua'})
local outline = {}

--- Read the value of a direct Word property.
local function value(node, name)
  local child = xml.child(node, name)
  return child and xml.attr(child, 'val')
end

--- Resolve a list instance and its level, including a full level override.
local function list_level(instances, definitions, id, index)
  local list = instances[id]
  local definition = definitions[value(list, 'abstractNumId') or '']
  if not list or not definition then return end
  for _, override in ipairs(list.children) do
    if xml.is(override, 'lvlOverride') and tonumber(xml.attr(override, 'ilvl')) == index then
      if value(override, 'startOverride') and value(override, 'startOverride') ~= '1' then return end
      local level = xml.child(override, 'lvl')
      if level then return level end
    end
  end
  for _, level in ipairs(definition.children) do
    if xml.is(level, 'lvl') and tonumber(xml.attr(level, 'ilvl')) == index then return level end
  end
end

--- Convert a standard decimal outline into a prefix and a title separator.
local function template_at(level, depth)
  if not level or value(level, 'numFmt') ~= 'decimal'
    or (value(level, 'start') or '1') ~= '1' then return end
  local restart = value(level, 'lvlRestart')
  if restart and tonumber(restart) ~= depth - 1 then return end
  local format = value(level, 'lvlText') or ''
  local numbers = {}
  for index = 1, depth do numbers[index] = '%%' .. index end
  local prefix, suffix = format:match('^(.-)' .. table.concat(numbers, '%.') .. '(.-)$')
  if not prefix or prefix:find('%%%d') or suffix:find('%%%d') then return end
  local separator = value(level, 'suff') == 'nothing' and '' or ' '
  return prefix, suffix .. separator
end

--- Store the shared heading format only when every used level agrees.
function outline.import(meta, body, source, property)
  local instances, definitions, templates, depth = {}, {}, {}, 0
  if source then
    for _, node in ipairs(xml.parse(source).children) do
      if xml.is(node, 'num') then instances[xml.attr(node, 'numId')] = node end
      if xml.is(node, 'abstractNum') then definitions[xml.attr(node, 'abstractNumId')] = node end
    end
  end
  local seen_heading, unsupported = false, false
  for _, paragraph in ipairs(body.children) do
    if xml.is(paragraph, 'p') then
      local level = property(paragraph, 'outlineLvl')
      local index = level and tonumber(xml.attr(level, 'val'))
      if index and index >= 0 and index < 9 then
        seen_heading = true
        local numbering = property(paragraph, 'numPr')
        local id = value(numbering, 'numId')
        if id and id ~= '0' then
          -- A numPr inherited from a heading style may omit ilvl.
          local list_index = tonumber(value(numbering, 'ilvl')) or index
          local current = index + 1
          local prefix, separator = template_at(list_level(instances, definitions, id, list_index), current)
          local existing = templates[current]
          if list_index ~= index or not prefix
            or (existing and (existing[1] ~= prefix or existing[2] ~= separator)) then
            unsupported = true
          else
            templates[current], depth = {prefix, separator}, math.max(depth, current)
          end
        end
      end
    end
  end
  if unsupported then return false end
  if depth == 0 then
    if seen_heading then meta.numberSections = false end
    return false
  end
  local prefixes, delimiters = {}, {}
  for index = 1, depth do
    local template = templates[index] or {'', ' '}
    -- Keep edge spaces through the Markdown metadata parser. Blank prefixes
    -- use inline metadata so the writer retains their position in the list.
    prefixes[index] = pandoc.MetaInlines {pandoc.RawInline('markdown', template[1]:gsub(' ', '&#32;'))}
    delimiters[index] = pandoc.MetaInlines {pandoc.RawInline('markdown', template[2]:gsub(' ', '&#32;'))}
  end
  meta.numberSections, meta.sectionsDepth = true, tostring(depth)
  meta.secHeaderTemplate = pandoc.MetaInlines {
    pandoc.RawInline('markdown', '$$secHeaderPrefix[n]$$$$i$$$$secHeaderDelim[n]$$$$t$$'),
  }
  meta.secHeaderPrefix, meta.secHeaderDelim = pandoc.MetaList(prefixes), pandoc.MetaList(delimiters)
  return true
end

return outline
