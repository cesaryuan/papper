-- Read the shared manuscript attribute catalog used by native filters and the editor export.
-- Callers supply the resource path so loading works both in checkouts and unpacked releases.

local attributes = {}

--- Load the authored catalog and fail explicitly if packaged resources are incomplete
function attributes.load(path)
  local file, message = io.open(path, 'rb')
  if not file then error('Cannot read Papper element attributes: ' .. tostring(message)) end
  local source = file:read('*a')
  file:close()
  return pandoc.json.decode(source)
end

--- Resolve canonical discriminator values and their documented aliases
function attributes.variant(element, kind)
  if element.variants[kind] then return element.variants[kind] end
  for _, variant in pairs(element.variants) do
    for _, alias in ipairs(variant.aliases or {}) do
      if alias == kind then return variant end
    end
  end
end

--- Collect the canonical and alias keys transported to native table postprocessing
function attributes.table_metadata_keys(element)
  local keys = {}
  for name, property in pairs(element.properties) do
    if property.docxMetadata then
      keys[name] = true
      for _, alias in ipairs(property.aliases or {}) do keys[alias] = true end
    end
  end
  return keys
end

return attributes
