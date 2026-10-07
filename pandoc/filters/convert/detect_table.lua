-- Attach a separately authored numbered caption to the immediately following
-- captionless Table. Word authors often put a table title in an ordinary
-- paragraph above the table; this filter moves that paragraph into Table.caption.
-- Recognized prefixes: 表2, 表2-1, 表 2‑1, Table2, Table 2-1, Tbl. 2 and tbl 2-1.
-- English prefixes are case-insensitive and allow an optional trailing period.
-- Common Unicode hyphens/dashes are accepted; chapter numbering is
-- optional. The original caption inlines, formatting, links and bookmark spans
-- are preserved, as are table attributes, widths, alignments and merged cells.
-- Example input:
--   []{#_Ref202795830 .anchor}表2‑1 武汉市车辆类型组成
--
--   | 车型组成 | 车型占比/% |
--   |----------|-----------|
--   | 两轴车   | 83.51     |
-- Output when written as Markdown:
--   | 车型组成 | 车型占比/% |
--   |----------|-----------|
--   | 两轴车   | 83.51     |
--
--   : []{#_Ref202795830 .anchor}表2‑1 武汉市车辆类型组成
-- Both Para and Plain captions are supported, including pairs inside lists,
-- quotes and table cells. Already captioned tables, headings, non-numbered
-- titles, titles below tables and pairs separated by another block are retained.
-- A dash without a following number is not accepted as a table number.
-- Run after detect_figure.lua (which removes picture layout tables) and TOC
-- cleanup, before crossrefs.lua:
--   pandoc input.docx -f docx -t markdown -L pandoc/filters/convert/detect_table.lua

local paired_count = 0

--- Recognize numbered table titles without changing their original inlines.
local function is_caption(block)
  if block.t ~= 'Para' and block.t ~= 'Plain' then return false end
  local text = pandoc.utils.stringify(block.content):lower()
  -- Word's nonbreaking hyphen is multibyte; normalize literal dashes instead
  -- of using a byte-oriented Lua character class that can corrupt Unicode.
  for _, dash in ipairs({ '‐', '‑', '‒', '–', '—', '−', '﹣', '－' }) do
    text = text:gsub(dash, '-')
  end
  local suffix = text:match('^%s*表%s*%d+(.*)$')
    or text:match('^%s*table%.?%s*%d+(.*)$')
    or text:match('^%s*tbl%.?%s*%d+(.*)$')
  if not suffix then return false end
  if suffix:match('^%s*%-') then return suffix:match('^%s*%-%s*%d+') ~= nil end
  return true
end

--- Move only adjacent numbered titles into tables that have no caption.
function Blocks(blocks)
  local result, index = {}, 1
  while index <= #blocks do
    local block, next_block = blocks[index], blocks[index + 1]
    if is_caption(block) and next_block and next_block.t == 'Table'
      and #next_block.caption.long == 0 and not next_block.caption.short then
      next_block.caption = pandoc.Caption({ pandoc.Plain(block.content) })
      result[#result + 1] = next_block
      paired_count = paired_count + 1
      index = index + 2
    else
      result[#result + 1] = block
      index = index + 1
    end
  end
  return result
end

--- Report paired titles once after all block lists have been processed.
function Pandoc(doc)
  if paired_count > 0 then
    io.stderr:write('[detect-table] paired ' .. paired_count .. ' table caption(s)\n')
  end
  return doc
end
