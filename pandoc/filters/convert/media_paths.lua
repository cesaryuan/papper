-- Preserve existing imported pictures when multiple DOCX files share a media directory.
-- Run last in Convert, after equation preview cleanup and caption recovery. A conflicting
-- media/image1.png becomes media/image1-<content-hash>.png, with every Image reference
-- updated before Pandoc writes Markdown and extracts the media bag. Identical files reuse
-- their names; standalone invocations without PAPPER_CONVERT_OUTPUT_DIR remain unchanged.

local resource = dofile(pandoc.path.join {
  pandoc.path.directory(PANDOC_SCRIPT_FILE), '../shared/filter_resources.lua',
})
local destination = resource.getenv('PAPPER_CONVERT_OUTPUT_DIR')

--- Rename conflicting media bag entries once, retaining image captions and attributes.
function Pandoc(document)
  if not destination then return nil end
  local renamed = {}
  document = document:walk({ Image = function(image)
    local source = image.src:gsub('\\', '/'):gsub('^%./', '')
    if not source:match('^media/') then return nil end
    if renamed[source] then
      image.src = renamed[source]
      return image
    end
    local mime, bytes = pandoc.mediabag.lookup(source)
    if not bytes then return nil end
    local existing = resource.read(pandoc.path.join {destination, source})
    if not existing or existing == bytes then return nil end
    local stem, extension = pandoc.path.split_extension(source)
    local base = stem .. '-' .. pandoc.utils.sha1(bytes)
    local candidate, suffix = base .. extension, 1
    while true do
      local disk = resource.read(pandoc.path.join {destination, candidate})
      local _, bag = pandoc.mediabag.lookup(candidate)
      if (not disk or disk == bytes) and (not bag or bag == bytes) then break end
      -- A user may have edited a prior hash-named image; preserve that file too.
      candidate = base .. '-' .. suffix .. extension
      suffix = suffix + 1
    end
    pandoc.mediabag.insert(candidate, mime, bytes)
    renamed[source] = candidate
    image.src = candidate
    resource.warn('Preserved existing media; imported ' .. source .. ' as ' .. candidate)
    return image
  end })
  for source in pairs(renamed) do pandoc.mediabag.delete(source) end
  return document
end
