-- Recover typed figure/table/equation references after crossrefs.lua, enabled by
-- `papper convert input.docx --fuzzy-crossrefs`. Word authors sometimes type
-- "如图1-14所示" instead of inserting a bookmark-linked cross-reference.
-- Three passes collect numbered captions, assign/reuse IDs for unique targets,
-- and replace matching references in explicit reference phrases in prose.
-- Prefixes: 图/Figure/Fig. and 表/Table/Tbl. (case-insensitive English, optional
-- period). Single numbers and chapter-number pairs accept optional spaces and
-- Unicode dashes. Caption formatting, image attributes and table structure are
-- preserved; figure/table numbering moves to original-number attributes.
-- Examples:
--   如图1-14所示 + ![图1‑14 脆弱性曲线](image.svg)
--     -> 如[@fig:fuzz-1-14]所示 + ![脆弱性曲线](image.svg){#fig:fuzz-1-14}
--        with original-number="图1‑14"
--   如表1-3所示 + a Table captioned 表1‑3 鲁棒性度量
--     -> 如[@tbl:fuzz-1-3]所示 + the same Table with #tbl:fuzz-1-3
--   As shown in Fig. 4, ... + ![Fig. 4 Result](image.png){#fig:_Ref123}
--     -> As shown in [@fig:_Ref123], ... (reuse the ID from crossrefs.lua)
--   $x=y$ (4-43) + 如式 4-43 所示
--     -> $$x=y$$ {#eq:fuzz-4-43} + 如[@eq:fuzz-4-43] 所示
-- A numbered formula must be a standalone paragraph with one Math and only its
-- trailing parenthesized number. InlineMath becomes DisplayMath for downstream
-- equation numbering; ordinary prose math and descriptive suffixes are retained.
-- Existing eq: IDs are reused, including numbered Word bookmarks whose original
-- label crossrefs.lua saved in temporary metadata. The mapping and enable flag
-- are removed before writing output. Direct users should set the same flag when
-- running crossrefs.lua before this filter: -M papper-fuzzy-crossrefs:true.
-- Figure/table captions remove their leading prefix and number into an
-- original-number attribute: ![图1‑14 Curves](image.svg) becomes
-- ![Curves](image.svg){#fig:fuzz-1-14 original-number="图1‑14"}.
-- Formulas keep only the generated bare ID; their authored number is discarded.
-- Formatting in the remaining caption survives; ambiguous definitions retain
-- their captions.
-- Cues include 如/见/参见/参考/参照/详见 and 所示/所列/所述, plus English
-- "as shown in", "as show in", "shown in", "see", "refer to", and similar
-- illustrated/listed/given phrases. Plain mentions without a cue stay unchanged.
-- A cue also covers labels joined by and/or/commas/和/及/、, for example
-- "as shown in Figure 1-14 and Table 1-3"; each target must still be unique.
-- Duplicate type/number captions are ambiguous and are skipped. Missing IDs
-- use fig:/tbl:/eq:fuzz-<number> with collision suffixes; existing crossref IDs
-- are reused. Other existing IDs are preserved without inference.
-- Single figures must contain one standalone image; subfigure Divs use their
-- final overall caption and reuse the group ID from detect_subfigures.lua.
-- For example "as shown in Fig. 1" can target ::: {#fig:subfig-1} ... Fig. 1 ... :::.
-- Group/child captions are definitions and are excluded from prose rewriting.
-- Captions, headings, code, math,
-- links, existing citations and raw markup are excluded from prose rewriting.
-- Formatting around a reference is preserved, even when the label crosses runs.
-- Direct usage:
--   pandoc input.docx -t markdown -L crossrefs.lua -L crossrefs_fuzz.lua

local dashes = { '-', '‐', '‑', '‒', '–', '—', '−', '﹣', '－' }
local prefixes = {
  { kind = 'fig', pattern = 'figure%.?', english = true },
  { kind = 'fig', pattern = 'fig%.?', english = true },
  { kind = 'fig', pattern = '图' },
  { kind = 'tbl', pattern = 'table%.?', english = true },
  { kind = 'tbl', pattern = 'tbl%.?', english = true },
  { kind = 'tbl', pattern = '表' },
  { kind = 'eq', pattern = 'equation%.?', english = true },
  { kind = 'eq', pattern = 'eq%.?', english = true },
  { kind = 'eq', pattern = '公式' },
  { kind = 'eq', pattern = '式' },
}
local equation_numbers = {}
local containers = {
  Emph = true, Strong = true, Underline = true, Strikeout = true,
  Superscript = true, Subscript = true, SmallCaps = true, Span = true,
}

--- Read an original-text number, retaining byte offsets across Unicode dashes.
local function read_number(text, position)
  local first, ending = text:match('^%s*(%d+)()', position)
  if not first then return nil end
  local tail = text:sub(ending)
  local spaces = tail:match('^%s*')
  local number = first
  for _, dash in ipairs(dashes) do
    if tail:sub(#spaces + 1, #spaces + #dash) == dash then
      local second, second_end = text:match('^%s*(%d+)()', ending + #spaces + #dash)
      if not second then return nil end
      number, ending = first .. '-' .. second, second_end
      break
    end
  end
  -- Do not match a shorter label inside 1-14-2, 1.14 or an English word.
  tail = text:sub(ending)
  local trimmed = tail:gsub('^%s*', '')
  for _, dash in ipairs(dashes) do
    if trimmed:sub(1, #dash) == dash then return nil end
  end
  if tail:match('^%.%d') or tail:match('^[%w_]') then return nil end
  return number, ending - 1
end

--- Allow another label in the same explicit reference list, without crossing prose.
local function reference_connector(text)
  local connector = text:lower():gsub('%s+', '')
  for _, allowed in ipairs({ 'and', 'or', ',', ',and', ',or', '、', '和', '及', '与',
    '或', '以及', '，', '，和', '，及' }) do
    if connector == allowed then return true end
  end
  return false
end

--- Find the next complete figure/table label with its original byte interval.
local function next_label(text, position)
  local lower, best = text:lower(), nil
  for _, prefix in ipairs(prefixes) do
    local cursor = position
    while cursor <= #text do
      local first, last = lower:find(prefix.pattern, cursor)
      if not first or (best and first >= best.first) then break end
      local valid = not prefix.english or not lower:sub(first - 1, first - 1):match('[%w_]')
      local number_start = last + 1
      local closing = nil
      if prefix.kind == 'eq' then
        local spaces = text:sub(number_start):match('^%s*')
        number_start = number_start + #spaces
        if text:sub(number_start, number_start) == '(' then
          number_start, closing = number_start + 1, ')'
        elseif text:sub(number_start, number_start + 2) == '（' then
          number_start, closing = number_start + 3, '）'
        end
      end
      local number, ending = read_number(text, number_start)
      if number and closing then
        local tail = text:sub(ending + 1)
        local spaces = tail:match('^%s*')
        if tail:sub(#spaces + 1, #spaces + #closing) == closing then
          ending = ending + #spaces + #closing
        else number = nil end
      end
      if valid and number then
        best = { first = first, last = ending, kind = prefix.kind, number = number }
        break
      end
      cursor = last + 1
    end
  end
  return best
end

--- Read a complete parenthesized formula number, including Word Unicode variants.
local function equation_number(text)
  text = text:gsub('^%s+', ''):gsub('%s+$', '')
  local opening, closing = text:sub(1, 1), ')'
  if text:sub(1, 3) == '（' then opening, closing = '（', '）' end
  if opening ~= '(' and opening ~= '（' then return nil end
  local number, ending = read_number(text, #opening + 1)
  if number and text:sub(ending + 1):match('^%s*' .. (closing == ')' and '%)' or closing) .. '$') then
    return number
  end
  return nil
end

--- Read only a standalone equation definition, retaining an existing eq: ID.
local function equation_target(block)
  if block.t ~= 'Para' and block.t ~= 'Plain' then return nil end
  local math, suffix = nil, {}
  for _, inline in ipairs(block.content) do
    if inline.t == 'Math' then
      if math then return nil end
      math = inline
    elseif inline.t == 'Space' or inline.t == 'SoftBreak' or inline.t == 'LineBreak' then
      if math then suffix[#suffix + 1] = ' ' end
    elseif math and inline.t == 'RawInline' and inline.format == 'markdown' then
      suffix[#suffix + 1] = inline.text
    elseif math and (inline.t == 'Str' or inline.t == 'Strong' or inline.t == 'Emph') then
      suffix[#suffix + 1] = pandoc.utils.stringify(inline)
    elseif inline.t ~= 'Span' or #inline.content ~= 0 or not inline.identifier:match('^_Ref') then
      return nil
    end
  end
  if not math then return nil end
  local text = table.concat(suffix)
  local identifier = text:match('{#(eq:[%w_:%.%-]+)}')
  if identifier then
    -- crossrefs.lua retains Unicode/descriptive suffixes after the ID when its
    -- manual-number check cannot remove them, so the ID may precede the number.
    local cleaned, count = text:gsub('%s*{#eq:[%w_:%.%-]+}', '')
    if count ~= 1 then return nil end
    text = cleaned
  end
  local number = equation_number(text)
  if not number and identifier and text:match('^%s*$') then
    number = equation_numbers[identifier]
  end
  if not number then return nil end
  return { kind = 'eq', number = number }, {
    t = 'Equation', identifier = identifier or '', math = math,
  }
end

--- Recognize a numbered caption only when the label starts its visible text.
local function caption_label(caption)
  local text = pandoc.utils.stringify(caption)
  local label = next_label(text, 1)
  if label and text:sub(1, label.first - 1):match('^%s*$') then
    label.original = text:sub(label.first, label.last)
    label.cut = label.last + #(text:sub(label.last + 1):match('^%s*'))
    return label
  end
  return nil
end

--- Remove a caption prefix across formatting runs while preserving empty bookmarks.
local function strip_caption_inlines(inlines, state)
  local result = {}
  for _, inline in ipairs(inlines) do
    local length = #pandoc.utils.stringify(inline)
    if state.remaining == 0 or length == 0 then result[#result + 1] = inline
    elseif state.remaining >= length then state.remaining = state.remaining - length
    elseif inline.t == 'Str' then
      result[#result + 1] = pandoc.Str(inline.text:sub(state.remaining + 1))
      state.remaining = 0
    elseif containers[inline.t] then
      local copy = inline:clone()
      copy.content = strip_caption_inlines(copy.content, state)
      if #copy.content > 0 then result[#result + 1] = copy end
    else result[#result + 1] = inline; state.remaining = 0 end
  end
  return result
end

--- Remove an original caption prefix without flattening the remaining formatting.
local function preserve_caption_number(block, owner, label)
  owner.attributes['original-number'] = label.original
  local state = { remaining = label.cut }
  if block.t == 'Figure' or block.t == 'Table' then
    local caption = block.caption.long
    for _, paragraph in ipairs(caption) do
      if paragraph.t == 'Plain' or paragraph.t == 'Para' then
        paragraph.content = strip_caption_inlines(paragraph.content, state)
      end
    end
    block.caption.long = caption
    if block.t == 'Figure' then
      -- Markdown's Figure writer falls back to HTML when Figure Attr carries
      -- custom attributes. Put a simple figure's caption and attributes on its
      -- single image so conversion keeps the requested ![caption](path){...} form.
      local image = block.content[1].content[1]
      local inlines = {}
      for _, paragraph in ipairs(caption) do
        for _, inline in ipairs(paragraph.content) do inlines[#inlines + 1] = inline end
      end
      image.caption = inlines
      image.identifier = owner.identifier
      for key, value in pairs(owner.attributes) do image.attributes[key] = value end
      if owner.t ~= 'Image' then
        for _, class in ipairs(owner.classes) do image.classes:insert(class) end
      end
      return pandoc.Para({ image })
    end
  elseif block.t == 'Div' then
    local caption = block.content[#block.content]
    caption.content = strip_caption_inlines(caption.content, state)
    block.content[#block.content] = caption
  else
    owner.caption = strip_caption_inlines(owner.caption, state)
    block.content = { owner }
  end
  return block
end

--- Return one image from a simple Figure or standalone image paragraph.
local function sole_image(block)
  if block.t == 'Figure' then
    if #block.content ~= 1 then return nil end
    block = block.content[1]
  end
  if (block.t ~= 'Para' and block.t ~= 'Plain') or #block.content ~= 1 then return nil end
  return block.content[1].t == 'Image' and block.content[1] or nil
end

--- Describe a numbered target and the element that owns its existing identifier.
local function target_for(block)
  if block.t == 'Div' and block.identifier:match('^fig:') then
    local caption = block.content[#block.content]
    local label = caption and caption_label({ caption })
    if label and label.kind == 'fig' then return label, block end
    return nil
  end
  if block.t == 'Table' then
    local label = caption_label(block.caption.long)
    if label and label.kind == 'tbl' then return label, block end
    return nil
  end
  local equation, owner = equation_target(block)
  if equation then return equation, owner end
  local image = sole_image(block)
  if not image then return nil end
  local caption = block.t == 'Figure' and block.caption.long or image.caption
  local label = caption_label(caption)
  if not label or label.kind ~= 'fig' then return nil end
  -- A Figure may own the ID itself, or its image may carry an earlier label.
  local owner = block.t == 'Figure' and (block.identifier ~= '' or image.identifier == '')
    and block or image
  return label, owner
end

--- Walk all targets once, skipping Figure internals to avoid duplicate images.
local function walk_targets(doc, callback)
  return doc:walk({
    traverse = 'topdown',
    -- A subfigure Div owns the final caption; its panels are separate targets
    -- only when they themselves have numbered captions, not (a)/(b) labels.
    Div = function(block)
      if block.identifier:match('^fig:') then
        return callback(block) or block, false
      end
    end,
    -- Figures own their captions and content; do not index their image twice.
    Figure = function(block) return callback(block) or block, false end,
    Table = callback,
    Para = callback,
    Plain = callback,
  })
end

--- Flatten text runs while using opaque barriers for links, citations and code.
local function inline_text(inline)
  if inline.t == 'Str' then return inline.text end
  if inline.t == 'Space' or inline.t == 'SoftBreak' then return ' ' end
  -- An existing cross-reference remains opaque but can carry a reference cue
  -- to a following typed label: "see [@fig:known] and Table 2".
  if inline.t == 'RawInline' and inline.format == 'markdown'
    and (inline.text:match('^%[@fig:[^%s%]]+%]$') or inline.text:match('^%[@tbl:[^%s%]]+%]$')
      or inline.text:match('^%[@eq:[^%s%]]+%]$')) then
    return '\1'
  end
  if inline.t == 'Cite' then
    for _, citation in ipairs(inline.citations) do
      if citation.id:match('^fig:') or citation.id:match('^tbl:') or citation.id:match('^eq:') then return '\1' end
    end
  end
  if inline.t == 'Link' and (inline.target:match('^#fig:') or inline.target:match('^#tbl:')
    or inline.target:match('^#eq:')) then return '\1' end
  if containers[inline.t] and (inline.t ~= 'Span' or inline.identifier == '') then
    local parts = {}
    for _, child in ipairs(inline.content) do parts[#parts + 1] = inline_text(child) end
    return table.concat(parts)
  end
  -- Opaque nodes cannot supply a label or cue, and are copied as whole nodes.
  return '\0'
end

--- Slice a prose interval without dropping formatting, attributes or opaque nodes.
local function slice_inlines(inlines, first, last)
  local result, offset = {}, 1
  if first > last then return result end
  for _, inline in ipairs(inlines) do
    local length = #inline_text(inline)
    local ending = offset + length - 1
    if first <= ending and last >= offset then
      if first <= offset and last >= ending then
        result[#result + 1] = inline
      elseif inline.t == 'Str' then
        result[#result + 1] = pandoc.Str(inline.text:sub(math.max(1, first - offset + 1), last - offset + 1))
      elseif containers[inline.t] then
        local copy = inline:clone()
        copy.content = slice_inlines(inline.content, math.max(1, first - offset + 1), last - offset + 1)
        if #copy.content > 0 then result[#result + 1] = copy end
      end
    end
    offset = ending + 1
  end
  return result
end

--- Recognize cues before a label, including lists beginning with a stable reference.
local function prefix_cue(before)
  for _, cue in ipairs({ '如', '见', '参见', '参考', '参照', '详见', '由', '根据' }) do
    if before:match(cue .. '%s*$') then return true end
  end
  for _, cue in ipairs({ 'see', 'refer to', 'shown in', 'show in', 'illustrated in',
    'listed in', 'given in', 'presented in', 'depicted in', 'according to', 'as in',
    'defined by', 'given by', 'using' }) do
    if before:match('%f[%a]' .. cue .. '%s*$') then return true end
  end
  local earlier, connector = before:match('^(.*)\1(.-)$')
  return earlier ~= nil and reference_connector(connector) and prefix_cue(earlier)
end

--- Require an explicit local reference cue so ordinary numbered mentions survive.
local function reference_cue(text, label)
  local before = text:sub(1, label.first - 1):lower()
  local after = text:sub(label.last + 1):lower()
  if prefix_cue(before) then return true end
  for _, cue in ipairs({ '所示', '所列', '所述', '显示', '展示', '列出' }) do
    if after:match('^%s*' .. cue) then return true end
  end
  return after:match('^%s+shows%f[%A]') ~= nil or after:match('^%s+illustrates%f[%A]') ~= nil
end

--- Replace only complete uniquely matched reference tokens inside a prose block.
local function rewrite_prose(block, targets, stats)
  local parts = {}
  for _, inline in ipairs(block.content) do parts[#parts + 1] = inline_text(inline) end
  local text, matches, cursor = table.concat(parts), {}, 1
  local previous, previous_reference = nil, false
  while cursor <= #text do
    local label = next_label(text, cursor)
    if not label then break end
    local target = targets[label.kind .. ':' .. label.number]
    local is_reference = reference_cue(text, label) or (previous_reference
      and reference_connector(text:sub(previous.last + 1, label.first - 1)))
    if target and target.id and is_reference then
      label.id = target.id
      matches[#matches + 1] = label
    end
    previous, previous_reference = label, is_reference
    cursor = label.last + 1
  end
  if #matches == 0 then return block end
  local result, kept_from = {}, 1
  for _, label in ipairs(matches) do
    for _, inline in ipairs(slice_inlines(block.content, kept_from, label.first - 1)) do
      result[#result + 1] = inline
    end
    result[#result + 1] = pandoc.RawInline('markdown', '[@' .. label.id .. ']')
    kept_from = label.last + 1
  end
  for _, inline in ipairs(slice_inlines(block.content, kept_from, #text)) do result[#result + 1] = inline end
  block.content = result
  stats.references = stats.references + #matches
  return block
end

--- Rewrite cell prose recursively while excluding a table's caption definition.
local function rewrite_table(table_element, filter)
  local rows = {}
  for _, row in ipairs(table_element.head.rows) do rows[#rows + 1] = row end
  for _, body in ipairs(table_element.bodies) do
    for _, row in ipairs(body.head) do rows[#rows + 1] = row end
    for _, row in ipairs(body.body) do rows[#rows + 1] = row end
  end
  for _, row in ipairs(table_element.foot.rows) do rows[#rows + 1] = row end
  for _, row in ipairs(rows) do
    for _, cell in ipairs(row.cells) do cell.contents = pandoc.Pandoc(cell.contents):walk(filter).blocks end
  end
  return table_element, false
end

--- Recover typed references after stable bookmark conversion without guessing duplicates.
function Pandoc(doc)
  local targets, ordered, used = {}, {}, {}
  local stats = { ids = 0, references = 0, ambiguous = 0 }
  equation_numbers = {}
  for _, record in ipairs(doc.meta['papper-equation-labels'] or {}) do
    local number = equation_number(pandoc.utils.stringify(record.label))
    if number then
      equation_numbers[pandoc.utils.stringify(record.id)] = number
    end
  end
  doc.meta['papper-equation-labels'], doc.meta['papper-fuzzy-crossrefs'] = nil, nil
  -- Reserve every authored identifier, including IDs outside numbered captions.
  doc:walk({
    -- Generic nodes expose attr only when their AST type carries identifiers.
    Block = function(element)
      if element.attr and element.identifier ~= '' then used[element.identifier] = (used[element.identifier] or 0) + 1 end
    end,
    Inline = function(element)
      if element.attr and element.identifier ~= '' then used[element.identifier] = (used[element.identifier] or 0) + 1 end
      -- Equation identifiers are raw Markdown after Math, rather than Math Attr.
      if element.t == 'RawInline' or element.t == 'Str' then
        local identifier = element.text:match('{#(eq:[%w_:%.%-]+)}')
        if identifier then used[identifier] = (used[identifier] or 0) + 1 end
      end
    end,
  })
  -- Index all targets before resolving duplicates, including forward references.
  walk_targets(doc, function(block)
    local label, owner = target_for(block)
    if not label then return nil end
    local key = label.kind .. ':' .. label.number
    if targets[key] then
      targets[key].duplicate = true
    else
      local target = { key = key, kind = label.kind, number = label.number, authored_id = owner.identifier }
      targets[key] = target
      ordered[#ordered + 1] = target
    end
  end)
  for _, target in ipairs(ordered) do
    if target.duplicate then
      stats.ambiguous = stats.ambiguous + 1
    elseif target.authored_id == '' then
      local base = target.kind .. ':fuzz-' .. target.number
      local id, suffix = base, 2
      while used[id] do id, suffix = base .. '-' .. suffix, suffix + 1 end
      used[id], target.id = 1, id
      stats.ids = stats.ids + 1
    elseif target.authored_id:sub(1, #target.kind + 1) == target.kind .. ':'
      and used[target.authored_id] == 1 then
      target.id = target.authored_id
    end
  end
  -- Assign the final ID only after uniqueness and authored-ID collisions are known.
  doc = walk_targets(doc, function(block)
    local label, owner = target_for(block)
    local target = label and targets[label.kind .. ':' .. label.number]
    if target and target.id and owner.t == 'Equation' then
      -- Only confirmed standalone numbered formulas are promoted. The authored
      -- number is intentionally discarded so pandoc-crossref sees a bare ID.
      block.content = { pandoc.Math('DisplayMath', owner.math.text),
        pandoc.RawInline('markdown', ' {#' .. target.id .. '}') }
      return block
    end
    if target and target.id then
      if owner.identifier == '' then owner.identifier = target.id end
      block = preserve_caption_number(block, owner, label)
      return block
    end
  end)
  local filter
  filter = {
    traverse = 'topdown',
    -- Caption definitions and headings never supply prose reference phrases.
    Figure = function(block) return block, false end,
      Div = function(block)
        if block.identifier:match('^fig:') then return block, false end
      end,
    Header = function(block) return block, false end,
    Table = function(block) return rewrite_table(block, filter) end,
    -- Process the whole paragraph once so a label split across Word runs matches.
    Para = function(block) return rewrite_prose(block, targets, stats), false end,
    Plain = function(block) return rewrite_prose(block, targets, stats), false end,
  }
  doc = doc:walk(filter)
  if stats.ids + stats.references + stats.ambiguous > 0 then
    io.stderr:write('[crossrefs-fuzz] generated ' .. stats.ids .. ' ID(s), converted '
      .. stats.references .. ' text reference(s), skipped ' .. stats.ambiguous .. ' ambiguous number(s)\n')
  end
  return doc
end
