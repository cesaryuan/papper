-- Import Word pagination and page stories before the ordinary Convert filters.
-- For example, a Word page break becomes []{t="pageBreak"}, and the following
-- section's footer becomes sectionBreak footerDefault="...". A temporary ZIP
-- is built entirely in memory and reread once with Pandoc, retaining formatted
-- paragraphs, tables, links, pictures, and math in headers and footers.
-- docx_layout_finish.lua serializes stories after media paths have been finalized.

local directory = pandoc.path.directory(PANDOC_SCRIPT_FILE)
local xml = dofile(pandoc.path.join {directory, 'docx_layout_xml.lua'})
local fields = dofile(pandoc.path.join {directory, 'docx_layout_fields.lua'})
local R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'

--- Resolve internal package targets relative to their source part.
local function resolve(part, target)
  local path = target:sub(1, 1) == '/' and target:sub(2)
    or (part:match('^(.*)/') or '') .. '/' .. target
  local components = {}
  for component in path:gmatch('[^/]+') do
    if component == '..' then table.remove(components)
    elseif component ~= '.' then components[#components + 1] = component end
  end
  return table.concat(components, '/')
end

--- Read a part's relationships without assuming a relationship namespace prefix.
local function relationships(part, entries)
  local folder, name = part:match('^(.*)/([^/]+)$')
  local source = entries[folder .. '/_rels/' .. name .. '.rels']
  local result = {}
  if not source then return result end
  for _, node in ipairs(xml.parse(source).children) do
    if type(node) == 'table' and xml.attr(node, 'Id') then result[xml.attr(node, 'Id')] = node end
  end
  return result
end

--- Index Word styles for inherited alignment and paragraph page-break properties.
local function styles_from(source)
  local result = {}
  if not source then return result end
  local root = xml.parse(source)
  for _, style in ipairs(root.children) do
    if xml.is(style, 'style') then
      result[xml.attr(style, 'styleId')] = style
      if xml.attr(style, 'type') == 'paragraph'
        and ({['1']=true, ['true']=true, on=true})[xml.attr(style, 'default') or ''] then
        result.default = style
      end
    end
  end
  result.defaults = xml.child(xml.child(xml.child(root, 'docDefaults'), 'pPrDefault'), 'pPr')
  return result
end

--- Resolve a paragraph property through its style ancestry and document defaults.
local function property(paragraph, name, styles)
  local props = xml.child(paragraph, 'pPr')
  local direct = xml.child(props, name)
  if direct then return direct end
  local style_node = xml.child(props, 'pStyle')
  local style = style_node and styles[xml.attr(style_node, 'val')] or styles.default
  local seen = {}
  while style and not seen[style] do
    seen[style] = true
    local value = xml.child(xml.child(style, 'pPr'), name)
    if value then return value end
    local parent = xml.child(style, 'basedOn')
    style = parent and styles[xml.attr(parent, 'val')] or nil
  end
  return xml.child(styles.defaults, name)
end

--- Read the first paragraph's alignment for the overall page-story setting.
local function alignment(paragraph, styles)
  local node = property(paragraph, 'jc', styles)
  local value = node and xml.attr(node, 'val') or 'left'
  value = ({start='left', ['end']='right', both='justify'})[value] or value
  if not ({left=true, center=true, right=true, justify=true, distribute=true})[value] then
    io.stderr:write('[convert] Unsupported page-story paragraph alignment ', value, '; using left\n')
    return 'left'
  end
  return value
end

--- Read active section properties in document order, excluding historical revisions.
local function collect_sections(body, rels, entries)
  local result = {}
  for index, node in ipairs(body.children) do
    local props = xml.is(node, 'sectPr') and node
      or (xml.is(node, 'p') and xml.child(xml.child(node, 'pPr'), 'sectPr'))
    if props then
      local type_node, number = xml.child(props, 'type'), xml.child(props, 'pgNumType')
      local kind = type_node and xml.attr(type_node, 'val') or 'nextPage'
      if not ({nextPage=true, continuous=true, oddPage=true, evenPage=true})[kind] then
        io.stderr:write('[convert] Unsupported section start ', kind, '; using nextPage\n')
        kind = 'nextPage'
      end
      local record = {ending=index, kind=kind, stories={}, refs={},
        differentFirstPage=tostring(xml.enabled(xml.child(props, 'titlePg'))),
        format=number and xml.attr(number, 'fmt') or 'decimal'}
      record.pageStart = number and xml.attr(number, 'start') or nil
      for _, story in ipairs({'header', 'footer'}) do
        record.refs[story] = {}
        for _, ref in ipairs(props.children) do
          if xml.is(ref, story .. 'Reference') then
            local rel = rels[xml.attr(ref, 'id')]
            local part = rel and resolve('word/document.xml', xml.attr(rel, 'Target') or '')
            assert(part and entries[part], 'Cannot resolve DOCX ' .. story .. ' reference')
            record.refs[story][xml.attr(ref, 'type') or 'default'] = part
          end
        end
        local margins = xml.child(props, 'pgMar')
        local distance = margins and tonumber(xml.attr(margins, story))
        if distance then record[story .. 'Distance'] = tostring(distance / 20) .. 'pt' end
      end
      result[#result + 1] = record
    end
  end
  return result
end

--- Insert a marker run after paragraph properties, keeping it outside hyperlinks.
local function insert_marker(paragraph, token)
  local text = xml.word(paragraph, 't', {token})
  local run = xml.word(paragraph, 'r', {text})
  local index = xml.is(paragraph.children[1], 'pPr') and 2 or 1
  table.insert(paragraph.children, index, run)
end

--- Create a standalone marker paragraph using the document's Word prefix.
local function marker_paragraph(context, token)
  local paragraph = xml.word(context, 'p')
  insert_marker(paragraph, token)
  return paragraph
end

--- Replace authored body/note page breaks before Pandoc discards their layout semantics.
local function prepare_paragraphs(root, styles, marker)
  xml.walk(root, function(node)
    if xml.is(node, 'br') and xml.attr(node, 'type') == 'page' then
      node.name = node.name:gsub('br$', 't')
      node.attrs, node.children, node.dirty = {}, {marker({inline='[]{t="pageBreak"}'})}, true
    elseif xml.is(node, 'p') and xml.enabled(property(node, 'pageBreakBefore', styles)) then
      insert_marker(node, marker({inline='[]{t="pageBreak"}'}))
    end
  end)
end

--- Split markers out of merged Str nodes without changing surrounding punctuation or emphasis.
local function recover_markers(document, prefix, records)
  document = document:walk({Str=function(str)
    if not str.text:find(prefix, 1, true) then return nil end
    local result, position = pandoc.Inlines {}, 1
    while true do
      local first, last = str.text:find(prefix .. '%d+END', position)
      if not first then break end
      if first > position then result:insert(pandoc.Str(str.text:sub(position, first - 1))) end
      local token = str.text:sub(first, last)
      local record = records[token]
      result:insert(record and record.inline and pandoc.RawInline('markdown', record.inline) or pandoc.Str(token))
      position = last + 1
    end
    if position <= #str.text then result:insert(pandoc.Str(str.text:sub(position))) end
    return result
  end})
  --- Restore block boundaries only after inline fields have been recovered.
  local function paragraph(para)
    local token = pandoc.utils.stringify(para)
    if records[token] and records[token].section then return pandoc.RawBlock('markdown', token) end
    return para
  end
  return document:walk({Para=paragraph, Plain=paragraph})
end

--- Rebuild only DOCX imports containing pagination or page stories.
function Pandoc(document)
  local source = PANDOC_STATE.input_files[1]
  if not source or not source:lower():match('%.docx$') then return nil end
  local original = pandoc.zip.Archive(pandoc.system.read_file(source, true))
  local entries, prefix = {}, 'PAPPERLAYOUT'
  for _, entry in ipairs(original.entries) do entries[entry.path] = entry:contents() end
  -- Avoid interpreting authored text as a marker, even in an imported story or note.
  local all_xml = {}
  for name, text in pairs(entries) do if name:match('%.xml$') then all_xml[#all_xml + 1] = text end end
  local authored = table.concat(all_xml)
  while authored:find(prefix, 1, true) do prefix = prefix .. 'X' end
  local records, serial = {}, 0
  --- Allocate markers shared by the preparation and final serialization passes.
  local function marker(record)
    serial = serial + 1
    local token = prefix .. serial .. 'END'
    records[token] = record
    return token
  end
  local root = xml.parse(assert(entries['word/document.xml'], 'DOCX document part is missing'))
  local body = assert(xml.child(root, 'body'), 'DOCX document body is missing')
  local rels = relationships('word/document.xml', entries)
  local sections = collect_sections(body, rels, entries)
  if #sections == 0 then return nil end
  local styles = styles_from(entries['word/styles.xml'])
  local settings = entries['word/settings.xml'] and xml.parse(entries['word/settings.xml'])
  local even = settings and xml.enabled(xml.child(settings, 'evenAndOddHeaders')) or false
  local manifest = {records=records, sections=sections, stories={}, globals={}}
  local appended, inherited, cache = {}, {header={}, footer={}}, {}
  local document_rels = xml.parse(assert(entries['word/_rels/document.xml.rels'], 'DOCX relationships are missing'))
  local relation_serial = 0
  local had_story = false

  --- Import one page story once per numbering format and remap its resource relationships.
  local function import_story(part, format)
    if not part then return false end
    if cache[part] then return cache[part] end
    local key = part .. ':' .. format
    if cache[key] then return cache[key] end
    local story = xml.parse(entries[part])
    local base_alignment = 'left'
    for _, child in ipairs(story.children) do
      if xml.is(child, 'p') then base_alignment = alignment(child, styles); break end
    end
    -- Import content without local formatting blocks; overall story settings
    -- support left/center/right, so other paragraph alignments fall back to left.
    if base_alignment ~= 'left' and base_alignment ~= 'center' and base_alignment ~= 'right' then base_alignment = 'left' end
    local story_rels, mapped = relationships(part, entries), {}
    for id, relation in pairs(story_rels) do
      repeat relation_serial = relation_serial + 1 until not rels['rIdPapperLayout' .. relation_serial]
      local new_id = 'rIdPapperLayout' .. relation_serial
      xml.set(relation, 'Id', new_id)
      for prefix_name, uri in pairs(relation.ns) do
        xml.set(relation, prefix_name == '' and 'xmlns' or 'xmlns:' .. prefix_name, uri)
      end
      if xml.attr(relation, 'TargetMode') ~= 'External' then
        xml.set(relation, 'Target', '/' .. resolve(part, xml.attr(relation, 'Target')))
      end
      document_rels.children[#document_rels.children + 1] = relation
      mapped[id], rels[new_id] = new_id, relation
    end
    xml.walk(story, function(node)
      for name, value in pairs(node.attrs) do
        local attr_prefix = name:match('^(.-):')
        if attr_prefix and node.ns[attr_prefix] == R and mapped[value] then xml.set(node, name, mapped[value]) end
      end
    end)
    local sensitive
    story, sensitive = fields.prepare(story, format, true, function(fragment) return marker({inline=fragment}) end)
    local id = tostring(#manifest.stories + 1)
    local begin_token, end_token = marker({storyBegin=id}), marker({storyEnd=id})
    manifest.stories[#manifest.stories + 1] = {beginToken=begin_token, endToken=end_token, alignment=base_alignment}
    appended[#appended + 1] = marker_paragraph(body, begin_token)
    for _, child in ipairs(story.children) do
      if type(child) == 'table' then
        -- Namespace bindings inherited from a header/footer root must travel
        -- with the content when it is read as a temporary body story.
        for name, value in pairs(story.attrs) do
          if name == 'xmlns' or name:match('^xmlns:') then xml.set(child, name, value) end
        end
      end
      appended[#appended + 1] = child
    end
    appended[#appended + 1] = marker_paragraph(body, end_token)
    cache[key], had_story = id, true
    if not sensitive then cache[part] = id end
    return id
  end

  local previous_first = false
  local effective = {header={}, footer={}}
  for index, section in ipairs(sections) do
    for _, kind in ipairs({'header', 'footer'}) do
      local refs = section.refs[kind]
      for variant, part in pairs(refs) do inherited[kind][variant] = part end
      if index == 1 then
        manifest.globals[kind] = import_story(inherited[kind].default, section.format)
        effective[kind].default = manifest.globals[kind]
      elseif refs.default then
        section.stories[kind .. 'Default'] = import_story(refs.default, section.format)
      end
      if even and (index == 1 or refs.even) then
        section.stories[kind .. 'Even'] = import_story(inherited[kind].even, section.format)
      end
      if section.differentFirstPage == 'true' and (index == 1 or not previous_first or refs.first) then
        section.stories[kind .. 'First'] = import_story(inherited[kind].first, section.format)
      end
      -- An inherited PAGE field follows the new section's numbering format.
      -- Materialize it again when the format changes, retaining its visible value.
      if index > 1 and section.format ~= sections[index - 1].format then
        for variant, suffix in pairs({default='Default', even='Even', first='First'}) do
          if variant == 'default' or (variant == 'even' and even) or (variant == 'first' and section.differentFirstPage == 'true') then
            local id = import_story(inherited[kind][variant], section.format)
            if id ~= effective[kind][variant] then section.stories[kind .. suffix] = id end
          end
        end
      end
      for variant, suffix in pairs({default='Default', even='Even', first='First'}) do
        local id = section.stories[kind .. suffix]
        if id ~= nil then effective[kind][variant] = id end
      end
    end
    previous_first = section.differentFirstPage == 'true'
  end

  local rewritten, section_index = {}, 1
  local note_formats = {footnote={}, endnote={}}
  for index, node in ipairs(body.children) do
    if type(node) == 'table' and not xml.is(node, 'sectPr') then
      xml.walk(node, function(child)
        for _, kind in ipairs({'footnote', 'endnote'}) do
          if xml.is(child, kind .. 'Reference') then
            local id = xml.attr(child, 'id')
            if id then note_formats[kind][id] = sections[section_index].format end
          end
        end
      end)
      node = fields.prepare(node, sections[section_index].format, false,
        function(fragment) return marker({inline=fragment}) end)
      prepare_paragraphs(node, styles, marker)
    end
    rewritten[#rewritten + 1] = node
    if section_index < #sections and index == sections[section_index].ending then
      section_index = section_index + 1
      rewritten[#rewritten + 1] = marker_paragraph(body, marker({section=section_index}))
    end
  end
  for _, kind in ipairs({'footnote', 'endnote'}) do
    local part = 'word/' .. kind .. 's.xml'
    if entries[part] then
      local notes = xml.parse(entries[part])
      for index, note in ipairs(notes.children) do
        if xml.is(note, kind) then
          -- PAGE fields in notes use the section containing the note reference,
          -- including Roman-numbered preliminary matter.
          notes.children[index] = fields.prepare(note, note_formats[kind][xml.attr(note, 'id')] or 'decimal',
            false, function(fragment) return marker({inline=fragment}) end)
        end
      end
      prepare_paragraphs(notes, styles, marker)
      entries[part] = xml.write(notes)
    end
  end
  local first = sections[1]
  manifest.initial = had_story or #sections > 1 or first.pageStart ~= nil or first.differentFirstPage == 'true'
  if serial == 0 and not manifest.initial then return nil end
  manifest.emptyToken = marker({empty=true})
  local initial_token = manifest.initial and marker({section=1}) or nil
  -- Insert before the final sectPr rather than the last XML token: pretty
  -- printed packages can end their body with whitespace after that element.
  local combined, inserted = {}, false
  for _, node in ipairs(rewritten) do
    if xml.is(node, 'sectPr') then
      for _, story_node in ipairs(appended) do combined[#combined + 1] = story_node end
      inserted = true
    end
    combined[#combined + 1] = node
  end
  assert(inserted, 'DOCX final section properties are missing')
  body.children = combined
  entries['word/document.xml'], entries['word/_rels/document.xml.rels'] = xml.write(root), xml.write(document_rels)
  local zip_entries = {}
  for _, entry in ipairs(original.entries) do
    zip_entries[#zip_entries + 1] = pandoc.zip.Entry(entry.path, entries[entry.path], entry.modtime)
  end
  local parsed = pandoc.read(pandoc.zip.Archive(zip_entries):bytestring(), 'docx', PANDOC_READER_OPTIONS)
  for name, value in pairs(document.meta) do parsed.meta[name] = value end
  -- A leading Word paragraph marker interrupts Pandoc's Title/Author metadata
  -- recognition and duplicates that text in the body. Insert initial settings
  -- into the AST after the reader has extracted its normal metadata instead.
  if initial_token then parsed.blocks:insert(1, pandoc.Para {pandoc.Str(initial_token)}) end
  parsed = recover_markers(parsed, prefix, records)
  parsed.meta['papper-convert-layout'] = pandoc.MetaString(pandoc.json.encode(manifest))
  return parsed
end
