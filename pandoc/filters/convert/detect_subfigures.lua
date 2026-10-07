-- Recover Word subfigures laid out with tables or successive image paragraphs.
-- Run after equation/TOC cleanup, BEFORE extract_inline_images.lua, detect_figure.lua
-- and crossrefs.lua: those filters otherwise separate pictures from their cells.
-- Requires at least two images and one unambiguous numbered overall caption
-- (图1, 图1‑13, Figure 1-13, Fig. 1; English is case-insensitive).
-- The caption may be the table caption, a full-width cell, or the next paragraph.
-- Supported layouts include:
--   | image A | image B |       | image A |   | (a) caption + image A |
--   | (a) A   | (b) B   |       | (a) A   |   | image B + (b) caption |
--   | image C | image D |       | image B |   | 图1‑13 Overall caption |
--   | (c) C   | (d) D   |       | (b) B   |
-- Separate caption rows, captions before/after images, merged full-width cells,
-- full-width （a） markers, ordered-list (a) markers and captionless panels work.
-- Pipes stranded inside cells by a previous Markdown export are treated as
-- separators; this cannot reconstruct image syntax already damaged by that export.
-- Standalone image/(a) caption/image/(b) caption/overall caption sequences work too.
-- Tables containing several numbered figure captions or conflicting overall
-- captions are preserved: they may contain independent figures, not subfigures.
-- Data tables, nested tables, row-spanning cells and uncertain caption assignments
-- are preserved. This conservative boundary avoids discarding authored content.
-- Output is pandoc-crossref's Div syntax, with each image row in one paragraph:
--   ::: {#fig:_Ref123}
--   ![A](a.png){#fig:_Ref123-a width="50%" label="a"}
--   ![B](b.png){#fig:_Ref123-b width="50%" label="b"}
--
--   图1‑13 Overall caption
--   :::
-- A real row is written as ONE paragraph (the two images above share a line).
-- subfigGrid: true is added to metadata; use standalone Markdown to export it.
-- Grid widths must be percentages. Original dimensions are retained in
-- original-width/original-height attributes; height is unset to retain aspect ratio.
-- Different row lengths become vertical rows in source order: pandoc-crossref
-- sizes its grid from the first row and can silently drop later extra columns.
-- Existing bookmarks become parent/child fig: IDs; otherwise deterministic IDs
-- derive from the overall figure number. ID collisions get a numeric suffix.
-- crossrefs.lua resolves Word links; crossrefs_fuzz.lua can resolve typed references.

local used, remapped = {}, {}
local groups, vertical, conflicts = 0, 0, 0

--- Normalize Word's Unicode dashes before recognizing numbered captions.
local function normalized(text)
  for _, dash in ipairs({ '‐', '‑', '‒', '–', '—', '−', '﹣', '－' }) do
    text = text:gsub(dash, '-')
  end
  return text:lower()
end

--- Return the caption number, rejecting incomplete chapter-number suffixes.
local function caption_number(inlines)
  local reference = false
  pandoc.Span(inlines):walk({ Link = function(link)
    if link.target:match('^#_Ref') or link.target:match('^#fig:') then reference = true end
  end, Cite = function(cite)
    for _, citation in ipairs(cite.citations) do
      if citation.id:match('^fig:') then reference = true end
    end
  end, RawInline = function(raw)
    if raw.text:match('%[@fig:') then reference = true end
  end })
  -- A paragraph such as "[图3-48](#_Ref...)中所示..." is prose, even
  -- though its visible text begins with a figure number. Never consume it.
  if reference then return nil end
  local text = normalized(pandoc.utils.stringify(inlines))
  local number, suffix = text:match('^%s*图%s*(%d+)(.*)$')
  if not number then number, suffix = text:match('^%s*figure%.?%s*(%d+)(.*)$') end
  if not number then number, suffix = text:match('^%s*fig%.?%s*(%d+)(.*)$') end
  if not number then return nil end
  if suffix:match('^%s*%-') then
    local chapter, remaining = suffix:match('^%s*%-%s*(%d+)(.*)$')
    if not chapter then return nil end
    number, suffix = number .. '-' .. chapter, remaining
  end
  -- Plain typed references can also start a paragraph. A common prose lead-in
  -- such as "图1-2中所示..." is not an overall caption after a layout table.
  for _, cue in ipairs({ '^%s*中', '^%s*所示', '^%s*显示', '^%s*可见',
    '^%s+shows%f[%W]', '^%s+illustrates%f[%W]', '^%s+depicts%f[%W]' }) do
    if suffix:match(cue) then return nil end
  end
  return number
end

--- Trim spacing and ignore empty bookmark spans when deciding if text exists.
local function trim(inlines)
  local result = pandoc.List(inlines)
  while #result > 0 and (result[1].t == 'Space' or result[1].t == 'SoftBreak'
    or result[1].t == 'LineBreak') do result:remove(1) end
  while #result > 0 and (result[#result].t == 'Space' or result[#result].t == 'SoftBreak'
    or result[#result].t == 'LineBreak') do result:remove(#result) end
  return result
end

--- Read plain paragraphs and Word's single-item alphabetic caption lists.
local function paragraph(block)
  if block.t == 'Para' or block.t == 'Plain' then return block.content end
  if block.t == 'BlockQuote' or block.t == 'Div' then
    if #block.content == 1 then return paragraph(block.content[1]) end
  elseif block.t == 'OrderedList' and #block.content == 1 and #block.content[1] == 1 then
    local attrs = block.listAttributes
    if attrs.style == 'LowerAlpha' or attrs.style == 'UpperAlpha' then
      local content = paragraph(block.content[1][1])
      if content then
        local letter = string.char((attrs.style == 'LowerAlpha' and 96 or 64) + attrs.start)
        local result = { pandoc.Str('(' .. letter .. ')'), pandoc.Space() }
        for _, inline in ipairs(content) do result[#result + 1] = inline end
        return result
      end
    end
  elseif block.t == 'Figure' and #block.content == 1 then
    local content = paragraph(block.content[1])
    if content and #content == 1 and content[1].t == 'Image' then return content end
  end
  return nil
end

--- Flatten only paragraph captions, rejecting images embedded in a main caption.
local function caption_content(blocks)
  local result = {}
  for _, block in ipairs(blocks) do
    local content = paragraph(block)
    if not content then return nil end
    if #result > 0 then result[#result + 1] = pandoc.Space() end
    for _, inline in ipairs(content) do
      if inline.t == 'Image' then return nil end
      result[#result + 1] = inline
    end
  end
  return #result > 0 and result or nil
end

--- Read all physical table rows without losing promoted header rows.
local function table_rows(element)
  local rows = {}
  for _, row in ipairs(element.head.rows) do rows[#rows + 1] = row end
  for _, body in ipairs(element.bodies) do
    for _, row in ipairs(body.head) do rows[#rows + 1] = row end
    for _, row in ipairs(body.body) do rows[#rows + 1] = row end
  end
  for _, row in ipairs(element.foot.rows) do rows[#rows + 1] = row end
  return rows
end

--- Split flattened Markdown cells at literal pipes, keeping formatting intact.
local function segments(inlines)
  local result, current = {}, {}
  for _, inline in ipairs(inlines) do
    if inline.t == 'Str' and inline.text == '|' then
      if #current > 0 then result[#result + 1] = current end
      current = {}
    else current[#current + 1] = inline end
  end
  if #current > 0 then result[#result + 1] = current end
  return result
end

--- Parse a cell as images plus caption fragments; never discard structural blocks.
local function read_cell(cell)
  if cell.row_span ~= 1 then return nil end
  local panels, texts = {}, {}
  for line, block in ipairs(cell.contents) do
    local content = paragraph(block)
    if not content then return nil end
    for _, segment in ipairs(segments(content)) do
      local text = {}
      for _, inline in ipairs(segment) do
        if inline.t == 'Image' then
          if caption_number(inline.caption) then return nil end
          panels[#panels + 1] = { image = inline, line = line }
        else text[#text + 1] = inline end
      end
      text = trim(text)
      if pandoc.utils.stringify(text):match('%S') then texts[#texts + 1] = text end
    end
  end
  if #panels > 0 and #texts > 0 then
    -- Both "(a) caption image" and "image (a) caption" are common in Word.
    if #panels ~= #texts then return nil end
    for index, panel in ipairs(panels) do
      if caption_number(texts[index]) then return nil end
      panel.caption = texts[index]
    end
    texts = {}
  end
  return panels, texts
end

--- Return one Word bookmark and remove only its empty anchor spans.
local function take_bookmark(inlines)
  local ids = {}
  pandoc.Span(inlines):walk({ Span = function(span)
    if span.identifier:match('^_Ref[%w_]+$') then ids[span.identifier] = true end
  end })
  local id = nil
  for candidate in pairs(ids) do
    if id then return inlines, nil end
    id = candidate
  end
  if not id then return inlines, nil end
  local cleaned = pandoc.Span(inlines):walk({ Span = function(span)
    if span.identifier == id then
      span.identifier = ''
      if #span.content == 0 then return {} end
      return span.content
    end
  end })
  return cleaned.content, id
end

--- Allocate deterministic group/panel IDs without colliding with authored IDs.
local function unique_id(base)
  local id, suffix = base, 2
  while used[id] do id = base .. '-' .. suffix; suffix = suffix + 1 end
  used[id] = true
  return id
end

--- Remove an authored (a) marker while retaining formatted caption text.
local function subcaption(inlines)
  local text = pandoc.utils.stringify(inlines)
  local label = text:match('^%s*%(([A-Za-z])%)') or text:match('^%s*（([A-Za-z])）')
  if not label then return inlines, nil end
  local removed = false
  local cleaned = pandoc.Span(inlines):walk({ Str = function(str)
    if not removed then
      local after, count = str.text:gsub('^%(([A-Za-z])%)', '', 1)
      if count == 0 then after, count = str.text:gsub('^（([A-Za-z])）', '', 1) end
      if count > 0 then removed = true; return after ~= '' and pandoc.Str(after) or {} end
    end
  end })
  return trim(cleaned.content), label:lower()
end

--- Build a crossref Div; normalize irregular rows to prevent dropped panels.
local function make_group(rows, caption, original_id)
  local number = caption_number(caption)
  if not number then return nil end
  local total = 0
  for _, row in ipairs(rows) do total = total + #row end
  if total < 2 then return nil end
  local clean, bookmark = take_bookmark(caption)
  local base = bookmark and ('fig:' .. bookmark)
    or (original_id and original_id:gsub('^tbl:', 'fig:')) or ('fig:subfig-' .. number)
  if not base:match('^fig:') then return nil end
  -- The source table owns its existing ID; it is safe to transfer it to the Div.
  if original_id == base then used[base] = nil end
  local parent = unique_id(base)
  if original_id and original_id ~= parent then remapped[original_id] = parent end
  local irregular = false
  for _, row in ipairs(rows) do if #row ~= #rows[1] then irregular = true end end
  if irregular then
    local flat = {}
    for _, row in ipairs(rows) do for _, panel in ipairs(row) do flat[#flat + 1] = { panel } end end
    rows = flat
    vertical = vertical + 1
  end
  local blocks, index = {}, 0
  for _, row in ipairs(rows) do
    local inlines = {}
    for _, panel in ipairs(row) do
      index = index + 1
      local image = panel.image:clone()
      local child_caption, anchor = take_bookmark(panel.caption or image.caption)
      local label
      child_caption, label = subcaption(child_caption)
      image.caption = child_caption
      if image.identifier == '' then
        -- Child labels are valid pandoc-crossref subfigure labels. Reusing the
        -- authored marker also keeps references such as #fig:parent-a stable.
        image.identifier = unique_id(anchor and ('fig:' .. anchor)
          or (label and (parent .. '-' .. label) or (parent .. '-' .. index)))
      elseif not image.identifier:match('^fig:') then
        -- Custom image IDs need a fig: prefix for crossref to index panels.
        local original = image.identifier
        image.identifier = unique_id('fig:' .. original)
        remapped[original] = image.identifier
      end
      if label then image.attributes.label = label end
      -- Absolute widths fail in subfigGrid; save source dimensions for review.
      for _, dimension in ipairs({ 'width', 'height' }) do
        if image.attributes[dimension] then
          image.attributes['original-' .. dimension] = image.attributes[dimension]
        end
      end
      image.attributes.width = string.format('%.6g%%', 100 / #row)
      image.attributes.height = nil
      if #inlines > 0 then inlines[#inlines + 1] = pandoc.Space() end
      inlines[#inlines + 1] = image
    end
    blocks[#blocks + 1] = pandoc.Para(inlines)
  end
  blocks[#blocks + 1] = pandoc.Para(clean)
  groups = groups + 1
  return pandoc.Div(blocks, pandoc.Attr(parent))
end

--- Recover a table only when every nonempty cell has an unambiguous role.
local function table_group(element, following)
  if element.caption.short then return nil end
  local caption = caption_content(element.caption.long)
  if caption and not caption_number(caption) then return nil end
  local external = following and paragraph(following)
  if external and not caption_number(external) then external = nil end
  local candidates, rows, pending = {}, {}, nil
  if caption then candidates[#candidates + 1] = caption end
  if external then candidates[#candidates + 1] = external end
  for _, row in ipairs(table_rows(element)) do
    local panels, texts = {}, {}
    for _, cell in ipairs(row.cells) do
      local images, fragments = read_cell(cell)
      if not images then return nil end
      for _, image in ipairs(images) do panels[#panels + 1] = image end
      for _, fragment in ipairs(fragments) do texts[#texts + 1] = fragment end
    end
    if #panels > 0 then
      if #texts > 0 then return nil end
      -- Two separate image paragraphs in one cell are a vertical layout;
      -- multiple images in one paragraph are a horizontal row.
      if #row.cells == 1 and #panels > 1 and panels[1].line ~= panels[#panels].line then
        local line_row, previous = {}, nil
        for _, panel in ipairs(panels) do
          if previous and panel.line ~= previous then rows[#rows + 1] = line_row; line_row = {} end
          line_row[#line_row + 1] = panel
          previous = panel.line
        end
        rows[#rows + 1] = line_row
      else rows[#rows + 1] = panels end
      pending = panels
    elseif #texts > 0 then
      if #texts == 1 and caption_number(texts[1]) then
        candidates[#candidates + 1] = texts[1]
        pending = nil
      elseif pending and #texts == #pending then
        for index, panel in ipairs(pending) do
          if panel.caption or caption_number(texts[index]) then return nil end
          panel.caption = texts[index]
        end
        pending = nil
      else return nil end
    end
  end
  if #candidates == 0 then return nil end
  local signature = pandoc.utils.stringify(candidates[1])
  for _, candidate in ipairs(candidates) do
    if pandoc.utils.stringify(candidate) ~= signature then
      conflicts = conflicts + 1
      return nil
    end
  end
  -- Prefer the caption with a bookmark so stable Word references remain usable.
  caption = candidates[1]
  for _, candidate in ipairs(candidates) do
    local _, anchor = take_bookmark(candidate)
    if anchor then caption = candidate end
  end
  local group = make_group(rows, caption, element.identifier ~= '' and element.identifier or nil)
  return group, group and external ~= nil
end

--- Recognize vertical paragraph sequences only with explicit child markers.
local function paragraph_group(blocks, start)
  local rows, index = {}, start
  while index <= #blocks do
    local content = paragraph(blocks[index])
    if not content or #content ~= 1 or content[1].t ~= 'Image' then break end
    local caption = blocks[index + 1] and paragraph(blocks[index + 1])
    if not caption then break end
    local _, label = subcaption(caption)
    if not label then break end
    rows[#rows + 1] = { { image = content[1], caption = caption } }
    index = index + 2
  end
  local caption = blocks[index] and paragraph(blocks[index])
  if #rows < 2 or not caption or not caption_number(caption) then return nil end
  return make_group(rows, caption), index + 1
end

--- Transform sibling layouts while leaving established crossref groups intact.
local function detect_blocks(blocks)
  local result, index = {}, 1
  while index <= #blocks do
    local group, next_index
    if blocks[index].t == 'Table' then
      local consumed
      group, consumed = table_group(blocks[index], blocks[index + 1])
      next_index = index + (consumed and 2 or 1)
    else group, next_index = paragraph_group(blocks, index) end
    result[#result + 1] = group or blocks[index]
    index = group and next_index or index + 1
  end
  return result
end

--- Detect groups, publish grid metadata and repair previously exported table refs.
function Pandoc(doc)
  doc:walk({ Block = function(block)
    if block.identifier and block.identifier ~= '' then used[block.identifier] = true end
  end, Inline = function(inline)
    if inline.identifier and inline.identifier ~= '' then used[inline.identifier] = true end
  end })
  doc = doc:walk({ traverse = 'topdown',
    Div = function(div) if div.identifier:match('^fig:') then return div, false end end,
    Blocks = detect_blocks,
  })
  if groups > 0 then
    doc.meta.subfigGrid = pandoc.MetaBool(true)
    doc = doc:walk({ Link = function(link)
      local replacement = remapped[link.target:match('^#(.+)$')]
      if replacement then link.target = '#' .. replacement; return link end
    end, Cite = function(cite)
      for _, citation in ipairs(cite.citations) do
        citation.id = remapped[citation.id] or citation.id
      end
      return cite
    end })
    io.stderr:write('[detect-subfigures] recovered ' .. groups .. ' group(s); normalized '
      .. vertical .. ' irregular layout(s) to vertical rows\n')
  end
  if conflicts > 0 then
    io.stderr:write('[detect-subfigures] preserved ' .. conflicts .. ' layout(s) with conflicting captions\n')
  end
  return doc
end
