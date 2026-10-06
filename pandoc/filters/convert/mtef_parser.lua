-- Recover MathType equations while Pandoc imports a DOCX document.
-- Pandoc's DOCX reader keeps an OLE object's preview image in the AST but
-- discards its MTEF payload. This native Lua filter reads the source DOCX with
-- pandoc.zip, matches each preview image to its embedded OLE relationship,
-- also recovers MTEF-bearing WMF pictures without OLE, and replaces Image with Math.
-- Run with: pandoc input.docx -f docx -t markdown -L pandoc/filters/convert/mtef_parser.lua
-- Install the native Papper CLI on PATH before using this standalone filter.
-- papper convert supplies a predecoded map; standalone Pandoc starts one native
-- batch helper (PAPPER_EXECUTABLE overrides its path) using the same linked decoder.

--- Return the local part of an XML name, independent of namespace prefix.
local function local_name(name)
  return name:match('([^:]+)$')
end

--- Decode XML entities used in OOXML relationship targets and attributes.
local function xml_unescape(value)
  value = value:gsub('&#x([%da-fA-F]+);', function(hex)
    return utf8.char(tonumber(hex, 16))
  end)
  value = value:gsub('&#(%d+);', function(decimal)
    return utf8.char(tonumber(decimal))
  end)
  return value:gsub('&quot;', '"'):gsub('&apos;', "'"):gsub('&lt;', '<'):gsub('&gt;', '>'):gsub('&amp;', '&')
end

--- Read quoted attributes from one XML tag without depending on prefix order.
local function attributes(tag)
  local result = {}
  for name, _, value in tag:gmatch('([%w_:%.-]+)%s*=%s*(["\'])(.-)%2') do
    result[name] = xml_unescape(value)
  end
  return result
end

--- Look up a namespaced XML attribute by its local name.
local function attribute_by_local_name(attrs, wanted)
  for name, value in pairs(attrs) do
    if local_name(name) == wanted then
      return value
    end
  end
  return nil
end

