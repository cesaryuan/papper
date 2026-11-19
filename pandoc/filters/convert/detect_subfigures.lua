-- Recover Word subfigures laid out with tables or successive image paragraphs.
-- Run after equation/TOC cleanup, BEFORE extract_inline_images.lua, detect_figure.lua
-- and crossrefs.lua: those filters otherwise separate pictures from their cells.
-- Requires an unambiguous numbered overall caption and at least two images for a group
-- (图1, 图1‑13, Figure 1-13, Fig. 1; English is case-insensitive).
-- The caption may be the table caption, a full-width cell, or the next paragraph.
-- Supported layouts include:
--   | image A | image B |       | image A |   | (a) caption + image A |
--   | (a) A   | (b) B   |       | (a) A   |   | image B + (b) caption |
--   | image C | image D |       | image B |   | 图1‑13 Overall caption |
--   | (c) C   | (d) D   |       | (b) B   |
-- Separate caption rows, captions before/after images, merged full-width cells,
-- full-width （a） markers, ordered-list (a) markers and captionless panels work.
-- A Chinese (a) row followed by an English (a) row becomes one bilingual panel;
-- 图2‑3 and Fig. 2‑3 become the primary and translated captions of the same group.
-- The same overall caption pair also identifies a standalone picture. With
-- --svg, a merged (a) caption below two pictures stays centered across both.
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
-- Authored (a)/(b) and full-width markers are removed from child captions;
-- their letters still supply panel IDs and label attributes for cross-references.
-- crossrefs.lua resolves Word links; crossrefs_fuzz.lua can resolve typed references.

local used, remapped = {}, {}
local groups, vertical, conflicts, singles = 0, 0, 0, 0
local svg_output = os.getenv('PAPPER_CONVERT_SUBFIGURE_SVG') == '1'

local dashes = { '-', '‐', '‑', '‒', '–', '—', '−', '﹣', '－' }

