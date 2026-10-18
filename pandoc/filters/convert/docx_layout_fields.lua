-- Recover dynamic fields before Pandoc replaces them with cached display text.
-- PAGE becomes []{t="pageNum"} in body and stories. Other story fields, e.g.
-- NUMPAGES and STYLEREF, use the existing generic t="field" representation.

local directory = pandoc.path.directory(PANDOC_SCRIPT_FILE)
local xml = dofile(pandoc.path.join {directory, 'docx_layout_xml.lua'})
local fields = {}
local formats = {
  Arabic='decimal', ROMAN='upperRoman', roman='lowerRoman',
  ALPHABETIC='upperLetter', alphabetic='lowerLetter', ArabicDash='decimalEnclosedDash',
  Ordinal='ordinal', CardText='cardinalText', OrdText='ordinalText', Hex='hex',
}
local section_formats = {
  decimal=true, upperRoman=true, lowerRoman=true, upperLetter=true, lowerLetter=true,
  decimalEnclosedDash=true, ordinal=true, cardinalText=true, ordinalText=true, hex=true,
}

--- Quote attributes using entities so quotes, line breaks, and ampersands round-trip.
function fields.quote(value)
  return '"' .. tostring(value):gsub('&', '&amp;'):gsub('"', '&quot;')
    :gsub('\r', '&#13;'):gsub('\n', '&#10;'):gsub('\t', '&#9;') .. '"'
end

--- Express a field in supported Papper syntax, preserving unsupported PAGE switches generically.
local function fragment(record, format, all_fields)
  local instruction = record.instruction:gsub('%s+', ' '):match('^%s*(.-)%s*$')
  local command, rest = instruction:match('^(%a+)(.*)$')
  if not command then return nil end
  if command:upper() == 'PAGE' then
    local selected = format ~= 'decimal' and section_formats[format] and format or nil
    local unknown, explicit = false, false
    for switch in rest:gmatch('\\%*%s+([^%s]+)') do
      if formats[switch] then selected, explicit = formats[switch], true
      elseif switch:upper() ~= 'MERGEFORMAT' and switch:upper() ~= 'CHARFORMAT' then unknown = true end
    end
    -- Other PAGE options cannot be represented by pageNum, so keep the original
    -- Word instruction instead of silently changing the numbering format.
    local remaining = rest:gsub('\\%*%s+[^%s]+', ''):gsub('%s+', '')
    if not unknown and remaining == '' and (section_formats[format] or format == nil) then
      return '[]{t="pageNum"' .. (selected and ' format=' .. fields.quote(selected) or '') .. '}', not explicit
    end
  elseif not all_fields then
    return nil
  end
  return '[]{t="field" instruction=' .. fields.quote(instruction)
    .. ' result=' .. fields.quote(record.result or '') .. '}'
end

--- Build a Word text node containing a collision-free marker.
local function text_node(context, text)
  return xml.word(context, 't', {xml.escape(text)})
end

--- Replace supported fields while retaining surrounding Word runs and typography.
function fields.prepare(root, format, all_fields, marker)
  local stack, sensitive = {}, false
  -- Field instructions may span several runs. Identify the complete field
  -- before editing its cached result; malformed/unclosed fields remain intact.
  local function scan(node)
    node._layout_owner = stack[#stack]
    if xml.is(node, 'fldSimple') then
      local record = {instruction=xml.attr(node, 'instr') or '', result=xml.text(node)}
      node._layout_fragment, node._layout_sensitive = fragment(record, format, all_fields)
      sensitive = sensitive or node._layout_sensitive or false
      return
    end
    if xml.is(node, 'fldChar') then
      local kind = xml.attr(node, 'fldCharType')
      if kind == 'begin' then
        local record = {instruction='', result='', parent=stack[#stack]}
        stack[#stack + 1], node._layout_begin = record, record
      elseif kind == 'separate' and stack[#stack] then
        stack[#stack].separated = true
      elseif kind == 'end' and stack[#stack] then
        local record = table.remove(stack)
        record.replacement, record.sensitive = fragment(record, format, all_fields)
        sensitive = sensitive or record.sensitive or false
      end
    elseif xml.is(node, 'instrText') and stack[#stack] then
      stack[#stack].instruction = stack[#stack].instruction .. xml.decode(table.concat(node.children))
    elseif xml.is(node, 't') then
      for _, record in ipairs(stack) do
        if record.separated then record.result = record.result .. xml.decode(table.concat(node.children)) end
      end
    end
    for _, child in ipairs(node.children) do if type(child) == 'table' then scan(child) end end
  end
  scan(root)

  --- Find the outermost replaced field owning a cached result node.
  local function replaced(record)
    if not record then return nil end
    return replaced(record.parent) or (record.replacement and record or nil)
  end

  --- Rewrite only recognized fields; other body fields remain available to crossref recovery.
  local function rewrite(node)
    local owner = replaced(node._layout_owner)
    if node._layout_fragment and not owner then
      local run = xml.word(node, 'r', {text_node(node, marker(node._layout_fragment))})
      local first_run = xml.child(node, 'r')
      if first_run and xml.child(first_run, 'rPr') then table.insert(run.children, 1, xml.child(first_run, 'rPr')) end
      return run
    end
    if node._layout_begin and node._layout_begin.replacement and not owner then
      return text_node(node, marker(node._layout_begin.replacement))
    end
    if owner and not xml.is(node, 'r') and not xml.is(node, 'p') and not xml.is(node, 'hyperlink') then return nil end
    local children = {}
    for _, child in ipairs(node.children) do
      local value = child
      if type(child) == 'table' then value = rewrite(child) end
      if value then children[#children + 1] = value end
    end
    node.children = children
    return node
  end
  return rewrite(root), sensitive
end

return fields
