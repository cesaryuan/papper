-- Lexically normalize SVG child images without reserializing unrelated XML
-- Embedded local resources and PNG cache keys are based on actual source bytes
-- The small renderer is used only for SVGZ decompression and PNG pixels

local resource = dofile(pandoc.path.join {
  pandoc.path.directory(PANDOC_SCRIPT_FILE), '../shared/filter_resources.lua',
})
local M = {}
local xlink = 'http://www.w3.org/1999/xlink'
local mime_types = {png = 'image/png', jpg = 'image/jpeg', jpeg = 'image/jpeg',
  gif = 'image/gif', webp = 'image/webp', svg = 'image/svg+xml', svgz = 'image/svg+xml',
  bmp = 'image/bmp', tif = 'image/tiff', tiff = 'image/tiff', avif = 'image/avif',
  ico = 'image/vnd.microsoft.icon', pdf = 'application/pdf', emf = 'image/emf', wmf = 'image/wmf'}
local alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'

--- Encode child bytes in base64 with no external program or whitespace additions
local function base64(bytes)
  local result, chunk = {}, {}
  for index = 1, #bytes, 3 do
    local a, b, c = bytes:byte(index, index + 2)
    local number = (a << 16) | ((b or 0) << 8) | (c or 0)
    chunk[#chunk + 1] = alphabet:sub(((number >> 18) & 63) + 1, ((number >> 18) & 63) + 1)
      .. alphabet:sub(((number >> 12) & 63) + 1, ((number >> 12) & 63) + 1)
      .. (b and alphabet:sub(((number >> 6) & 63) + 1, ((number >> 6) & 63) + 1) or '=')
      .. (c and alphabet:sub((number & 63) + 1, (number & 63) + 1) or '=')
    if #chunk == 1024 then result[#result + 1], chunk = table.concat(chunk), {} end
  end
  if #chunk > 0 then result[#result + 1] = table.concat(chunk) end
  return table.concat(result)
end

--- Decode XML predefined/numeric entities so escaped and Unicode hrefs resolve
local function unescape(text)
  local named = {amp = '&', lt = '<', gt = '>', quot = '"', apos = "'"}
  assert(not text:gsub('&[^;]+;', ''):find('&', 1, true), 'Invalid SVG XML: unescaped ampersand')
  return (text:gsub('&([^;]+);', function(entity)
    if named[entity] then return named[entity] end
    local code = entity:match('^#x(%x+)$')
    code = code and tonumber(code, 16) or tonumber(entity:match('^#(%d+)$'))
    assert(code and code > 0 and code <= 0x10ffff and not (code >= 0xd800 and code <= 0xdfff),
      'Invalid SVG XML entity: &' .. entity .. ';')
    return utf8.char(code)
  end))
end

--- Escape replacement href values without changing the surrounding SVG markup
local function escape(text)
  return (text:gsub('&', '&amp;'):gsub('"', '&quot;'):gsub('<', '&lt;'):gsub('>', '&gt;'))
end

--- Locate a tag boundary without treating greater-than signs inside quotes as markup
local function tag_end(text, start)
  local quote
  for index = start, #text do
    local character = text:sub(index, index)
    if character == quote then quote = nil
    elseif not quote and (character == '"' or character == "'") then quote = character
    elseif not quote and character == '>' then return index end
  end
  error('Invalid SVG XML: incomplete opening tag')
end

--- Parse quoted attributes and retain their exact value offsets for lexical edits
local function attributes(text, first, last)
  local result, seen, cursor = {}, {}, first
  while cursor < last do
    local whitespace = text:match('^%s*', cursor)
    cursor = cursor + #whitespace
    if cursor >= last or text:sub(cursor, cursor) == '/' then break end
    assert(#whitespace > 0, 'Invalid SVG XML: attributes require whitespace')
    local name = text:match('^([%a_:\128-\255][%w_:%.%-\128-\255]*)', cursor)
    assert(name and not seen[name], 'Invalid SVG XML: duplicate or malformed attribute')
    cursor = cursor + #name
    local separator = text:match('^%s*=%s*', cursor)
    assert(separator, 'Invalid SVG XML: missing attribute assignment')
    cursor = cursor + #separator
    local quote = text:sub(cursor, cursor)
    assert(quote == '"' or quote == "'", 'Invalid SVG XML: unquoted attribute')
    local ending = assert(text:find(quote, cursor + 1, true), 'Invalid SVG XML: unclosed attribute')
    assert(ending < last, 'Invalid SVG XML: unclosed attribute')
    local raw = text:sub(cursor + 1, ending - 1)
    assert(not raw:find('<', 1, true), 'Invalid SVG XML: less-than in an attribute')
    result[#result + 1] = {name = name, value = unescape(raw), first = cursor + 1, last = ending - 1}
    seen[name], cursor = true, ending + 1
  end
  return result
end

--- Parse element scopes while ignoring comments/CDATA and rejecting malformed XML
local function image_elements(text)
  assert(utf8.len(text), 'SVG is not UTF-8')
  assert(not text:find('[%z\1-\8\11\12\14-\31]'), 'Invalid SVG XML control character')
  local images, stack, cursor, root_count = {}, {}, 1, 0
  while cursor <= #text do
    local first = text:find('<', cursor, true)
    local plain = text:sub(cursor, first and first - 1 or #text)
    if #stack == 0 then
      assert(plain:gsub('^\239\187\191', ''):match('^%s*$'), 'Invalid SVG XML: text outside root')
    else unescape(plain) end
    if not first then break end
    if text:sub(first, first + 3) == '<!--' then
      local ending = assert(text:find('-->', first + 4, true), 'Invalid SVG XML comment')
      assert(not text:sub(first + 4, ending - 1):find('--', 1, true), 'Invalid SVG XML comment')
      cursor = ending + 3
    elseif text:sub(first, first + 8) == '<![CDATA[' then
      assert(#stack > 0, 'Invalid SVG CDATA outside the root element')
      cursor = assert(text:find(']]>', first + 9, true), 'Invalid SVG CDATA') + 3
    elseif text:sub(first, first + 1) == '<?' then
      cursor = assert(text:find('?>', first + 2, true), 'Invalid SVG processing instruction') + 2
    elseif text:sub(first, first + 1) == '<!' then
      error('SVG XML declarations other than comments and CDATA are unsupported')
    else
      local ending = tag_end(text, first)
      local closing = text:sub(first + 1, first + 1) == '/'
      local beginning = first + (closing and 2 or 1)
      local name = assert(text:match('^([%a_:\128-\255][%w_:%.%-\128-\255]*)', beginning), 'Invalid SVG element name')
      if closing then
        assert(stack[#stack] and stack[#stack].name == name, 'Invalid SVG XML: mismatched closing tag')
        assert(text:sub(beginning + #name, ending - 1):match('^%s*$'), 'Invalid SVG closing tag')
        stack[#stack] = nil
      else
        if #stack == 0 then root_count = root_count + 1 end
        assert(root_count <= 1, 'Invalid SVG XML: more than one root')
        local parsed, namespaces = attributes(text, beginning + #name, ending), {}
        if stack[#stack] then
          for key, value in pairs(stack[#stack].namespaces) do namespaces[key] = value end
        end
        for _, attribute in ipairs(parsed) do
          local prefix = attribute.name:match('^xmlns:(.+)$')
          if prefix then namespaces[prefix] = attribute.value end
        end
        local prefix = name:match('^([^:]+):')
        assert(not prefix or namespaces[prefix], 'Invalid SVG XML: undeclared element namespace')
        for _, attribute in ipairs(parsed) do
          local attr_prefix = attribute.name:match('^([^:]+):')
          assert(not attr_prefix or attr_prefix == 'xmlns' or attr_prefix == 'xml' or namespaces[attr_prefix],
            'Invalid SVG XML: undeclared attribute namespace')
        end
        if name:match('([^:]+)$') == 'image' then
          images[#images + 1] = {attributes = parsed, namespaces = namespaces,
            insertion = text:sub(ending - 1, ending - 1) == '/' and ending - 1 or ending}
        end
        if text:sub(ending - 1, ending - 1) ~= '/' then
          stack[#stack + 1] = {name = name, namespaces = namespaces}
        end
      end
      cursor = ending + 1
    end
  end
  assert(#stack == 0 and root_count == 1, 'Invalid SVG XML: unclosed or missing root')
  return images
end

--- Decode only SVGZ through the dedicated decompressor; ordinary SVG stays in Lua
function M.read_svg(source)
  local bytes = assert(resource.read(source), 'Cannot read SVG: ' .. source)
  if source:lower():match('%.svgz$') then
    return pandoc.pipe(resource.svg_helper(), {'gunzip'}, bytes), bytes
  end
  return bytes, bytes
end

--- Select standard child-image MIME types without depending on a Python registry
local function mime_type(source)
  return mime_types[(source:match('%.([^./\\]+)$') or ''):lower()] or 'application/octet-stream'
end

--- Normalize both href spellings and preserve every unrelated source XML byte
function M.normalize(source, embed)
  local text, original = M.read_svg(source)
  local resources, edits, embeds_svg = {}, {}, false
  for _, image in ipairs(image_elements(text)) do
    local href, plain, linked
    for _, attribute in ipairs(image.attributes) do
      if attribute.name == 'href' then plain = attribute
      else
        local prefix = attribute.name:match('^([^:]+):href$')
        if prefix and image.namespaces[prefix] == xlink then linked = attribute end
      end
    end
    href = plain and plain.value ~= '' and plain.value or linked and linked.value
    local child_path = href and resource.local_path(href)
    if child_path then
      local candidate = pandoc.path.is_absolute(child_path) and child_path
        or pandoc.path.join {pandoc.path.directory(source), child_path}
      local child = resource.resolve(candidate, {})
      if child then
        local child_bytes = assert(resource.read(child), 'Cannot read SVG child: ' .. child)
        local entry = resource.fingerprint(child, child_bytes)
        entry.href = href
        local replacement
        if embed then
          entry.mime_type = mime_type(child)
          replacement = 'data:' .. entry.mime_type .. ';base64,' .. base64(child_bytes)
          embeds_svg = embeds_svg or resource.is_svg(child) or false
        else
          replacement = resource.pandoc_path(pandoc.path.make_relative(child, pandoc.path.directory(source)))
          entry.normalized_href = replacement
        end
        if embed or replacement ~= href then
          local escaped, additions = escape(replacement), ''
          if plain then edits[#edits + 1] = {plain.first, plain.last, escaped}
          else additions = ' href="' .. escaped .. '"' end
          if linked then edits[#edits + 1] = {linked.first, linked.last, escaped}
          else
            local prefix = 'xlink'
            while image.namespaces[prefix] and image.namespaces[prefix] ~= xlink do prefix = 'pmt' .. prefix end
            if image.namespaces[prefix] ~= xlink then additions = additions .. ' xmlns:' .. prefix .. '="' .. xlink .. '"' end
            additions = additions .. ' ' .. prefix .. ':href="' .. escaped .. '"'
          end
          if additions ~= '' then edits[#edits + 1] = {image.insertion, image.insertion - 1, additions} end
        end
        resources[#resources + 1] = entry
      elseif embed then resource.warn('SVG child image not found, leaving unchanged: ' .. href) end
    end
  end
  table.sort(edits, function(left, right) return left[1] > right[1] end)
  for _, edit in ipairs(edits) do text = text:sub(1, edit[1] - 1) .. edit[3] .. text:sub(edit[2] + 1) end
  return {text = text, original = original, resources = resources, embeds_svg = embeds_svg}
end

--- Build an order-independent content/options identity for the cache sidecar
function M.fingerprint(source, normalized, options)
  local parts = {source, pandoc.utils.sha1(normalized.original)}
  for _, entry in ipairs(normalized.resources) do
    parts[#parts + 1] = entry.href .. '\0' .. entry.path .. '\0' .. entry.sha1
  end
  for _, value in ipairs(options) do parts[#parts + 1] = tostring(value) end
  return pandoc.utils.sha1(table.concat(parts, '\0'))
end

return M
