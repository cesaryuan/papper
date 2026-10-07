-- Repair oversized pictures embedded in Word body-text paragraphs.
-- A picture wider than 2 inches inside a Para/Plain is treated as an incorrectly
-- wrapped Word illustration. Remove it from the paragraph's inline content and
-- insert a separate Pandoc Figure immediately after that paragraph. Multiple
-- qualifying pictures keep their original order; text before/after each picture
-- stays together, and image source, dimensions, alt text and attributes survive.
-- Example input:
--   对桥隧基本信息进行预![](media/image16.png){width="5.78125in" height="3.120138888888889in"}处理，建立区域
-- Markdown output:
--   对桥隧基本信息进行预处理，建立区域
--
--   ![](media/image16.png){width="5.78125in" height="3.120138888888889in"}
-- The inserted node is a Figure even when its caption is empty. Absolute widths
-- in in/inch, cm, mm, pt, pc and px (96 px/in) are supported. Exactly 2in, smaller
-- pictures, missing/invalid/relative widths and already standalone images stay
-- untouched. Inline formatting, list/quote/cell context and footnotes survive;
-- images within a note are processed in that note, never pulled into body text.
-- Run after MathType recovery/TOC cleanup, before detect_figure.lua and
-- crossrefs.lua. The former can attach a following numbered caption to the new
-- bare Figure; the latter can resolve its figure bookmark and inbound links.
--   pandoc input.docx -f docx -t markdown -L pandoc/filters/convert/extract_inline_images.lua

local units_per_inch = { ['in'] = 1, inch = 1, cm = 2.54, mm = 25.4, pt = 72, pc = 6, px = 96 }
local moved_count = 0

--- Convert an authored absolute width to inches, leaving unknown sizes unclassified.
local function width_inches(image)
  local raw = image.attributes.width
  if not raw then return nil end
  local number, unit = raw:lower():match('^%s*([+%-]?%d*%.?%d+)%s*([%a]+)%s*$')
  local scale = unit and units_per_inch[unit]
  return scale and tonumber(number) / scale or nil
end

--- Determine whether images share a paragraph with prose or with other images.
local function has_inline_context(block)
  local image_count = 0
  local context = block:walk({
    traverse = 'topdown',
    -- Alt text is not body prose and must not make a sole image appear inline.
    Image = function(image)
      image_count = image_count + 1
      return {}, false
    end,
    -- Notes have independent block lists and must not affect the image count.
    Note = function(note) return note, false end,
  })
  return image_count > 1 or pandoc.utils.stringify(context):match('%S') ~= nil
end

--- Remove empty formatting shells left by extracting their only image.
local function empty_formatting(inline)
  if #inline.content == 0 then return {} end
  return nil
end

--- Extract qualifying pictures while preserving the remaining paragraph and local context.
local function extract_images(block)
  if (block.t ~= 'Para' and block.t ~= 'Plain') or not has_inline_context(block) then return nil end
  local figures = {}
  local paragraph = block:walk({
    traverse = 'topdown',
    -- Match outer images only; an alt-text image is not a separate body picture.
    Image = function(image)
      local width = width_inches(image)
      if width and width > 2 then
        local caption = #image.caption > 0 and { pandoc.Plain(image.caption) } or {}
        figures[#figures + 1] = pandoc.Figure({ pandoc.Plain({ image }) }, pandoc.Caption(caption))
        return {}, false
      end
      return image, false
    end,
    -- A note's images were already handled in its own Blocks pass.
    Note = function(note) return note, false end,
  })
  if #figures == 0 then return nil end
  paragraph = paragraph:walk({
    Emph = empty_formatting, Strong = empty_formatting, Underline = empty_formatting,
    Strikeout = empty_formatting, Superscript = empty_formatting,
    Subscript = empty_formatting, SmallCaps = empty_formatting,
  })
  local result = { paragraph }
  for _, figure in ipairs(figures) do result[#result + 1] = figure end
  moved_count = moved_count + #figures
  return result
end

--- Insert extracted figures after their paragraph within each original block list.
function Blocks(blocks)
  local result = {}
  for _, block in ipairs(blocks) do
    local replacement = extract_images(block)
    if replacement then
      for _, item in ipairs(replacement) do result[#result + 1] = item end
    else
      result[#result + 1] = block
    end
  end
  return result
end

--- Report repaired pictures once, after all nested block lists have been processed.
function Pandoc(doc)
  if moved_count > 0 then
    io.stderr:write('[extract-inline-images] moved ' .. moved_count .. ' image(s) wider than 2in\n')
  end
  return doc
end
