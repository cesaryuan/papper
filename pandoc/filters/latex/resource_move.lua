-- Copy LaTeX image/link/metadata resources into the output tree in-process
-- Resource URLs remain unchanged, so relative project layouts survive publication

local resource = dofile(pandoc.path.join {
  pandoc.path.directory(PANDOC_SCRIPT_FILE), '../shared/filter_resources.lua',
})
local target = resource.absolute(resource.getenv('PMT_LATEX_TARGET_DIR') or 'output/latex')

--- Copy one available resource, preserving relative layout and its original bytes
local function copy(raw)
  local bytes = resource.read(raw)
  if not bytes then
    resource.warn('Resource file not found, leaving unchanged: ' .. raw)
    return
  end
  local relative = pandoc.path.is_absolute(raw) and pandoc.path.filename(raw) or raw
  local output = resource.absolute(pandoc.path.join {target, relative})
  if output ~= resource.absolute(raw) then resource.atomic_write(output, bytes) end
end

--- Visit nested file-valued metadata without reserializing its representation
local function copy_metadata(value)
  local kind = pandoc.utils.type(value)
  if type(value) == 'string' or kind == 'Inlines' then
    copy(pandoc.utils.stringify(value))
  elseif kind == 'List' or kind == 'Meta' or kind == 'MetaMap' or type(value) == 'table' then
    for _, item in pairs(value) do copy_metadata(item) end
  end
end

--- Copy the same file-valued metadata keys used by the historical publication filter
function Meta(metadata)
  for _, key in ipairs({'bibliography', 'csl', 'reference-doc', 'reference-docx',
    'template', 'include-before-body', 'include-after-body', 'include-in-header',
    'css', 'data-dir', 'extract-media', 'resource-path'}) do
    if metadata[key] then copy_metadata(metadata[key]) end
  end
  return metadata
end

--- Copy image resources while preserving URL, attributes, caption and title
function Image(element)
  copy(element.src)
  return element
end

--- Copy file-like local links while leaving ordinary external URLs unchanged
function Link(element)
  if element.target:find('.', 1, true) and element.target:sub(1, 4) ~= 'http' then
    copy(element.target)
  end
  return element
end
