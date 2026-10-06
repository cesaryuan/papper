-- Attach a separately authored numbered caption to the preceding bare image.
-- Word often stores a picture and its caption as two ordinary paragraphs rather
-- than a Pandoc Figure. This filter works on adjacent blocks (also inside lists,
-- quotes and table cells), then moves the caption's original inlines into Image
-- alt text without changing its source, dimensions, title or bookmark spans.
-- Recognized prefixes: 图1-11, 图 1‑11, Figure1-11, Figure 1–11 (case-insensitive
-- English). ASCII hyphens and common Unicode hyphen/dash variants are accepted.
-- Example input:
--   ![](media/image24.png){width="4.25in" height="4.40625in"}
--
--   [[]{#_Toc241697898 .anchor}]{#_Ref181174213 .anchor}图1‑11 识别结果
-- Output when used alone:
--   ![[[]{#_Toc241697898 .anchor}]{#_Ref181174213 .anchor}图1‑11 识别结果](media/image24.png){width="4.25in" height="4.40625in"}
-- remove_toc_anchors.lua removes the _Toc span in the full Convert pipeline.
-- Pictures that already have captions, multi-image paragraphs, intervening
-- prose and non-numbered paragraphs are preserved rather than guessed at.
-- Run after MathType recovery and TOC cleanup, before crossrefs.lua:
--   pandoc input.docx -f docx -t markdown -L pandoc/filters/convert/figure_captions.lua

--- Return the sole bare image in a paragraph, ignoring surrounding whitespace.
local function bare_image(block)
  if block.t ~= 'Para' and block.t ~= 'Plain' then return nil end
  local image = nil
  for _, inline in ipairs(block.content) do
    if inline.t == 'Image' and not image and #inline.caption == 0 then
      image = inline
    elseif inline.t ~= 'Space' and inline.t ~= 'SoftBreak' and inline.t ~= 'LineBreak' then
      return nil
    end
  end
  return image
end

--- Recognize chapter-figure numbering without changing the authored caption.
local function is_caption(block)
  if not block or (block.t ~= 'Para' and block.t ~= 'Plain') then return false end
  local text = pandoc.utils.stringify(block.content):lower()
  -- Word's nonbreaking hyphen is multibyte: byte-oriented Lua character classes
  -- cannot safely treat all Unicode dashes as one class, so normalize literals.
  for _, dash in ipairs({ '‐', '‑', '‒', '–', '—', '−', '﹣', '－' }) do
    text = text:gsub(dash, '-')
  end
  return text:match('^%s*图%s*%d+%-%d+') ~= nil
    or text:match('^%s*figure%s*%d+%-%d+') ~= nil
end

--- Combine only adjacent, unambiguous image/caption pairs in each block list.
function Blocks(blocks)
  local result, index = {}, 1
  while index <= #blocks do
    local image = bare_image(blocks[index])
    if image and is_caption(blocks[index + 1]) then
      image.caption = blocks[index + 1].content
      result[#result + 1] = pandoc.Para({ image })
      index = index + 2
    else
      result[#result + 1] = blocks[index]
      index = index + 1
    end
  end
  return result
end
