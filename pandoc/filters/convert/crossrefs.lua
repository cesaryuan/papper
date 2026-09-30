-- Convert Word bookmark links and caption anchors to pandoc-crossref syntax.
-- Run after equation_tables.lua, so table-based equation anchors are visible
-- beside the display Math node in an ordinary paragraph.

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
local function caption_inlines(figure)
  local result, id = {}, nil
  for _, block in ipairs(figure.caption.long) do
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
    if standalone then
      pending_anchor = standalone
    elseif block.t == 'Figure' then
      local image = figure_image(block)
      local caption, internal = caption_inlines(block)
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
