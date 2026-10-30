-- Insert DOCX-only layout and Word fields at empty t spans after manuscript numbering.
-- []{t="toc" title="Contents"}, []{t="listofFigures" includeSubfigures="true"},
-- and []{t="sectionBreak" pageStart="1" footer='[]{t="pageNum"}'} are standalone blocks.
-- Section stories are rendered by Pandoc and moved into native parts after global stories.
-- Native crossrefs use caption-label lists; static numbers and expanded panel lists
-- keep style selectors, with a bare \c to apply Word's Table of Figures style.
-- headerDefault/headerEven/headerFirst override the shared header independently;
-- headerDistance="3cm" and footerDistance="2cm" set the following section's edge distances.

local directory = pandoc.path.directory(PANDOC_SCRIPT_FILE)
local fields = dofile(pandoc.path.join {directory, 'docx_fields.lua'})
local helpers = dofile(pandoc.path.join {directory, 'native_field_helpers.lua'})
local escape = helpers.xml_escape
local block_types = {toc = true, listofFigures = true, listofTables = true, sectionBreak = true}
local section_count = 0
local initial_section = false
local stories = pandoc.Blocks {}
local lists = {}
local labels = {}
local native_lists = false

--- Require literal booleans for section linkage and subfigure selection
local function boolean(value, fallback, key)
  if value == nil then return fallback end
  if value ~= 'true' and value ~= 'false' then error('DOCX ' .. key .. ' must be true or false') end
  return value == 'true'
end

--- Serialize hidden boundary/configuration paragraphs for native package finalization
local function marker(text)
  return pandoc.RawBlock('openxml', '<w:p><w:r><w:rPr><w:vanish/></w:rPr><w:t>'
    .. escape(text) .. '</w:t></w:r></w:p>')
end

--- Emit a title outside heading outlines, followed by an updateable TOC field
local function contents(span)
  local kind, attrs = span.attributes.t, span.attributes
  fields.validate(span)
  local instruction, default_title
  if kind == 'toc' then
    local depth = attrs.depth or '3'
    if not depth:match('^[1-9]$') then error('DOCX toc depth must be between 1 and 9') end
    instruction, default_title = 'TOC \\o "1-' .. depth .. '" \\h \\z', 'Contents'
  else
    local include = kind == 'listofFigures' and boolean(attrs.includeSubfigures, false, 'includeSubfigures')
    local identifier = kind == 'listofTables' and 'T' or (include and 'S' or 'F')
    if native_lists and not include then
      local label = labels[identifier]
      instruction = 'TOC \\h \\z \\c "' .. label:gsub('\\', '\\\\'):gsub('"', '\\"') .. '"'
    else
      lists[identifier] = true
      local styles = kind == 'listofTables' and 'Papper Table Caption,1'
        or (include and 'Papper Figure Caption,1,Papper Subfigure Caption,2' or 'Papper Figure Caption,1')
      -- A bare \c selects Table of Figures formatting without requiring SEQ labels.
      instruction = 'TOC \\t "' .. styles .. '" \\h \\z \\c'
    end
    default_title = kind == 'listofTables' and 'List of Tables' or 'List of Figures'
  end
  local blocks = pandoc.Blocks {}
  local title = attrs.title or default_title
  if title ~= '' then
    blocks:insert(pandoc.Div({pandoc.Para({pandoc.Str(title)})},
      pandoc.Attr('', {}, {['custom-style'] = 'TOC Heading'})))
  end
  local field = helpers.field(instruction, {pandoc.Str('Update field in Word.')})
  field[1].text = field[1].text:gsub('w:fldCharType="begin"', 'w:fldCharType="begin" w:dirty="true"')
  local paragraph = pandoc.Para(field)
  blocks:insert(kind == 'toc' and paragraph or pandoc.Div({paragraph},
    pandoc.Attr('', {}, {['custom-style'] = 'table of figures'})))
  return blocks
end

