-- Keep OOXML structure intact while layout import edits selected nodes.
-- For example, replacing a PAGE field must leave surrounding runs, hyperlinks,
-- and DrawingML untouched. Quoted attributes may contain ">", and historical
-- sectPrChange content must not be mistaken for an active section.

local lpeg = require 'lpeg'
local xml = {}
local W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
local P, S = lpeg.P, lpeg.S
local quoted = P('"') * (1 - P('"'))^0 * P('"') + P("'") * (1 - P("'"))^0 * P("'")
local token = P('<!--') * (1 - P('-->'))^0 * P('-->')
  + P('<![CDATA[') * (1 - P(']]>'))^0 * P(']]>')
  + P('<?') * (1 - P('?>'))^0 * P('?>')
  + P('<') * (quoted + (1 - S('>"\'')))^0 * P('>')
  + (1 - P('<'))^1

--- Decode XML text and attributes, including numeric Unicode entities.
function xml.decode(value)
  value = value:gsub('&#x([%da-fA-F]+);', function(hex) return utf8.char(tonumber(hex, 16)) end)
    :gsub('&#(%d+);', function(decimal) return utf8.char(tonumber(decimal)) end)
  return (value:gsub('&quot;', '"'):gsub('&apos;', "'"):gsub('&lt;', '<')
    :gsub('&gt;', '>'):gsub('&amp;', '&'))
end

--- Escape XML text or quoted attribute content.
function xml.escape(value)
  return (tostring(value):gsub('&', '&amp;'):gsub('<', '&lt;'):gsub('>', '&gt;')
    :gsub('"', '&quot;'):gsub("'", '&apos;'))
end

--- Parse already validated DOCX XML, retaining unknown nodes and original tags.
function xml.parse(source)
  local stack, root = {}, nil
  local tokens = lpeg.match(lpeg.Ct(lpeg.C(token)^0) * -P(1), source)
  assert(tokens, 'Cannot tokenize DOCX layout XML')
  for _, text in ipairs(tokens) do
    local name = text:match('^<([%w_:.-]+)')
    if text:match('^</') then
      local node = assert(table.remove(stack), 'Unexpected DOCX XML closing tag')
      node.close = text
    elseif name and not text:match('^<!') then
      local node = {name = name, attrs = {}, children = {}, open = text, close = ''}
      for key, _, value in text:gmatch('([%w_:.-]+)%s*=%s*(["\'])(.-)%2') do
        node.attrs[key] = xml.decode(value)
      end
      node.ns = {}
      if stack[#stack] then for key, value in pairs(stack[#stack].ns) do node.ns[key] = value end end
      for key, value in pairs(node.attrs) do
        if key == 'xmlns' then node.ns[''] = value
        elseif key:match('^xmlns:') then node.ns[key:sub(7)] = value end
      end
      if stack[#stack] then table.insert(stack[#stack].children, node) else root = node end
      if not text:match('/%s*>$') then stack[#stack + 1] = node end
    elseif stack[#stack] then
      table.insert(stack[#stack].children, text)
    end
  end
  assert(root and #stack == 0, 'Incomplete DOCX layout XML')
  return root
end

--- Recognize a Word element by namespace, even when its prefix is not w.
function xml.is(node, name)
  if type(node) ~= 'table' then return false end
  local prefix, local_name = node.name:match('^(.-):([^:]+)$')
  return (local_name or node.name) == name and node.ns[prefix or ''] == W
end

--- Read an attribute by local name.
function xml.attr(node, name)
  for key, value in pairs(node.attrs) do
    if key:match('([^:]+)$') == name then return value end
  end
end

--- Find the first direct Word child with a given local name.
function xml.child(node, name)
  if not node then return nil end
  for _, child in ipairs(node.children) do if xml.is(child, name) then return child end end
end

--- Visit XML elements in source order, excluding text tokens.
function xml.walk(node, action)
  action(node)
  for _, child in ipairs(node.children) do
    if type(child) == 'table' then xml.walk(child, action) end
  end
end

--- Collect visible Word text without including field instructions.
function xml.text(node)
  local result = {}
  xml.walk(node, function(child)
    if xml.is(child, 't') then
      for _, text in ipairs(child.children) do
        if type(text) == 'string' then result[#result + 1] = xml.decode(text) end
      end
    end
  end)
  return table.concat(result)
end

--- Read an on/off Word property, including explicit false values.
function xml.enabled(node)
  return node ~= nil and not ({['0']=true, ['false']=true, off=true})[xml.attr(node, 'val') or 'true']
end

--- Create a Word node in the same namespace as its context.
function xml.word(context, name, children)
  local prefix = context.name:match('^(.-):') or 'w'
  return {name = prefix .. ':' .. name, ns = context.ns, attrs = {},
    children = children or {}, dirty = true}
end

--- Change an attribute while preserving the other namespace declarations.
function xml.set(node, key, value)
  node.attrs[key], node.dirty = tostring(value), true
end

--- Serialize edited nodes and retain original XML for every unchanged tag.
function xml.write(node)
  if type(node) == 'string' then return node end
  local open, close = node.open, node.close
  if node.dirty or not open then
    local names, attributes = {}, {}
    for name in pairs(node.attrs) do names[#names + 1] = name end
    table.sort(names)
    for _, name in ipairs(names) do attributes[#attributes + 1] = ' ' .. name .. '="' .. xml.escape(node.attrs[name]) .. '"' end
    open = '<' .. node.name .. table.concat(attributes) .. '>'
    close = '</' .. node.name .. '>'
  elseif open:match('/%s*>$') and #node.children > 0 then
    open, close = open:gsub('/%s*>$', '>'), '</' .. node.name .. '>'
  end
  local parts = {open}
  for _, child in ipairs(node.children) do parts[#parts + 1] = xml.write(child) end
  parts[#parts + 1] = close or ''
  return table.concat(parts)
end

return xml
