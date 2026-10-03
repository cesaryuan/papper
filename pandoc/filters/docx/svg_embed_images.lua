-- Embed local SVG child images in-process before the DOCX rasterization pass
-- Sources remain unchanged; content-aware sidecars reuse complete generated files
-- Nested SVG children request PNG fallback because Word cannot render SVG data URIs

local directory = pandoc.path.directory(PANDOC_SCRIPT_FILE)
local resource = dofile(pandoc.path.join {directory, '../shared/filter_resources.lua'})
local svg = dofile(pandoc.path.join {directory, '../shared/svg_resources.lua'})
local enabled = resource.boolean(resource.getenv('PMT_SVG_EMBED_IMAGES'))
local roots = resource.roots('PMT_SVG_EMBED_BASE_DIRS')
local output = resource.absolute(resource.getenv('PMT_SVG_EMBED_DIR') or 'tmp/svg-embedded')
local version = resource.getenv('PMT_SVG_EMBED_PMT_VERSION') or 'unknown'

--- Embed resolved child image bytes and invalidate the cache on any dependency edit
function Image(element)
  if not enabled then return nil end
  local local_path = resource.local_path(element.src)
  if not local_path or not resource.is_svg(local_path) then return nil end
  local source = resource.resolve(local_path, roots)
  if not source then
    resource.warn('SVG image not found, leaving unchanged: ' .. element.src)
    return nil
  end
  local normalized = svg.normalize(source, true)
  if #normalized.resources == 0 then return nil end
  local target = resource.cache_path(source, output, roots, 'svg')
  assert(target ~= source, 'SVG cache destination would overwrite its source: ' .. source)
  local fingerprint = svg.fingerprint(source, normalized, {'lua-svg-embed-v1', version})
  if not resource.cache_matches(target, fingerprint) then
    resource.atomic_write(target, normalized.text)
    resource.publish_metadata(target, fingerprint, {version = 1, source = resource.fingerprint(source, normalized.original),
      resources = normalized.resources, pmt_version = version, converter = 'lua-svg-embed-v1'})
  end
  element.src = resource.pandoc_path(target)
  if normalized.embeds_svg then element.attributes['to-png'] = 'true' end
  return element
end
