-- Round image width/height to five significant digits before Markdown export.
-- Include original-width/original-height saved by subfigure detection, which
-- replaces layout widths with percentages and removes layout heights.
-- Parse the numeric component separately so units (including %) are preserved.
-- Use general-format rounding, then expand scientific notation to plain decimal
-- because downstream dimension readers may not accept exponents. Missing or
-- invalid sizes remain untouched; shorter numbers are not padded with zeros.
-- Run after image extraction/detection so rounding cannot change size thresholds:
--   pandoc input.docx -t markdown -L pandoc/filters/convert/round_image_dimensions.lua

local rounded_count = 0

--- Expand a formatted exponent without introducing a second floating-point rounding.
local function plain_decimal(value)
  local mantissa, exponent = value:match('^(.-)[eE]([+%-]?%d+)$')
  if not mantissa then return value end
  local sign = ''
  if mantissa:sub(1, 1) == '-' then
    sign, mantissa = '-', mantissa:sub(2)
  end
  local point = (mantissa:find('%.') or (#mantissa + 1)) - 1 + tonumber(exponent)
  local digits = mantissa:gsub('%.', '')
  if point <= 0 then return sign .. '0.' .. string.rep('0', -point) .. digits end
  if point >= #digits then return sign .. digits .. string.rep('0', point - #digits) end
  return sign .. digits:sub(1, point) .. '.' .. digits:sub(point + 1)
end

--- Round a finite decimal dimension, preserving its original unit or percentage suffix.
local function round_dimension(raw)
  if not raw then return nil end
  local numeric, unit = raw:match('^%s*([+%-]?[%d%.]+[eE][+%-]?%d+)%s*([%a%%]*)%s*$')
  if not numeric then
    numeric, unit = raw:match('^%s*([+%-]?[%d%.]+)%s*([%a%%]*)%s*$')
  end
  local number = numeric and tonumber(numeric)
  -- Invalid input and exponent overflow must not become nan/inf dimensions.
  if not number or number ~= number or math.abs(number) == math.huge then return nil end
  return plain_decimal(string.format('%.5g', number)) .. unit
end

--- Normalize layout and retained source dimensions, including nested subfigures.
function Image(image)
  local changed = false
  -- Subfigure source sizes no longer live in width/height after detection.
  for _, attribute in ipairs({ 'width', 'height', 'original-width', 'original-height' }) do
    local raw = image.attributes[attribute]
    local rounded = round_dimension(raw)
    if rounded and rounded ~= raw then
      image.attributes[attribute] = rounded
      changed = true
    end
  end
  if not changed then return nil end
  rounded_count = rounded_count + 1
  return image
end

--- Report changed images once per document rather than logging individual attributes.
function Pandoc(doc)
  if rounded_count > 0 then
    io.stderr:write('[round-image-dimensions] normalized ', tostring(rounded_count), ' image(s) to 5 significant digits\n')
  end
  return doc
end
