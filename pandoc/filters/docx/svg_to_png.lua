-- Rasterize requested SVG resources with Lua-owned resolution and cache decisions
-- Only a cache miss starts the small papper-svg renderer with one SVG byte stream
-- Pandoc's AST stays in-process; no JSON filter or complete-CLI alias is used

local directory = pandoc.path.directory(PANDOC_SCRIPT_FILE)
local resource = dofile(pandoc.path.join {directory, '../shared/filter_resources.lua'})
local svg = dofile(pandoc.path.join {directory, '../shared/svg_resources.lua'})
local roots = resource.roots('PMT_SVG_TO_PNG_BASE_DIRS')
local output = resource.absolute(resource.getenv('PMT_SVG_TO_PNG_DIR') or 'tmp/svg-png')
local dpi = resource.positive(resource.getenv('PMT_SVG_TO_PNG_DPI'), 300)
local default_scale = resource.positive(resource.getenv('PMT_SVG_TO_PNG_SCALE'), 1)
local explicit_width = resource.positive(resource.getenv('PMT_SVG_TO_PNG_WIDTH'))
if explicit_width and explicit_width % 1 ~= 0 then explicit_width = nil end
local convert_all = resource.boolean(resource.getenv('PMT_SVG_TO_PNG_CONVERT_ALL'))
local version = resource.getenv('PMT_SVG_TO_PNG_PMT_VERSION') or 'unknown'
local renderer_identity = resource.getenv('PAPPER_SVG_RENDERER_ID')
local request_keys = {'to-png', 'to_png', 'toPng'}
local scale_keys = {'to-png-scale', 'to_png_scale', 'toPngScale'}
local fonts

--- Fingerprint fonts once per conversion only for text or potentially text-bearing nested SVGs.
local function font_identity(text)
  if not text:find('<text[/%s>]') and not text:find('<[%w_.-]+:text[/%s>]')
    and not text:find('<image[/%s>]') and not text:find('<[%w_.-]+:image[/%s>]') then
    return ''
  end
  if fonts == nil then
    local ok, identity = pcall(pandoc.pipe, resource.svg_helper(), {'font-identity'}, '')
    if ok then
      fonts = identity:gsub('%s+$', '')
      if #fonts ~= 64 or fonts:find('[^%x]') then fonts = false end
    else fonts = false end
    if not fonts then resource.warn('Text image cache unavailable; rendering without reuse') end
  end
  return fonts
end

--- Find the first configured attribute alias while preserving historical precedence
local function attribute(element, names)
  for _, name in ipairs(names) do if element.attributes[name] then return element.attributes[name] end end
end

--- Apply Python's ties-to-even rounding for existing half-pixel dimension decisions
local function rounded(number)
  local integer = math.floor(number)
  local fraction = number - integer
  if fraction > 0.5 or (fraction == 0.5 and integer % 2 ~= 0) then integer = integer + 1 end
  return math.max(1, math.min(integer, 0xffffffff))
end

--- Derive width pixels using the existing percent, pixel and physical-unit policy
local function auto_width(raw)
  if not raw then return nil end
  local number, unit = raw:lower():match('^%s*(%d*%.?%d+)%s*(.-)%s*$')
  number = tonumber(number)
  if not number then return nil end
  -- Preserve operation order: combining physical factors can move a value
  -- across a half-pixel boundary and change Python's ties-to-even result
  local pixels
  if unit == '%' then pixels = number * 3000 / 100
  elseif unit == 'px' then pixels = number * 2
  elseif unit == 'cm' then pixels = number / 2.54 * 500
  elseif unit == 'mm' then pixels = number / 25.4 * 500
  elseif unit == 'in' or unit == 'inch' then pixels = number * 500 end
  return pixels and rounded(pixels) or nil
end

--- Resolve renderer identity once for standalone Lua invocations only
local function implementation()
  if not renderer_identity or renderer_identity == '' then
    renderer_identity = pandoc.pipe(resource.svg_helper(), {'--version'}, ''):gsub('%s+$', '')
  end
  return renderer_identity
end

--- Render only requested local SVGs and strip DOCX-only hints from every image
function Image(element)
  local requested = resource.boolean(attribute(element, request_keys))
  local override = resource.positive(attribute(element, scale_keys))
  local width = explicit_width or auto_width(element.attributes.width)
  local has_override = override ~= nil and width == nil
  if override and width then resource.warn('Ignoring to-png-scale because docxSvgToPngWidth is set') end
  local scale = has_override and override or default_scale
  for _, names in ipairs({request_keys, scale_keys}) do
    for _, name in ipairs(names) do element.attributes[name] = nil end
  end
  local local_path = resource.local_path(element.src)
  if not local_path or not resource.is_svg(local_path) or (not convert_all and not requested) then return element end
  local source = resource.resolve(local_path, roots)
  if not source then
    resource.warn('SVG image not found, leaving unchanged: ' .. element.src)
    return element
  end
  local target = resource.cache_path(source, output, roots, 'png')
  if has_override and scale ~= default_scale then
    local label = string.format('%.6g', scale):gsub('%.', 'p'):gsub('%-', 'm'):gsub('%+', '')
    target = target:gsub('%.png$', '.scale-' .. label .. '.png')
  end
  local normalized = svg.normalize(source, false)
  local font_id = font_identity(normalized.text)
  local fingerprint = svg.fingerprint(source, normalized,
    {'lua-svg-png-v2', implementation(), version, dpi, scale, width or 'intrinsic', font_id or ''})
  if font_id == false or not resource.cache_matches(target, fingerprint) then
    local arguments = {'render', '--source', source, '--dpi', tostring(dpi), '--scale', tostring(scale)}
    if width then arguments[#arguments + 1], arguments[#arguments + 2] = '--width', tostring(width) end
    local pixels = pandoc.pipe(resource.svg_helper(), arguments, normalized.text)
    resource.atomic_write(target, pixels)
    resource.publish_metadata(target, fingerprint, {version = 1, source = resource.fingerprint(source, normalized.original),
      resources = normalized.resources, dpi = dpi, scale = scale, width = width or pandoc.json.null,
      pmt_version = version, converter = 'lua-svg-png-v2', renderer = renderer_identity})
  end
  element.src = resource.pandoc_path(target)
  return element
end