--- Resolve one internal OOXML relationship path against its source part.
local function relationship_target(part, target)
  local path = target:sub(1, 1) == '/' and target:sub(2) or (part:match('^(.*)/') .. '/' .. target)
  local pieces = {}
  for component in path:gmatch('[^/]+') do
    if component == '..' then
      table.remove(pieces)
    elseif component ~= '.' then
      pieces[#pieces + 1] = component
    end
  end
  return table.concat(pieces, '/')
end

--- Read the relationships belonging to one DOCX XML part.
local function relationships(part, entries)
  local folder, filename = part:match('^(.*)/([^/]+)$')
  local rels = entries[folder .. '/_rels/' .. filename .. '.rels']
  if not rels then
    return {}
  end
  local result = {}
  for tag in rels:contents():gmatch('<[^>]+>') do
    local name = tag:match('^<%s*([%w_:%.-]+)')
    if name and local_name(name) == 'Relationship' then
      local attrs = attributes(tag)
      if attrs.Id and attrs.Target and attrs.TargetMode ~= 'External' then
        result[attrs.Id] = {
          kind = attrs.Type or '',
          target = relationship_target(part, attrs.Target),
        }
      end
    end
  end
  return result
end

--- Match a Pandoc image name to the DOCX media part carrying its preview.
local function image_source(source)
  local normalized = source:gsub('\\', '/'):gsub('^%./', '')
  local media = normalized:match('.*/(media/.+)$')
  return media or normalized:gsub('^word/', '')
end

--- Collect preview/OLE pairs in one XML part, keeping ambiguous images intact.
local function scan_part(part, entries, previews, ambiguous, blocked)
  local rels = relationships(part, entries)
  local current = nil
  for tag in entries[part]:contents():gmatch('<[^>]+>') do
    local closing = tag:match('^<%s*/') ~= nil
    local name = tag:match('^<%s*/?%s*([%w_:%.-]+)')
    local kind = name and local_name(name)
    if kind == 'object' and not closing then
      current = { images = {} }
    elseif current and kind == 'OLEObject' and not closing then
      local attrs = attributes(tag)
      local prog_id = attrs.ProgID or ''
      if prog_id:match('^Equation[%.]') or prog_id:find('MathType', 1, true) then
        current.ole_id = attribute_by_local_name(attrs, 'id')
      else
        current.other_ole = true
      end
    elseif current and (kind == 'imagedata' or kind == 'blip') and not closing then
      local attrs = attributes(tag)
      local id = attribute_by_local_name(attrs, kind == 'blip' and 'embed' or 'id')
      if id then
        current.images[#current.images + 1] = id
      end
    elseif not current and (kind == 'imagedata' or kind == 'blip') and not closing then
      local attrs = attributes(tag)
      local id = attribute_by_local_name(attrs, kind == 'blip' and 'embed' or 'id')
      local image_rel = rels[id]
      if image_rel and image_rel.kind:match('/image$') then
        -- A normal picture reusing a formula preview cannot be distinguished by
        -- its Pandoc Image source, so retain every use of that media part.
        ambiguous[image_source(image_rel.target)] = true
      end
    elseif current and kind == 'object' and closing then
      local ole_rel = rels[current.ole_id]
      local ole_entry = ole_rel and ole_rel.kind:match('/oleObject$') and entries[ole_rel.target]
      if ole_entry then
        local ole = ole_entry:contents()
        for _, id in ipairs(current.images) do
          local image_rel = rels[id]
          if image_rel and image_rel.kind:match('/image$') then
            local source = image_source(image_rel.target)
            if previews[source] and previews[source] ~= ole then
              ambiguous[source] = true
            else
              previews[source] = ole
            end
          end
        end
      else
        for _, id in ipairs(current.images) do
          local image_rel = rels[id]
          if image_rel and image_rel.kind:match('/image$') then
            local source = image_source(image_rel.target)
            if current.other_ole then
              blocked[source] = true
            end
          end
        end
      end
      current = nil
    end
  end
end

--- Read each source DOCX using Pandoc's built-in ZIP support.
local function collect_previews()
  local previews = {}
  local wmfs = {}
  local ambiguous = {}
  local blocked = {}
  for _, input in ipairs(PANDOC_STATE.input_files) do
    if input:lower():match('%.docx$') then
      local archive = pandoc.zip.Archive(pandoc.system.read_file(input, true))
      local entries = {}
      for _, entry in ipairs(archive.entries) do
        entries[entry.path] = entry
        if entry.path:lower():match('^word/media/.+%.wmf$') then
          wmfs[image_source(entry.path)] = entry:contents()
        end
      end
      for part in pairs(entries) do
        if part:match('^word/[^/]+%.xml$') then
          scan_part(part, entries, previews, ambiguous, blocked)
        end
      end
    end
  end
  for source in pairs(ambiguous) do
    if previews[source] then
      previews[source] = nil
      wmfs[source] = nil
      io.stderr:write('[mtef-parser] ambiguous preview ', source, '; keeping image\n')
    end
  end
  -- A standalone WMF needs no OLE association. Explicitly unrelated attachments
  -- and ambiguous OLE previews must not be reclassified via their picture data.
  for source in pairs(blocked) do
    previews[source] = nil
    wmfs[source] = nil
  end
  return previews, wmfs
end

--- Read in-process conversion results or decode documents in one native batch call.
local function read_latex_map()
  -- Lua's C getenv uses ANSI on Windows; native map and executable paths can be Unicode.
  local environment = pandoc.system.environment()
  local mapping = environment.MATHTYPE_LATEX_MAP
  if mapping and mapping ~= '' then
    return pandoc.json.decode(pandoc.system.read_file(mapping), false)
  end
  local args = { '__decode_docx' }
  for _, input in ipairs(PANDOC_STATE.input_files) do
    if input:lower():match('%.docx$') then
      args[#args + 1] = input
    end
  end
  local executable = environment.PAPPER_EXECUTABLE or 'papper'
  return pandoc.json.decode(pandoc.pipe(executable, args, ''), false)
end

--- Remove one outer TeX math delimiter pair before constructing a Pandoc Math.
local function math_body(latex)
  local value = latex:match('^%s*(.-)%s*$')
  for _, pair in ipairs({ { '\\[', '\\]' }, { '\\(', '\\)' }, { '$$', '$$' }, { '$', '$' } }) do
    local left, right = pair[1], pair[2]
    if value:sub(1, #left) == left and value:sub(-#right) == right and #value >= #left + #right then
      return value:sub(#left + 1, -#right - 1):match('^%s*(.-)%s*$')
    end
  end
  return value
end

--- Look up decoded OLE/WMF bytes by content hash and strip outer math delimiters.
local function decode_payload(payload, decoded, cache)
  if cache[payload] ~= nil then
    return cache[payload] or nil
  end
  local latex = decoded[pandoc.utils.sha1(payload)]
  if type(latex) == 'string' then
    local body = math_body(latex)
    if body ~= '' then
      cache[payload] = body
      return body
    end
  end
  cache[payload] = false
  return nil
end

--- Return true when an image is the only meaningful inline in its paragraph.
local function sole_image(inlines)
  local image = nil
  for _, inline in ipairs(inlines) do
    if inline.t == 'Image' then
      if image then
        return nil
      end
      image = inline
    elseif inline.t ~= 'Space' and inline.t ~= 'SoftBreak' and inline.t ~= 'LineBreak' then
      return nil
    end
  end
  return image
end

--- Convert display equations first, then remaining inline MathType previews.
function Pandoc(doc)
  local previews, wmfs = collect_previews()
  if next(previews) == nil and next(wmfs) == nil then
    return doc
  end
  local decoded = read_latex_map()
  local cache = {}
  local count = 0
  local converted_sources = {}
  -- Prefer editable OLE data, then use the WMF's own validated MTEF comments.
  local function replace_image(image, style)
    local source = image_source(image.src)
    local ole = previews[source]
    local wmf = wmfs[source]
    if not ole and not wmf then
      return nil
    end
    local body = ole and decode_payload(ole, decoded, cache)
    if not body and wmf then
      body = decode_payload(wmf, decoded, cache)
    end
    if not body then
      -- Ordinary WMF pictures have no decoded entry and should stay silent.
      if ole or (wmf and decoded[pandoc.utils.sha1(wmf)] ~= nil) then
        io.stderr:write('[mtef-parser] MathType decode failed; keeping preview image\n')
      end
      return nil
    end
    count = count + 1
    converted_sources[source] = true
    return pandoc.Math(style, body)
  end
  doc = doc:walk({
    Para = function(para)
      local image = sole_image(para.content)
      if image then
        local math = replace_image(image, 'DisplayMath')
        if math then
          para.content = { math }
          return para
        end
      end
      return nil
    end,
    Plain = function(plain)
      local image = sole_image(plain.content)
      if image then
        local math = replace_image(image, 'DisplayMath')
        if math then
          plain.content = { math }
          return plain
        end
      end
      return nil
    end,
  })
  doc = doc:walk({ Image = function(image)
    return replace_image(image, 'InlineMath')
  end })
  -- Pandoc extracts every media bag item, even when its Image was replaced.
  -- Keep a shared preview if any Image still references it after conversion.
  local retained_sources = {}
  doc:walk({ Image = function(image)
    retained_sources[image_source(image.src)] = true
  end })
  for source in pairs(converted_sources) do
    if not retained_sources[source] then
      pandoc.mediabag.delete(source)
    end
  end
  io.stderr:write('[mtef-parser] converted ', tostring(count), ' MathType equation(s)\n')
  return doc
end
