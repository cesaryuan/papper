-- Recover editable crops after docx_layout.lua rereads the prepared package.
-- For example, a Word right crop of 52.014% becomes crop-right="52.014" on
-- the original image. Restore the authored title before caption/subfigure recovery.

local marker = 'PAPPER_IMAGE_CROP:'

--- Decode per-picture metadata instead of matching by a shared source filename.
function Image(image)
  if image.title:sub(1, #marker) ~= marker then return nil end
  local record = pandoc.json.decode(image.title:sub(#marker + 1))
  for index, side in ipairs({'left', 'top', 'right', 'bottom'}) do
    if record.crop[index] ~= 0 then
      image.attributes['crop-' .. side] = string.format('%.3f', record.crop[index] / 1000)
        :gsub('0+$', ''):gsub('%.$', '')
    end
  end
  image.title = record.title
  return image
end
