-- Convert confirmed Word figure/table/equation bookmarks to crossref syntax.
-- Run after equation_tables.lua exposes table-based math and after TOC cleanup
-- and detect_figure.lua/detect_table.lua. A Table or Figure caption, or a
-- standalone Image caption (or its preceding empty paragraph), can carry an
-- empty _Ref span; an inline/display equation paragraph can carry the same.
-- Pairing images/captions in Lua leaves Para/Image
-- nodes in the AST, so both forms must be recognized without re-reading Markdown.
-- These known targets receive fig:/tbl:/eq: identifiers, then their inbound
-- bookmark links become [@fig:...]/[@tbl:...]/[@eq:...] references.
-- Examples:
--   Figure caption: []{#_Ref123 .anchor}Network -> ![Network](image.png){#fig:_Ref123}
--   $$E=mc^2$$ []{#_Ref456 .anchor}(7) -> $$E=mc^2$$ {#eq:_Ref456}
--   $E=mc^2$ []{#_Ref456 .anchor}(7) -> $$E=mc^2$$ {#eq:_Ref456}
--   $F=ma$ []{#_Ref789 .anchor}force balance -> $$F=ma$$ {#eq:_Ref789} force balance
--   [Figure 1](#_Ref123) -> [@fig:_Ref123] (only if the figure was confirmed)
--   [Equation 7](#_Ref456) -> [@eq:_Ref456] (only if the equation was confirmed)
--   : []{#_Ref789 .anchor}Table title -> : Table title {#tbl:_Ref789}
--   [Table 1](#_Ref789) -> [@tbl:_Ref789] (only if the table was confirmed)
-- Subfigure Divs and images labeled by detect_subfigures.lua are registered too:
--   ::: {#fig:_Ref123} ... -> [Figure 1](#_Ref123) becomes [@fig:_Ref123].
-- Manual numeric equation labels are dropped for crossref to regenerate;
-- descriptive labels survive.
-- A bookmarked equation at the start of its paragraph becomes DisplayMath here,
-- after equation_tables.lua has flattened layouts as InlineMath. Equations with
-- preceding prose retain their inline context.
-- Table captions use the same empty _Ref span as
-- figure captions and receive tbl: identifiers; unknown links, uncaptured
-- tables and section bookmarks are retained. Nested caption spans and multiple
-- distinct caption bookmarks are not guessed as targets.
-- When papper-fuzzy-crossrefs metadata is enabled, save equation IDs and their
-- original numeric labels in temporary metadata before removing those labels.
-- crossrefs_fuzz.lua consumes this mapping and removes it before Markdown export,
-- allowing typed "如式4-43" references to reuse an anchored equation's ID.
-- The Image walk also normalizes ./media/image.png to media/image.png.
-- Run after the earlier filters in the Convert chain:
--   pandoc input.docx -f docx -t markdown -L pandoc/filters/convert/crossrefs.lua

--- Return a Word-generated bookmark identifier carried by an empty Span.
local function anchor_id(inline)
  if inline.t == 'Span' and inline.identifier:match('^_Ref[%w_]+$') and #inline.content == 0 then
    return inline.identifier
  end
  return nil
end

--- Return one bookmark from a paragraph that contains only bookmark spans.
local function anchor_paragraph(block)
  if block.t ~= 'Para' and block.t ~= 'Plain' then return nil end
  local found = nil
  for _, inline in ipairs(block.content) do
    local id = anchor_id(inline)
    if id then
      if found and found ~= id then return nil end
      found = id
    elseif inline.t ~= 'Space' and inline.t ~= 'SoftBreak' then
      return nil
    end
  end
  return found
end

--- Find one inline/display equation and its bookmark in a flattened paragraph.
local function equation_anchor(block)
  if block.t ~= 'Para' and block.t ~= 'Plain' then return nil end
  local math, id = nil, nil
  for _, inline in ipairs(block.content) do
    -- Word can import numbered object paragraphs as InlineMath too. Count both
    -- types so mixed paragraphs cannot assign a bookmark ambiguously.
    if inline.t == 'Math' then
      if math then return nil end
      math = inline
    else
      local candidate = anchor_id(inline)
      if candidate then
        if id and id ~= candidate then return nil end
        id = candidate
      end
    end
  end
  return math and id or nil
end

--- Remove an empty bookmark Span while preserving visible caption content.
local function caption_inlines(caption_blocks)
  local result, id = {}, nil
  for _, block in ipairs(caption_blocks) do
    if block.t ~= 'Para' and block.t ~= 'Plain' then return nil, nil end
    for _, inline in ipairs(block.content) do
      local candidate = anchor_id(inline)
      if candidate then
        if id and id ~= candidate then return nil, nil end
        id = candidate
      else
        result[#result + 1] = inline
      end
    end
  end
  return result, id
end

--- Return the sole image in a simple Pandoc Figure.
local function figure_image(figure)
  if #figure.content ~= 1 then return nil end
  local block = figure.content[1]
  if (block.t ~= 'Plain' and block.t ~= 'Para') or #block.content ~= 1 then return nil end
  return block.content[1].t == 'Image' and block.content[1] or nil
end

--- Recognize captioned standalone images produced by detect_figure.lua.
local function paragraph_image(block)
  if (block.t ~= 'Para' and block.t ~= 'Plain') or #block.content ~= 1 then return nil end
  local image = block.content[1]
  -- Changing Image.caption in a previous filter does not promote its Para to
  -- Figure. Restrict this fix to sole, captioned images to avoid labeling prose.
  if image.t == 'Image' and #image.caption > 0 then return image end
  return nil
end

--- Detect a plain displayed number which pandoc-crossref will regenerate.
local function is_manual_number(inlines)
  local text = pandoc.utils.stringify(inlines)
  return text:match('^%s*%(%d+[%.%d%-]*%)%s*$') ~= nil
end

--- Attach a known equation identifier and remove its old numeric label.
local function label_equation(block, id)
  local kept, math, has_prefix = {}, nil, false
  for _, inline in ipairs(block.content) do
    if inline.t == 'Math' then
      math = inline
    elseif not anchor_id(inline) then
      kept[#kept + 1] = inline
      if not math and pandoc.utils.stringify({ inline }):match('%S') then has_prefix = true end
    end
  end
  if not math then return block end
  if not has_prefix then
    -- Flattened layout equations may have descriptive labels instead of numbers.
    -- Promote the confirmed bookmark target here so later builds can resolve its ID.
    -- Preceding prose keeps an inline equation in its authored context.
    math.mathtype = 'DisplayMath'
  end
  local label = pandoc.RawInline('markdown', ' {#eq:' .. id .. '}')
  if is_manual_number(kept) or pandoc.utils.stringify(kept):match('^%s*$') then
    return pandoc.Para({ math, label })
  end
  local result = {}
  -- Inline formulas may sit inside prose; keep surrounding text in authored
  -- order instead of moving the math to the beginning of its paragraph.
  for _, inline in ipairs(block.content) do
    if not anchor_id(inline) then
      result[#result + 1] = inline
      if inline.t == 'Math' then result[#result + 1] = label end
    end
  end
  return pandoc.Para(result)
end

--- Attach a known table identifier while removing its Word bookmark span.
local function label_table(block)
  local caption, id = caption_inlines(block.caption.long)
  if not id or not caption or #caption == 0 then return nil end
  local caption_blocks = block.caption.long
  for _, paragraph in ipairs(caption_blocks) do
    local kept = {}
    for _, inline in ipairs(paragraph.content) do
      if not anchor_id(inline) then kept[#kept + 1] = inline end
    end
    paragraph.content = kept
  end
  -- Retain caption block boundaries and formatting instead of flattening them.
  block.caption.long = caption_blocks
  block.identifier = 'tbl:' .. id
  return block, id
end

--- Apply figure, table and equation labels before changing their inbound links.
function Pandoc(doc)
  local targets, blocks = {}, {}
  local equation_labels = pandoc.MetaList({})
  local fuzzy_enabled = doc.meta['papper-fuzzy-crossrefs'] == true
    or pandoc.utils.stringify(doc.meta['papper-fuzzy-crossrefs'] or '') == 'true'
  local pending_anchor = nil
  for _, block in ipairs(doc.blocks) do
    local standalone = anchor_paragraph(block)
    local image = block.t == 'Figure' and figure_image(block) or paragraph_image(block)
    if standalone then
      pending_anchor = standalone
    elseif image then
      local caption_blocks = block.t == 'Figure' and block.caption.long
        or { pandoc.Plain(image.caption) }
      local caption, internal = caption_inlines(caption_blocks)
      local id = internal or pending_anchor
      if image and caption and id then
        image.caption = caption
        image.identifier = 'fig:' .. id
        blocks[#blocks + 1] = pandoc.Para({ image })
        targets[id] = 'fig'
      else
        if pending_anchor then blocks[#blocks + 1] = pandoc.Para({ pandoc.Span({}, pandoc.Attr(pending_anchor, { 'anchor' })) }) end
        blocks[#blocks + 1] = block
      end
      pending_anchor = nil
    else
      if pending_anchor then
        blocks[#blocks + 1] = pandoc.Para({ pandoc.Span({}, pandoc.Attr(pending_anchor, { 'anchor' })) })
        pending_anchor = nil
      end
      local id = equation_anchor(block)
      if id then
        if fuzzy_enabled then
          local label = {}
          for _, inline in ipairs(block.content) do
            if inline.t ~= 'Math' and not anchor_id(inline) then label[#label + 1] = inline end
          end
          equation_labels:insert(pandoc.MetaMap({
            id = pandoc.MetaString('eq:' .. id),
            label = pandoc.MetaString(pandoc.utils.stringify(label)),
          }))
        end
        blocks[#blocks + 1] = label_equation(block, id)
        targets[id] = 'eq'
      else
        blocks[#blocks + 1] = block
      end
    end
  end
  if pending_anchor then
    blocks[#blocks + 1] = pandoc.Para({ pandoc.Span({}, pandoc.Attr(pending_anchor, { 'anchor' })) })
  end
  doc.blocks = blocks
  if fuzzy_enabled then doc.meta['papper-equation-labels'] = equation_labels end
  local table_count = 0
  doc = doc:walk({
    -- Subfigure groups and their panels already own IDs from detect_subfigures.
    -- Register them before rewriting links, including forward references.
    Div = function(block)
      local id = block.identifier:match('^fig:(_Ref[%w_]+)$')
      if id then targets[id] = 'fig' end
    end,
    Image = function(image)
      local id = image.identifier:match('^fig:(_Ref[%w_]+)$')
      if id then targets[id] = 'fig' end
    end,
    -- detect_table.lua pairs captions inside lists, quotes and table cells too.
    -- Collect all table targets before rewriting links, including forward links.
    Table = function(block)
      local labeled, id = label_table(block)
      if id then
        targets[id] = 'tbl'
        table_count = table_count + 1
      end
      return labeled
    end,
  })
  if table_count > 0 then
    io.stderr:write('[crossrefs] labeled ' .. table_count .. ' table(s)\n')
  end
  return doc:walk({
    Link = function(link)
      local id = link.target:match('^#(_Ref[%w_]+)$')
      if id and targets[id] then
        -- Keep brackets literal so the exported Markdown uses citation syntax
        -- consistently, including references touching surrounding Chinese text.
        return pandoc.RawInline('markdown', '[@' .. targets[id] .. ':' .. id .. ']')
      end
      return nil
    end,
    Image = function(image)
      -- Pandoc can prefix extracted media paths with ./ in the output directory.
      image.src = image.src:gsub('^%./', '')
      return image
    end,
  })
end
