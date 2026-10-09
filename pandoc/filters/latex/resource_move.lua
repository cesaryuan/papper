-- Copy LaTeX image/link/metadata resources into the output tree in-process
-- Resolve against the manuscript's effective resource roots and publish beside the
-- actual .tex output. Absolute/outside-root inputs become portable resource URLs.
-- Example: -o submission/paper.tex copies figures/plot.png into submission/figures/.

local resource = dofile(pandoc.path.join {
  pandoc.path.directory(PANDOC_SCRIPT_FILE), '../shared/filter_resources.lua',
})
local target = resource.absolute(resource.getenv('PMT_LATEX_TARGET_DIR') or 'output/latex')
local roots = resource.roots('PMT_LATEX_RESOURCE_PATH')

--- Copy one available resource, preserving relative layout and its original bytes
local function copy(raw)
  local local_path = resource.local_path(raw)
  if not local_path then return nil end
  local source = resource.resolve(local_path, roots)
  local bytes = source and resource.read(source)
  if not bytes then
    resource.warn('Resource file not found, leaving unchanged: ' .. raw)
    return
  end
  local relative = pandoc.path.normalize(local_path)
  if pandoc.path.is_absolute(relative) or relative == '..' or relative:match('^%.%.[/\\]') then
    -- Keep external resources inside the output tree, disambiguating equal basenames.
    relative = pandoc.path.join {'resources', pandoc.utils.sha1(source):sub(1, 16),
      pandoc.path.filename(source)}
  end
  local output = resource.absolute(pandoc.path.join {target, relative})
  if output ~= source then resource.atomic_write(output, bytes) end
  return resource.pandoc_path(relative) .. (raw:match('[?#].*$') or '')
end

--- Visit nested file-valued metadata without reserializing its representation
local function copy_metadata(value)
  local kind = pandoc.utils.type(value)
  if type(value) == 'string' or kind == 'Inlines' then
    local relative = copy(pandoc.utils.stringify(value))
    if relative then return pandoc.MetaString(relative) end
  elseif kind == 'List' or kind == 'Meta' or kind == 'MetaMap' or type(value) == 'table' then
    for key, item in pairs(value) do value[key] = copy_metadata(item) end
  end
  return value
end

--- Copy the same file-valued metadata keys used by the historical publication filter
function Meta(metadata)
  for _, key in ipairs({'bibliography', 'csl', 'reference-doc', 'reference-docx',
    'template', 'include-before-body', 'include-after-body', 'include-in-header',
    'css', 'data-dir', 'extract-media', 'resource-path'}) do
    if metadata[key] then metadata[key] = copy_metadata(metadata[key]) end
  end
  return metadata
end

--- Copy image resources while preserving URL, attributes, caption and title
function Image(element)
  element.src = copy(element.src) or element.src
  return element
end

--- Copy file-like local links while leaving ordinary external URLs unchanged
function Link(element)
  if element.target:find('.', 1, true) and element.target:sub(1, 4) ~= 'http' then
    element.target = copy(element.target) or element.target
  end
  return element
end
