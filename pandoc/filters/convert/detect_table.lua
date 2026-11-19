-- Attach Word titles above tables to their numbered table. For example:
--   表 4-2 各事件样本数
--   Table 4-2 Sample counts by event
--   [table]
-- becomes one table with the Chinese title and caption-en="Sample counts by event".
-- Single-language titles continue to work. Keep unmatched titles and intervening
-- prose in place. Run after removing layout tables and before bookmark recovery.

local caption_text = dofile(pandoc.path.join {pandoc.path.directory(PANDOC_SCRIPT_FILE), 'caption_text.lua'})
local dashes = { '-', '‐', '‑', '‒', '–', '—', '−', '﹣', '－' }
local paired, bilingual, used, aliases = 0, 0, {}, {}

--- Read a number and the original byte boundary of the caption prefix.
local function title(block)
  if not block or (block.t ~= 'Para' and block.t ~= 'Plain') then return nil end
  local text = pandoc.utils.stringify(block.content):lower()
  local body = text:match('^%s*表%s*(.*)$')
  local language = body and 'zh' or 'en'
  body = body or text:match('^%s*table%.?%s*(.*)$') or text:match('^%s*tbl%.?%s*(.*)$')
  if not body then return nil end
  local number, suffix = body:match('^(%d+)(.*)$')
  local tail = (suffix or body):gsub('^%s+', '')
  for _, dash in ipairs(dashes) do
    if tail:sub(1, #dash) == dash then
      local part, rest = tail:sub(#dash + 1):match('^%s*(%d+)(.*)$')
      -- A blank Word chapter field can leave a leading dash and a figure/table index.
      if not part then return nil end
      number, suffix = (number or '') .. '-' .. part, rest
      break
    end
  end
  if not number then return nil end
  local delimiter = language == 'en' and '^%s*[%.:]?%s*' or '^%s*'
  return {number=number, language=language, inlines=block.content,
    prefix=#text - #suffix + #suffix:match(delimiter)}
end

--- Find an unambiguous Word cross-reference anchor without altering other spans.
local function bookmark(inlines)
  local found
  for _, inline in ipairs(inlines) do
    if inline.t == 'Span' and #inline.content == 0 and inline.identifier:match('^_Ref[%w_]+$') then
      if found and found.identifier ~= inline.identifier then return nil, false end
      found = inline
    end
  end
  return found, true
end

--- Word may detach the caption's bookmark into an otherwise empty paragraph.
local function preceding_bookmark(block)
  if block.t ~= 'Para' and block.t ~= 'Plain' then return nil end
  local anchor, valid = bookmark(block.content)
  if not valid or not anchor then return nil end
  for _, inline in ipairs(block.content) do
    if inline.t ~= 'Space' and inline.t ~= 'SoftBreak' and inline.identifier ~= anchor.identifier then return nil end
  end
  return anchor
end

--- Build one numbered table caption, retaining formatting and both languages' references.
local function attach_pair(tbl, first, second, detached)
  if first.language == second.language or tbl.attributes['caption-en'] then return false end
  local zh, en = first, second
  if zh.language ~= 'zh' then zh, en = en, zh end
  local missing = zh.number:sub(1, 1) == '-'
  if zh.number ~= en.number and not (missing and en.number:match('^%d+(%-%d+)$') == zh.number) then return false end
  if en.number:sub(1, 1) == '-' then return false end
  local primary_anchor, primary_valid = bookmark(zh.inlines)
  local english_anchor, english_valid = bookmark(en.inlines)
  if not primary_valid or not english_valid then return false end
  local translation = caption_text.drop_prefix(en.inlines, en.prefix)
  if not translation then return false end
  translation = translation:filter(function(inline)
    return not english_anchor or inline.identifier ~= english_anchor.identifier
  end)
  if not pandoc.utils.stringify(translation):match('%S') then return false end
  local primary = pandoc.List(zh.inlines)
  if missing then
    local rest = caption_text.drop_prefix(primary, zh.prefix)
    if not rest then return false end
    primary = pandoc.List{pandoc.Str('表'), pandoc.Space(), pandoc.Str(en.number), pandoc.Space()}
    primary:extend(rest)
  end
  local target = primary_anchor or detached or english_anchor
  if target then
    if not primary_anchor then primary:insert(1, target) end
    if english_anchor then aliases[english_anchor.identifier] = target.identifier end
    if detached then aliases[detached.identifier] = target.identifier end
  else
    if tbl.identifier == '' then
      local base = 'tbl:table-' .. en.number
      local id, suffix = base, 2
      while used[id] do id, suffix = base .. '-' .. suffix, suffix + 1 end
      tbl.identifier, used[id] = id, true
    elseif not tbl.identifier:match('^tbl:') then return false end
  end
  tbl.caption = pandoc.Caption({pandoc.Plain(primary)})
  tbl.attributes['caption-en'] = caption_text.markdown(translation)
  bilingual = bilingual + 1
  return true
end

--- Consider both adjacent titles before falling back to a single caption.
local function pair_blocks(blocks)
  local result, index = {}, 1
  while index <= #blocks do
    local detached = preceding_bookmark(blocks[index])
    local start = detached and index + 1 or index
    local first, next_block, after = title(blocks[start]), blocks[start + 1], blocks[start + 2]
    local second = first and title(next_block)
    local embedded = first and next_block and next_block.t == 'Table' and #next_block.caption.long == 1
      and title(next_block.caption.long[1])
    if first and after and after.t == 'Table' and #after.caption.long == 0 and not after.caption.short
      and second and attach_pair(after, first, second, detached) then
      result[#result + 1], index = after, start + 3
    elseif embedded and not next_block.caption.short and attach_pair(next_block, first, embedded, detached) then
      result[#result + 1], index = next_block, start + 2
    elseif not detached and first and first.number:sub(1, 1) ~= '-' and next_block and next_block.t == 'Table'
      and #next_block.caption.long == 0 and not next_block.caption.short then
      next_block.caption = pandoc.Caption({pandoc.Plain(first.inlines)})
      result[#result + 1], index = next_block, index + 2
      paired = paired + 1
    else
      result[#result + 1], index = blocks[index], index + 1
    end
  end
  return result
end

--- Reserve authored IDs and redirect translation bookmarks before Word reference recovery.
function Pandoc(doc)
  doc:walk({Table = function(tbl) used[tbl.identifier] = true end})
  doc = doc:walk({Blocks = pair_blocks})
  if paired + bilingual > 0 then
    io.stderr:write('[detect-table] paired ' .. paired .. ' single-language and ' .. bilingual .. ' bilingual table caption(s)\n')
  end
  return doc:walk({Link = function(link)
    local primary = aliases[link.target:match('^#(.+)$')]
    if primary then link.target = '#' .. primary; return link end
  end})
end