--- Save options for the following section and render its independent page stories
local function section(span)
  fields.validate(span)
  local attrs = span.attributes
  local kind = attrs.kind or 'nextPage'
  if not ({nextPage = true, continuous = true, oddPage = true, evenPage = true})[kind] then
    error('Unsupported DOCX sectionBreak kind: ' .. kind)
  end
  if attrs.pageStart and (not attrs.pageStart:match('^%d+$') or tonumber(attrs.pageStart) > 2147483647) then
    error('DOCX sectionBreak pageStart must be an integer between 0 and 2147483647')
  end
  section_count = section_count + 1
  local record = {id = section_count, kind = kind, pageStart = attrs.pageStart,
    headerDistance = attrs.headerDistance, footerDistance = attrs.footerDistance}
  -- Convert uses a leading continuous break to configure the first section.
  -- There is no preceding body content to close, so avoid creating an empty section.
  record.initial = initial_section and section_count == 1
  if attrs.differentFirstPage ~= nil then
    record.differentFirstPage = boolean(attrs.differentFirstPage, false, 'differentFirstPage')
  elseif attrs.headerFirst ~= nil or attrs.footerFirst ~= nil then
    record.differentFirstPage = true
  end
  if record.differentFirstPage == false and (attrs.headerFirst ~= nil or attrs.footerFirst ~= nil) then
    error('DOCX first-page content conflicts with differentFirstPage=false')
  end
  for _, story in ipairs({'header', 'footer'}) do
    local has_content = false
    for _, suffix in ipairs({'', 'Default', 'Even', 'First'}) do
      has_content = has_content or attrs[story .. suffix] ~= nil
    end
    local link = boolean(attrs[story .. 'Link'], not has_content, story .. 'Link')
    if link and has_content then error('DOCX ' .. story .. ' content conflicts with ' .. story .. 'Link=true') end
    local alignment = attrs[story .. 'Alignment'] or 'center'
    if alignment ~= 'left' and alignment ~= 'center' and alignment ~= 'right' then
      error('DOCX ' .. story .. 'Alignment must be left, center, or right')
    end
    if attrs[story .. 'Alignment'] and not has_content then
      error('DOCX ' .. story .. 'Alignment requires ' .. story .. ' content')
    end
    record[story .. 'Link'] = link
    record[story .. 'Alignment'] = alignment
    for _, suffix in ipairs({'', 'Default', 'Even', 'First'}) do
      local key = story .. suffix
      if attrs[key] ~= nil then
        local boundary = 'SECTION_' .. section_count .. '_' .. key:upper()
        stories:insert(marker('PMT_DOCX_' .. boundary .. '_BEGIN'))
        stories:extend(fields.story(attrs[key]))
        stories:insert(marker('PMT_DOCX_' .. boundary .. '_END'))
        record[key] = true
      end
    end
  end
  return marker('PMT_DOCX_SECTION:' .. pandoc.json.encode(record))
end

--- Replace only standalone block directives, leaving inline fields for the second walk
local function paragraph(para)
  local span = #para.content == 1 and para.content[1]
  if not span or span.t ~= 'Span' or not block_types[span.attributes.t] then return nil end
  if span.attributes.t == 'sectionBreak' then return section(span) end
  return contents(span)
end

--- Render inline fields/page breaks and reject block directives embedded in prose
local function inline(span)
  local kind = span.attributes.t
  if kind == nil then return nil end
  if block_types[kind] then error('DOCX ' .. kind .. ' must occupy its own paragraph') end
  if kind == 'pageBreak' then
    fields.validate(span)
    return pandoc.RawInline('openxml', '<w:r><w:br w:type="page"/></w:r>')
  end
  local result = fields.span(span)
  if result then return result end
  error('Unsupported DOCX directive t="' .. kind .. '"')
end

--- Reject section breaks in containers whose paragraph placement Word cannot preserve
local function reject_nested_section(container)
  container:walk({Span = function(span)
    if span.attributes.t == 'sectionBreak' then
      error('DOCX sectionBreak must be a top-level body paragraph, outside tables, lists, quotes, and notes')
    end
  end})
end

--- Mark primary caption paragraphs for inherited styles used by style-based lists
local function caption_entry(element, identifiers)
  local last = element.caption.long[1]
  if not last then return element end
  while last.t == 'Div' do last = last.content[#last.content] end
  if last.t ~= 'Para' and last.t ~= 'Plain' then return element end
  for _, identifier in ipairs(identifiers) do
    if lists[identifier] then
      local kind = identifiers[1] == 'S' and 'S' or identifier == 'T' and 'T' or 'F'
      last.content:insert(pandoc.RawInline('openxml',
        '<w:r><w:rPr><w:vanish/></w:rPr><w:t>PMT_DOCX_CAPTION:'
        .. kind
        .. '</w:t></w:r>'))
      break
    end
  end
  return element
end

--- Process body directives and caption indexes before appending rendered section stories
function Pandoc(doc)
  if FORMAT ~= 'docx' then return nil end
  section_count, stories, lists = 0, pandoc.Blocks {}, {}
  labels = {F = helpers.sequence_name(doc.meta.figureTitle, 'Figure'),
    T = helpers.sequence_name(doc.meta.tableTitle, 'Table')}
  local native = (os.getenv('PMT_DOCX_NATIVE_CROSSREFS') or 'false'):lower()
  native_lists = native ~= 'false' and native ~= 'no' and native ~= '0'
  local first = doc.blocks[1]
  local first_span = first and first.t == 'Para' and #first.content == 1 and first.content[1]
  initial_section = first_span and first_span.t == 'Span'
    and first_span.attributes.t == 'sectionBreak' and first_span.attributes.kind == 'continuous' or false
  doc:walk({Table = reject_nested_section, BlockQuote = reject_nested_section,
    BulletList = reject_nested_section, OrderedList = reject_nested_section,
    DefinitionList = reject_nested_section, Note = reject_nested_section})
  doc = doc:walk({Para = paragraph, Plain = paragraph})
  doc = doc:walk({Span = inline})
  if next(lists) then
    local children = {}
    doc:walk({Figure = function(figure)
      if figure.classes:includes('subfigures') then
        figure:walk({Figure = function(child)
          if child.identifier ~= figure.identifier then children[child.identifier] = true end
        end})
      end
    end})
    doc = doc:walk({
      Figure = function(figure) return caption_entry(figure, children[figure.identifier] and {'S'} or {'F', 'S'}) end,
      Table = function(tbl)
        if tbl.attributes['custom-style'] ~= 'TableSubfigure' then return caption_entry(tbl, {'T'}) end
      end,
    })
  end
  doc.blocks:extend(stories)
  return doc
end
