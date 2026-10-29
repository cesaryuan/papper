-- Preserve editable per-picture crops in DOCX, including notes and page stories.
-- For example, ![](photo.png){width="3in" crop-right="50"} keeps the original
-- photo embedded and shows its left half. Native finalization restores the
-- crop rectangle and adjusts inferred dimensions to the visible aspect ratio.

--- Validate percentage attributes and carry them through the DOCX image writer.
function Image(image)
  if FORMAT ~= 'docx' then return nil end
  local crop, present = {}, false
  for index, side in ipairs({'left', 'top', 'right', 'bottom'}) do
    local key = 'crop-' .. side
    local raw = image.attributes[key]
    present = present or raw ~= nil
    local value = 0
    if raw ~= nil then value = tonumber((raw:gsub('%%$', ''))) end
    if not value or value ~= value or math.abs(value) > 2147483 then
      error(key .. ' must be a finite percentage')
    end
    crop[index] = math.floor(value * 1000 + 0.5)
  end
  if not present then return nil end
  local x, y = 1 - (crop[1] + crop[3]) / 100000, 1 - (crop[2] + crop[4]) / 100000
  if x <= 0 or y <= 0 then error('Image crop must leave a positive visible width and height') end
  local width, height = image.attributes.width, image.attributes.height
  local scale = width and height and {1, 1} or width and {1, y / x}
    or height and {x / y, 1} or {x, y}
  image.title = 'PAPPER_IMAGE_CROP:' .. pandoc.json.encode({crop=crop, title=image.title, scale=scale})
  return image
end
