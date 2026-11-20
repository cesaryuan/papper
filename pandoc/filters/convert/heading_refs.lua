-- Recover numbered Word heading references while the REF switches and bookmark
-- are still available. For example, REF ... \r \h with a result of 5.4.2
-- becomes [-@sec:case-study]. A REF to the heading text stays a normal link.

local directory = pandoc.path.directory(PANDOC_SCRIPT_FILE)
local xml = dofile(pandoc.path.join {directory, 'docx_layout_xml.lua'})
local heading_refs = {}

--- Index heading bookmarks by outline level, including inherited Word styles.
function heading_refs.bookmarks(body, outline_level)
  local bookmarks = {}
  --- Track the enclosing paragraph without letting nested paragraphs inherit its level.
  local function visit(node, level)
    if xml.is(node, 'del') or xml.is(node, 'moveFrom') then return end
    if xml.is(node, 'p') then level = outline_level(node) end
    if level and xml.is(node, 'bookmarkStart') then
      local name = xml.attr(node, 'name')
      if name then bookmarks[name] = level end
    end
    for _, child in ipairs(node.children) do
      if type(child) == 'table' then visit(child, level) end
    end
  end
  visit(body)
  return bookmarks
end

--- Select numbered REF forms that can be represented by a full section number.
function heading_refs.reference(record, bookmarks)
  local command, arguments = record.instruction:match('^%s*(%a+)%s+(.+)$')
  if not command or command:upper() ~= 'REF' then return nil end
  local bookmark, options = arguments:match('^"([^"]+)"(.*)$')
  if not bookmark then bookmark, options = arguments:match('^(%S+)(.*)$') end
  local level = bookmarks[bookmark]
  if not level then return nil end

  local numbering
  options = options:lower():gsub('\\%*%s+mergeformat%f[%s%z]', '')
    :gsub('\\%*%s+charformat%f[%s%z]', '')
  for option in options:gmatch('%S+') do
    if option == '\\n' or option == '\\r' or option == '\\w' then
      if numbering then return nil end
      numbering = option
    elseif option ~= '\\h' and option ~= '\\t' then
      return nil
    end
  end
  if not numbering then return nil end

  local before, number, after = record.result:match('^(%D*)(%d[%d.]*)(%D*)$')
  if not number or number:find('..', 1, true) then return nil end
  if number:sub(-1) == '.' then number, after = number:sub(1, -2), '.' .. after end
  -- Word can return a local number (e.g. 2 instead of 5.4.2). Pandoc's section
  -- reference emits the full hierarchy, so preserve local-only links as written.
  local _, separators = number:gsub('%.', '')
  if separators + 1 ~= level then return nil end
  return {headingRef=bookmark, before=before, after=after, result=record.result}
end

--- Append temporary hyperlinks so Pandoc resolves the original heading bookmarks
--- to exactly the same IDs it uses for ordinary links and TOC entries.
function heading_refs.probe(body, references, marker)
  local paragraph = xml.word(body, 'p')
  local tokens = {}
  for _, record in ipairs(references) do
    local bookmark = record.headingRef
    if not tokens[bookmark] then
      local token = marker({headingProbe=true})
      local link = xml.word(body, 'hyperlink', {xml.word(body, 'r', {xml.word(body, 't', {token})})})
      local prefix = link.name:match('^(.-):')
      xml.set(link, prefix .. ':anchor', bookmark)
      paragraph.children[#paragraph.children + 1] = link
      tokens[bookmark] = token
    end
    record.probe = tokens[bookmark]
  end
  return paragraph
end

--- Remove the probe, assign sec: IDs, and keep existing links pointing at their headings.
function heading_refs.resolve(document, references)
  local targets, headings, used_ids, renamed = {}, {}, {}, {}
  local probe = document.blocks:remove(#document.blocks)
  for _, inline in ipairs(probe.content) do
    if inline.t == 'Link' then targets[pandoc.utils.stringify(inline.content)] = inline.target end
  end
  document:walk({Header=function(header)
    local target = '#' .. header.identifier
    used_ids[target] = true
    if not header.classes:includes('unnumbered') then headings[target] = true end
  end})
  for _, record in ipairs(references) do
    local target = targets[record.probe] or '#' .. record.headingRef
    if headings[target] then
      local id = target:sub(2)
      if not id:match('^sec:') then
        local base = 'sec:' .. id
        id = renamed[target] or base
        if not renamed[target] then
          local serial = 1
          while used_ids['#' .. id] do
            serial = serial + 1
            id = base .. '-' .. serial
          end
          renamed[target], used_ids['#' .. id] = id, true
        end
      end
      record.secId = id
    else
      -- Some Word outline styles are imported as ordinary paragraphs by Pandoc.
      -- Keep their original link instead of emitting an unresolved section reference.
      record.link = target
    end
  end
  return document:walk({
    Header=function(header)
      header.identifier = renamed['#' .. header.identifier] or header.identifier
      return header
    end,
    Link=function(link)
      if renamed[link.target] then link.target = '#' .. renamed[link.target]; return link end
    end,
  })
end

return heading_refs
