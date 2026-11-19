-- Scan for typed figure/table/equation references after crossrefs.lua and only
-- apply the resulting plan with `papper convert input.docx --fuzzy-crossrefs`.
-- Word authors sometimes type
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

local numbers = dofile(pandoc.path.join {pandoc.path.directory(PANDOC_SCRIPT_FILE), 'reference_numbers.lua'})
local caption_text = dofile(pandoc.path.join {pandoc.path.directory(PANDOC_SCRIPT_FILE), 'caption_text.lua'})
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
      local number, ending = numbers.read(text, number_start)
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
    -- Accept an authored ID before or after the number; a Word bookmark may
    -- already have had its numeric suffix removed by crossrefs.lua.
    local cleaned, count = text:gsub('%s*{#eq:[%w_:%.%-]+}', '')
    if count ~= 1 then return nil end
    text = cleaned
  end
  local original = text:match('^%s*(.-)%s*$')
  local number = numbers.equation(original)
  local already_numbered = false
  if not number and identifier and text:match('^%s*$') then
    local record = equation_numbers[identifier]
    if record then
      number, original = record.number, record.original
      already_numbered = math.mathtype == 'DisplayMath'
    end
  end
  if not number then return nil end
  return { kind = 'eq', number = number, original = original, text = math.text,
    already_numbered = already_numbered }, {
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
    label.text = text
    label.caption_after = text:sub(label.cut + 1)
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

--- Visit a table's cells in reading order without treating its caption as body text.
local function walk_table_cells(element, filter)
  local row_index = 0
  local caption = pandoc.utils.stringify(element.caption.long)
  --- Recurse through each row with the cell location available for review.
  local function walk_rows(rows)
    for _, row in ipairs(rows) do
      row_index = row_index + 1
      for index, cell in ipairs(row.cells) do
        local location = { row = row_index, cell = index, caption = caption ~= '' and caption or nil }
        cell.contents = pandoc.Pandoc(cell.contents):walk(filter(location)).blocks
      end
    end
  end
  walk_rows(element.head.rows)
  for _, body in ipairs(element.bodies) do walk_rows(body.head); walk_rows(body.body) end
  walk_rows(element.foot.rows)
  return element, false
end

--- Walk all targets once, skipping Figure internals to avoid duplicate images.
local function walk_targets(doc, callback)
  --- Retain a table-cell address when collecting a nested target.
  local function filter(location)
    local visit = function(block) return callback(block, location) end
    return {
      traverse = 'topdown',
      -- The final caption owns a subfigure Div's figure number.
      Div = function(block)
        if block.identifier:match('^fig:') then return visit(block) or block, false end
      end,
      Figure = function(block) return visit(block) or block, false end,
      Table = function(block) return walk_table_cells(visit(block) or block, filter) end,
      Para = visit,
      Plain = visit,
    }
  end
  return doc:walk(filter())
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

--- Report explicit cues with their original context and apply the same accepted matches on request.
local function scan_prose(block, targets, report, apply, location)
  local parts = {}
  for _, inline in ipairs(block.content) do parts[#parts + 1] = inline_text(inline) end
  local text, matches, cursor = table.concat(parts), {}, 1
  local context
  local previous, previous_reference = nil, false
  while cursor <= #text do
    local label = next_label(text, cursor)
    if not label then break end
    local key = label.kind .. ':' .. label.number
    local target = targets[key]
    local is_reference = reference_cue(text, label) or (previous_reference
      and reference_connector(text:sub(previous.last + 1, label.first - 1)))
    if is_reference then
      context = context or caption_text.markdown(block.content)
      local accepted = target and target.status == 'available'
      local replacement = accepted and '[@' .. target.id .. ']' or nil
      report.references:insert({
        key = key, text = text:sub(label.first, label.last), context = context,
        status = accepted and (apply and 'applied' or 'proposed') or (target and target.status or 'missing-target'),
        replacement = replacement, table_cell = location,
      })
      if accepted then
        report.summary.reference_changes = report.summary.reference_changes + 1
        label.id = target.id
        matches[#matches + 1] = label
      end
    end
    previous, previous_reference = label, is_reference
    cursor = label.last + 1
  end
  if not apply or #matches == 0 then return block end
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
  return block
end

--- Match normalized number keys, existing IDs or the exact generated ID shown in the report.
local function exclude_targets(ordered)
  local path = os.getenv('PAPPER_CONVERT_CROSSREF_EXCLUSIONS_FILE')
  if not path then return end
  local file <close> = assert(io.open(path, 'rb'))
  local requested = pandoc.json.decode(assert(file:read('*a')))
  for _, selector in ipairs(requested) do
    local kind = selector:match('^(%a+):')
    local number, ending
    if kind then number, ending = numbers.read(selector, #kind + 2) end
    local key = number and ending == #selector and kind .. ':' .. number or selector
    local matched = false
    for _, target in ipairs(ordered) do
      local matches = target.key == key or target.id == selector
      if not matches then
        for _, definition in ipairs(target.definitions) do
          if definition.existing_id == selector then matches = true; break end
        end
      end
      if matches then
        target.status, target.action = 'excluded', nil
        matched = true
      end
    end
    if not matched then error('No fuzzy cross-reference target matches --fuzzy-crossrefs-exclude ' .. selector) end
  end
end

--- Scan and optionally apply a single plan after stable Word bookmark conversion.
function Pandoc(doc)
  local apply = os.getenv('PAPPER_CONVERT_CROSSREF_MODE') ~= 'scan'
  local report_path = os.getenv('PAPPER_CONVERT_CROSSREF_REPORT')
  local stats = { target_changes = 0, reference_changes = 0, ambiguous_targets = 0, excluded_targets = 0 }
  local report = { targets = pandoc.List(), references = pandoc.List(), summary = stats }
  local targets, ordered, used = {}, report.targets, {}
  equation_numbers = {}
  for _, record in ipairs(doc.meta['papper-equation-labels'] or {}) do
    local original = pandoc.utils.stringify(record.label):match('^%s*(.-)%s*$')
    local number = numbers.equation(original)
    if number then
      equation_numbers[pandoc.utils.stringify(record.id)] = { number = number, original = original }
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
  walk_targets(doc, function(block, location)
    local label, owner = target_for(block)
    if not label then return nil end
    local key = label.kind .. ':' .. label.number
    local target = targets[key]
    if not target then
      target = { key = key, definitions = pandoc.List(),
        action = label.kind ~= 'eq' and 'move-caption-number'
          or (not label.already_numbered and 'number-equation' or nil) }
      targets[key] = target
      ordered:insert(target)
    end
    target.definitions:insert({ original_number = label.original, text = label.text, table_cell = location,
      caption_after = label.caption_after, existing_id = owner.identifier ~= '' and owner.identifier or nil })
  end)
  for _, target in ipairs(ordered) do
    local existing = target.definitions[1].existing_id
    local kind, number = target.key:match('^(%a+):(.+)$')
    if #target.definitions > 1 then
      target.status, target.action = 'ambiguous', nil
      stats.ambiguous_targets = stats.ambiguous_targets + 1
    elseif not existing then
      local base = kind .. ':fuzz-' .. number
      local id, suffix = base, 2
      while used[id] do id, suffix = base .. '-' .. suffix, suffix + 1 end
      used[id], target.id, target.status = 1, id, 'available'
    elseif existing:sub(1, #kind + 1) == kind .. ':' and used[existing] == 1 then
      target.id, target.status = existing, 'available'
    else
      target.status, target.action = 'id-conflict', nil
    end
  end
  -- Allocate IDs before excluding anything so proposed IDs match on the next run.
  exclude_targets(ordered)
  for _, target in ipairs(ordered) do
    if target.action then stats.target_changes = stats.target_changes + 1 end
    if target.status == 'excluded' then stats.excluded_targets = stats.excluded_targets + 1 end
  end
  --- Inspect paragraph text once; captions and headings do not supply reference phrases.
  local function prose_filter(location)
    local function visit(block)
      return scan_prose(block, targets, report, apply, location), false
    end
    return {
      traverse = 'topdown',
      Figure = function(block) return block, false end,
      Div = function(block)
        if block.identifier:match('^fig:') then return block, false end
      end,
      Header = function(block) return block, false end,
      Table = function(block) return walk_table_cells(block, prose_filter) end,
      Para = visit,
      Plain = visit,
    }
  end
  doc = doc:walk(prose_filter())
  if apply then
    doc = walk_targets(doc, function(block)
      local label, owner = target_for(block)
      local target = label and targets[label.kind .. ':' .. label.number]
      if not target or not target.action then return nil end
      -- Existing targets with no inferred edit remain available, even when we use them in new references.
      target.status = 'applied'
      if owner.t == 'Equation' then
        block.content = { pandoc.Math('DisplayMath', owner.math.text),
          pandoc.RawInline('markdown', ' {#' .. target.id .. '}') }
        return block
      end
      if owner.identifier == '' then owner.identifier = target.id end
      return preserve_caption_number(block, owner, label)
    end)
  end
  if report_path then
    local file = assert(io.open(report_path, 'wb'))
    assert(file:write(pandoc.json.encode(report)))
    assert(file:close())
  end
  return doc
end
