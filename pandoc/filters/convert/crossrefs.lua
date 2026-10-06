-- Convert confirmed Word figure/equation bookmarks to pandoc-crossref syntax.
-- Run after equation_tables.lua exposes table-based math and after TOC cleanup
-- and figure_captions.lua. A Figure caption or a standalone Image caption (or
-- its preceding empty paragraph) can carry an empty _Ref span; a display-equation
-- paragraph can carry the same. Pairing images/captions in Lua leaves Para/Image
-- nodes in the AST, so both forms must be recognized without re-reading Markdown.
-- These known targets receive fig:/eq: identifiers, then their inbound bookmark
-- links are replaced by @fig:.../@eq:... references throughout the document.
-- Examples:
--   Figure caption: []{#_Ref123 .anchor}Network -> ![Network](image.png){#fig:_Ref123}
--   $$E=mc^2$$ []{#_Ref456 .anchor}(7) -> $$E=mc^2$$ {#eq:_Ref456}
--   [Figure 1](#_Ref123) -> @fig:_Ref123 (only if the figure was confirmed)
--   [Equation 7](#_Ref456) -> @eq:_Ref456 (only if the equation was confirmed)
-- Manual numeric equation labels are dropped for crossref to regenerate;
-- descriptive labels survive. Unknown links, ordinary tables and section
-- bookmarks are retained. Nested caption spans are not guessed as targets.
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

--- Find a display equation and its bookmark in one flattened paragraph.
local function equation_anchor(block)
  if block.t ~= 'Para' and block.t ~= 'Plain' then return nil end
  local math, id = nil, nil
  for _, inline in ipairs(block.content) do
    if inline.t == 'Math' and inline.mathtype == 'DisplayMath' then
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

--- Recognize captioned standalone images produced by figure_captions.lua.
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
  local kept, math = {}, nil
  for _, inline in ipairs(block.content) do
    if inline.t == 'Math' and inline.mathtype == 'DisplayMath' then
      math = inline
    elseif not anchor_id(inline) then
      kept[#kept + 1] = inline
    end
  end
  if not math then return block end
  if is_manual_number(kept) then kept = {} end
  local result = { math, pandoc.RawInline('markdown', ' {#eq:' .. id .. '}') }
  if #kept > 0 then
    result[#result + 1] = pandoc.Space()
    for _, inline in ipairs(kept) do result[#result + 1] = inline end
  end
  return pandoc.Para(result)
end

--- Apply figure and equation labels before changing their inbound links.
function Pandoc(doc)
  local targets, blocks = {}, {}
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
  return doc:walk({
    Link = function(link)
      local id = link.target:match('^#(_Ref[%w_]+)$')
      if id and targets[id] then
        return pandoc.RawInline('markdown', '@' .. targets[id] .. ':' .. id)
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
