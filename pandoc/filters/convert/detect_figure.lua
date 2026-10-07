-- Detect Word pictures with separately authored numbered captions, including
-- tables and indentation used to position a picture. This filter combines:
--   1. A bare image paragraph followed immediately by a numbered caption.
--   2. A one-column, two-row table: one bare image, then its numbered caption.
--   3. A BlockQuote containing only a bare image, followed by a caption outside
--      the quote. Word's left paragraph indent can import as this structure.
--   4. A one-column, one-row image table with its numbered Table.caption.
-- Adjacent pairing also works inside lists, quotes and table cells. Table layouts
-- become standalone Figures; paired paragraphs become standalone Image paragraphs.
-- Caption inlines, formatting, bookmarks, image source, title and dimensions
-- are retained. All cases share the same numbered-caption recognition.
-- Recognized prefixes: 图1, 图 1‑11, Figure1, Figure 1–11, Fig. 1 and fig 1-11.
-- English prefixes are case-insensitive and allow an optional trailing period.
-- Single numbers and chapter-figure numbers are accepted, including common
-- Unicode hyphens/dashes. A dash without a following number is rejected.
-- Example input:
--   ![](media/image24.png){width="4.25in" height="4.40625in"}
--
--   [[]{#_Toc241697898 .anchor}]{#_Ref181174213 .anchor}图1‑11 识别结果
-- Output when used alone:
--   ![[[]{#_Toc241697898 .anchor}]{#_Ref181174213 .anchor}图1‑11 识别结果](media/image24.png){width="4.25in" height="4.40625in"}
-- Table example (horizontal rules in a Word export are table borders):
--   | ![](media/image762.png){width="4.9869in" height="2.7143in"} |
--   | []{#_Ref181215092 .anchor}图3‑17 车辆荷载示意图 |
-- becomes:
--   ![[]{#_Ref181215092 .anchor}图3‑17 车辆荷载示意图](media/image762.png){width="4.9869in" height="2.7143in"}
-- A single-row image table followed by ": 图4‑1 方法流程图" likewise becomes
-- a Figure: DOCX imports store this title in Table.caption, even when the
-- image row is promoted to Table.head. Wrapped attributes in Markdown output
-- do not represent separate cells in the original DOCX AST.
-- Indented-image example:
--   > ![](media/image224.png){width="4.09375in" height="2.231709317585302in"}
--
--   []{#_Ref202795466 .anchor}图2‑10 "智慧桥梁"车辆监测站点分布图
-- becomes:
--   ![[]{#_Ref202795466 .anchor}图2‑10 "智慧桥梁"车辆监测站点分布图](media/image224.png){width="4.09375in" height="2.231709317585302in"}
-- remove_toc_anchors.lua removes the _Toc span in the full Convert pipeline.
-- Also accepts bare Figures created by extract_inline_images.lua, allowing an
-- oversized inline picture moved out of prose to acquire its following caption.
-- crossrefs.lua then promotes a unique _Ref caption bookmark to an image
-- identifier such as #fig:_Ref181174213 and rewrites its inbound figure links.
-- Images already labeled fig: belong to established figures/subfigure groups;
-- their following overall caption must stay outside the child image caption.
-- Pictures that already have captions, multi-image paragraphs, intervening
-- prose and non-numbered paragraphs are preserved rather than guessed at.
-- Quotes containing prose or multiple blocks, or lacking a following caption,
-- keep their quote structure. Tables with non-figure captions, merged cells,
-- multiple columns or extra content keep their structure. A table caption
-- is used only for a single image row, never to override a second caption row.
-- Run after extract_inline_images.lua and TOC cleanup, before crossrefs.lua:
--   pandoc input.docx -f docx -t markdown -L pandoc/filters/convert/detect_figure.lua

local paired_count, table_count, indented_count = 0, 0, 0

--- Return the sole bare image in a paragraph, ignoring surrounding whitespace.
local function bare_image(block)
  if not block then return nil end
  if block.t == 'BlockQuote' then
    -- A lone quoted picture is Word's indentation workaround; unwrap it only
    -- when Blocks also confirms a numbered caption immediately after the quote.
    if #block.content ~= 1 then return nil end
    block = block.content[1]
  end
  if block.t == 'Figure' then
    if #block.caption.long > 0 or block.caption.short or #block.content ~= 1 then return nil end
    block = block.content[1]
  end
  if block.t ~= 'Para' and block.t ~= 'Plain' then return nil end
  local image = nil
  for _, inline in ipairs(block.content) do
    if inline.t == 'Image' and not image and #inline.caption == 0
      and not inline.identifier:match('^fig:') then
      image = inline
    elseif inline.t ~= 'Space' and inline.t ~= 'SoftBreak' and inline.t ~= 'LineBreak' then
      return nil
    end
  end
  return image
end

--- Return every physical table row in source order, including promoted headers.
local function rows(table_element)
  local result = {}
  for _, row in ipairs(table_element.head.rows) do result[#result + 1] = row end
  for _, body in ipairs(table_element.bodies) do
    for _, row in ipairs(body.head) do result[#result + 1] = row end
    for _, row in ipairs(body.body) do result[#result + 1] = row end
  end
  for _, row in ipairs(table_element.foot.rows) do result[#result + 1] = row end
  return result
end

--- Read a cell's sole paragraph while rejecting merged cells and structures.
local function cell_block(cell)
  if not cell or cell.row_span ~= 1 or cell.col_span ~= 1 or #cell.contents ~= 1 then return nil end
  local block = cell.contents[1]
  if block.t ~= 'Plain' and block.t ~= 'Para' then return nil end
  return block
end

--- Recognize single or chapter-figure numbers with Chinese/English prefixes.
local function is_caption(block)
  if not block or (block.t ~= 'Para' and block.t ~= 'Plain') then return false end
  local text = pandoc.utils.stringify(block.content):lower()
  -- Word's nonbreaking hyphen is multibyte: byte-oriented Lua character classes
  -- cannot safely treat all Unicode dashes as one class, so normalize literals.
  for _, dash in ipairs({ '‐', '‑', '‒', '–', '—', '−', '﹣', '－' }) do
    text = text:gsub(dash, '-')
  end
  local suffix = text:match('^%s*图%s*%d+(.*)$')
    or text:match('^%s*figure%.?%s*%d+(.*)$')
    or text:match('^%s*fig%.?%s*%d+(.*)$')
  if not suffix then return false end
  -- An optional chapter component must still have a number; a truncated
  -- "Fig. 2-" caption should not be mistaken for valid single-number "Fig. 2".
  if suffix:match('^%s*%-') then return suffix:match('^%s*%-%s*%d+') ~= nil end
  return true
end

--- Replace a confirmed one-picture/one-caption layout table with a Figure.
function Table(table_element)
  if #table_element.colspecs ~= 1 or table_element.caption.short then return nil end
  local table_rows = rows(table_element)
  local caption
  if #table_element.caption.long > 0 then
    -- Word's caption style can attach a figure title to the image table itself.
    -- Require one simple row/title so no data or competing caption is discarded.
    if #table_rows ~= 1 or #table_element.caption.long ~= 1 then return nil end
    caption = table_element.caption.long[1]
  else
    if #table_rows ~= 2 or #table_rows[2].cells ~= 1 then return nil end
    caption = cell_block(table_rows[2].cells[1])
  end
  if #table_rows[1].cells ~= 1 then return nil end
  local image = bare_image(cell_block(table_rows[1].cells[1]))
  if not image or not is_caption(caption) then return nil end
  image.caption = caption.content
  table_count = table_count + 1
  return pandoc.Figure({ pandoc.Plain({ image }) }, pandoc.Caption({ pandoc.Plain(caption.content) }))
end

--- Combine only adjacent, unambiguous image/caption pairs in each block list.
function Blocks(blocks)
  local result, index = {}, 1
  while index <= #blocks do
    local image = bare_image(blocks[index])
    if image and is_caption(blocks[index + 1]) then
      image.caption = blocks[index + 1].content
      result[#result + 1] = pandoc.Para({ image })
      paired_count = paired_count + 1
      if blocks[index].t == 'BlockQuote' then indented_count = indented_count + 1 end
      index = index + 2
    else
      result[#result + 1] = blocks[index]
      index = index + 1
    end
  end
  return result
end

--- Report recognized layouts once after all image and table transformations.
function Pandoc(doc)
  if paired_count + table_count > 0 then
    io.stderr:write('[detect-figure] paired ' .. paired_count .. ' image/caption pair(s), including '
      .. indented_count .. ' indented image(s); converted ' .. table_count .. ' layout table(s)\n')
  end
  return doc
end