--- Read the caption number; only merged table titles may have a missing chapter field.
local function caption_number(inlines, allow_missing)
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
  local text = pandoc.utils.stringify(inlines):lower()
  local body = text:match('^%s*图%s*(.*)$')
  local language = body and 'zh' or 'en'
  body = body or text:match('^%s*figure%.?%s*(.*)$') or text:match('^%s*fig%.?%s*(.*)$')
  if not body then return nil end
  local number, suffix = body:match('^(%d+)(.*)$')
  local tail = (suffix or body):gsub('^%s+', '')
  for _, dash in ipairs(dashes) do
    if tail:sub(1, #dash) == dash then
      local part, remaining = tail:sub(#dash + 1):match('^%s*(%d+)(.*)$')
      if not part and allow_missing and not number and language == 'en' then
        part, remaining = tail:sub(#dash + 1):match('^(%.)(%s+%S.*)$')
        if part then part = '' end
      end
      if not part or not (number or allow_missing) then return nil end
      number, suffix = (number or '') .. '-' .. part, remaining
      break
    end
  end
  if not number then return nil end
  -- Plain typed references can also start a paragraph. A common prose lead-in
  -- such as "图1-2中所示..." is not an overall caption after a layout table.
  for _, cue in ipairs({ '^%s*中', '^%s*所示', '^%s*显示', '^%s*可见',
    '^%s+shows%f[%W]', '^%s+illustrates%f[%W]', '^%s+depicts%f[%W]' }) do
    if suffix:match(cue) then return nil end
  end
  local delimiter = language == 'en' and '^%s*[%.:]?%s*' or '^%s*'
  return number, language, #text - #suffix + #suffix:match(delimiter)
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

--- Remove a known prefix across formatted runs without flattening the rest of the caption.
local function drop_prefix(inlines, remaining)
  local result = {}
  for _, inline in ipairs(inlines) do
    local length = #pandoc.utils.stringify(inline)
    if remaining == 0 or length == 0 then result[#result + 1] = inline
    elseif remaining >= length then remaining = remaining - length
    elseif inline.t == 'Str' then
      result[#result + 1] = pandoc.Str(inline.text:sub(remaining + 1))
      remaining = 0
    elseif inline.content then
      local copy = inline:clone()
      local content
      content, remaining = drop_prefix(copy.content, remaining)
      if not content then return nil end
      copy.content = content
      result[#result + 1] = copy
    else
      return nil
    end
  end
  return result, remaining
end

--- Strip the leading letter, including a Word inline equation containing only “(a)”.
local function subcaption(inlines)
  local text = pandoc.utils.stringify(inlines)
  local label, ending = text:match('^%s*%(([A-Za-z])%)()')
  if not label then label, ending = text:match('^%s*（([A-Za-z])）()') end
  if not label then return inlines, nil end
  local cleaned, remaining = drop_prefix(inlines, ending - 1)
  if not cleaned or remaining ~= 0 then return inlines, nil end
  return trim(cleaned), label:lower()
end

--- Identify Han text without depending on the document's declared language.
local function has_han(text)
  for _, code in utf8.codes(text) do
    if (code >= 0x3400 and code <= 0x9fff) or (code >= 0xf900 and code <= 0xfaff)
      or (code >= 0x20000 and code <= 0x323af) then return true end
  end
  return false
end

--- Pair only the same explicit panel marker with distinct Chinese and English text.
local function panel_translation(first, second)
  local primary, label = subcaption(first)
  local english, other = subcaption(second)
  if not label or label ~= other then return nil end
  local primary_text, english_text = pandoc.utils.stringify(primary), pandoc.utils.stringify(english)
  if not english_text:match('[A-Za-z]') then return nil end
  return has_han(primary_text) and not has_han(english_text)
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
  if #panels == 1 and #texts == 2 and panel_translation(texts[1], texts[2]) then
    panels[1].caption, panels[1].english, texts = texts[1], texts[2], {}
  elseif #panels > 0 and #texts > 0 then
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

--- Serialize formatted caption text using the existing caption-en Markdown syntax.
local function caption_markdown(inlines)
  return pandoc.write(pandoc.Pandoc{pandoc.Plain(inlines)}, 'markdown', {wrap_text='none'}):gsub('%s+$', '')
end

--- Pair translated titles, using a complete number to fill a missing Word field.
local function overall_caption(candidates)
  local titles, number = {}, nil
  for _, title in ipairs(candidates) do
    local candidate = title.inlines
    local current, language, ending = caption_number(candidate, title.merged)
    if not current then return nil end
    if current:sub(1, 1) ~= '-' then
      if number and current ~= number then return nil end
      number = current
    end
    local entry = titles[language]
    local signature = pandoc.utils.stringify(candidate)
    if entry and signature ~= entry.signature then return nil end
    local _, bookmark = take_bookmark(candidate)
    if not entry or bookmark then
      titles[language] = {inlines=candidate, number=current, ending=ending, signature=signature}
    end
  end
  for _, title in pairs(titles) do
    if title.number:sub(1, 1) == '-' then
      local suffix = number and number:match('^%d+%-(%d+)$')
      -- A missing Word field is recoverable only in an adjacent pair of full-width rows.
      local first, second = candidates[1], candidates[2]
      if not suffix or (title.number ~= '-' and title.number ~= '-' .. suffix)
        or #candidates ~= 2 or not first.merged or not second.merged or second.row ~= first.row + 1 then
        return nil
      end
    end
  end
  if not titles.zh then return titles.en and titles.en.inlines end
  local primary = titles.zh.inlines
  if titles.zh.number:sub(1, 1) == '-' then
    local text = drop_prefix(primary, titles.zh.ending)
    if not text then return nil end
    primary = pandoc.List{pandoc.Str('图'), pandoc.Space(), pandoc.Str(number), pandoc.Space()}
    primary:extend(text)
  end
  return primary, titles.en
end

--- Build a standalone bilingual figure or a crossref Div from an identified table.
local function make_group(rows, caption, original_id, english)
  local number = caption_number(caption)
  if not number then return nil end
  local total = 0
  for _, row in ipairs(rows) do total = total + #row end
  local single = total == 1 and rows[1][1] or nil
  if total == 0 or single and (not english or single.caption or #single.image.caption > 0) then return nil end
  local clean, bookmark = take_bookmark(caption)
  local translation, english_bookmark
  if english then
    local text
    text, english_bookmark = take_bookmark(english.inlines)
    text = drop_prefix(text, english.ending)
    if not text or #text == 0 then return nil end
    translation = caption_markdown(text)
  end
  bookmark = bookmark or english_bookmark
  local base = bookmark and ('fig:' .. bookmark)
    or (original_id and original_id:gsub('^tbl:', 'fig:')) or ((single and 'fig:figure-' or 'fig:subfig-') .. number)
  if not base:match('^fig:') then return nil end
  -- The source table owns its existing ID; it is safe to transfer it to the Div.
  if original_id == base then used[base] = nil end
  local parent = unique_id(base)
  if original_id and original_id ~= parent then remapped[original_id] = parent end
  if english_bookmark then remapped[english_bookmark] = parent end
  if single then
    local image = single.image:clone()
    if image.identifier ~= '' and image.identifier ~= parent then remapped[image.identifier] = parent end
    if bookmark and parent ~= 'fig:' .. bookmark then remapped[bookmark] = parent end
    image.identifier, image.caption = parent, clean
    image.attributes['caption-en'] = translation
    singles = singles + 1
    return pandoc.Para({image})
  end
  local irregular = false
  for _, row in ipairs(rows) do if #row ~= #rows[1] then irregular = true end end
  if irregular and not svg_output then
    local flat = {}
    for _, row in ipairs(rows) do for _, panel in ipairs(row) do flat[#flat + 1] = { panel } end end
    rows = flat
    vertical = vertical + 1
  end
  local blocks, index = {}, 0
  for _, row in ipairs(rows) do
    local inlines = {}
    for column, panel in ipairs(row) do
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
      if panel.english then
        local translation, english_anchor = take_bookmark(panel.english)
        image.attributes['caption-en'] = caption_markdown(subcaption(translation))
        if english_anchor then remapped[english_anchor] = image.identifier end
      end
      if row.shared and column == 1 then
        -- The SVG writer uses this marker to distinguish a row title from a panel title.
        image.attributes['papper-convert-shared-row'] = 'title'
      end
      -- Absolute widths fail in subfigGrid; save source dimensions for review.
      for _, dimension in ipairs({ 'width', 'height' }) do
        if image.attributes[dimension] then
          image.attributes['original-' .. dimension] = image.attributes[dimension]
        end
      end
      image.attributes.width = string.format('%.6g%%', panel.cell_percent or 100 / #row)
      image.attributes.height = nil
      if #inlines > 0 then inlines[#inlines + 1] = pandoc.Space() end
      inlines[#inlines + 1] = image
    end
    blocks[#blocks + 1] = pandoc.Para(inlines)
  end
  blocks[#blocks + 1] = pandoc.Para(clean)
  groups = groups + 1
  local div = pandoc.Div(blocks, pandoc.Attr(parent))
  if translation then div.attributes['caption-en'] = translation end
  -- The SVG writer restores single-column crossref rows if it cannot represent this irregular group.
  if svg_output and irregular then div.attributes['papper-convert-uneven-rows'] = '1' end
  return div
end

--- Recover a table only when every nonempty cell has an unambiguous role.
local function table_group(element, following)
  if element.caption.short then return nil end
  local caption = caption_content(element.caption.long)
  if caption and not caption_number(caption) then return nil end
  local external = following and paragraph(following)
  if external and not caption_number(external) then external = nil end
  local candidates, rows, pending = {}, {}, nil
  if caption then candidates[#candidates + 1] = {inlines=caption} end
  if external then candidates[#candidates + 1] = {inlines=external} end
  for row_index, row in ipairs(table_rows(element)) do
    local panels, texts = {}, {}
    local column = 1
    for _, cell in ipairs(row.cells) do
      local images, fragments = read_cell(cell)
      if not images then return nil end
      -- SVG rows can represent unequal Word columns and merged horizontal cells.
      if svg_output and #images == 1 then
        local share = 0
        for i = column, column + cell.col_span - 1 do
          local spec = element.colspecs[i]
          if not spec or not tonumber(spec[2]) then share = 0; break end
          share = share + spec[2]
        end
        if share > 0 then images[1].cell_percent = share * 100 end
      end
      column = column + cell.col_span
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
        pending = panels
      else
        rows[#rows + 1] = panels
        pending = panels
      end
    elseif #texts > 0 then
      local merged = #row.cells == 1 and row.cells[1].col_span == #element.colspecs
      if #texts == 1 and caption_number(texts[1], merged) then
        candidates[#candidates + 1] = {inlines=texts[1], merged=merged, row=row_index}
        pending = nil
      elseif svg_output and pending and pending == rows[#rows] and #pending > 1
        and #texts == 1 and merged then
        local first = pending[1]
        if pending.shared then
          if first.english or not panel_translation(first.caption, texts[1]) then return nil end
          first.english = texts[1]
        else
          local _, label = subcaption(texts[1])
          if not label then return nil end
          for _, panel in ipairs(pending) do
            if panel.caption or #panel.image.caption > 0 then return nil end
          end
          first.caption, pending.shared = texts[1], true
        end
      elseif pending and #texts == #pending then
        if pending.shared then return nil end
        for index, panel in ipairs(pending) do
          local text = texts[index]
          if caption_number(text) then return nil end
          if not panel.caption then panel.caption = text
          elseif not panel.english and panel_translation(panel.caption, text) then panel.english = text
          else return nil end
        end
      else return nil end
    end
  end
  if #candidates == 0 then return nil end
  local english
  caption, english = overall_caption(candidates)
  if not caption then conflicts = conflicts + 1; return nil end
  local group = make_group(rows, caption, element.identifier ~= '' and element.identifier or nil, english)
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
  if groups > 0 or singles > 0 then
    if groups > 0 and not svg_output then doc.meta.subfigGrid = pandoc.MetaBool(true) end
    doc = doc:walk({ Link = function(link)
      local replacement = remapped[link.target:match('^#(.+)$')]
      if replacement then link.target = '#' .. replacement; return link end
    end, Cite = function(cite)
      for _, citation in ipairs(cite.citations) do
        citation.id = remapped[citation.id] or citation.id
      end
      return cite
    end })
    io.stderr:write('[detect-subfigures] recovered ' .. groups .. ' group(s) and ' .. singles
      .. ' bilingual single figure(s); normalized ' .. vertical .. ' irregular layout(s) to vertical rows\n')
  end
  if conflicts > 0 then
    io.stderr:write('[detect-subfigures] preserved ' .. conflicts .. ' layout(s) with conflicting captions\n')
  end
  return doc
end
