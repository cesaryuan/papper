-- Native Word cross-references for Papper manuscript DOCX builds
-- The DOCX defaults load this file before and after pandoc-crossref
-- First pass: mark number placeholders in caption, section, and reference templates
-- Second pass: bookmark target numbers, then replace reference Links with REF fields
-- PMT_DOCX_NATIVE_CROSSREFS enables this filter only for manuscript DOCX builds
-- Enabled builds emit SEQ fields and export headings for native multilevel lists
-- Disabled builds leave the document and metadata unchanged
-- Cached field results preserve the initial display without requiring Word to open

local marker_class = 'pmt-native-number'
local child_marker_class = 'pmt-native-child-number'
local parent_ref_class = 'pmt-native-parent-reference'
local child_ref_class = 'pmt-native-child-reference'
local heading_title_class = 'pmt-native-heading-title'
local phase_key = 'pmt-native-crossrefs-prepared'
local equation_template = [[
+:------+:--------------------------------------------------:+--------:+
|       | $$t$$                                              | $$nmi$$ |
+-------+----------------------------------------------------+---------+
]]

--- Read boolean metadata from YAML booleans or Pandoc's string-valued -M option
local function enabled(value, fallback)
  if value == nil then return fallback end
  if type(value) == 'boolean' then return value end
  -- Repeating defaults metadata with -M makes a MetaList; the last value wins
  if pandoc.utils.type(value) == 'List' then
    return enabled(value[#value], fallback)
  end
  local text = pandoc.utils.stringify(value):lower()
  return text ~= 'false' and text ~= 'no' and text ~= '0'
end

--- Escape dynamic field instructions before embedding them in OpenXML
local function xml_escape(text)
  return text:gsub('&', '&amp;'):gsub('<', '&lt;'):gsub('>', '&gt;')
end

--- Use a caption title as the sequence name, trimming Unicode boundary whitespace
local function sequence_name(value, fallback)
  local text = pandoc.utils.stringify(value or fallback)
  local first, last
  for position, codepoint in utf8.codes(text) do
    -- Markdown metadata can preserve boundary spaces as NBSP or ideographic spaces.
    local whitespace = (codepoint >= 0x09 and codepoint <= 0x0D) or codepoint == 0x20
      or codepoint == 0x85 or codepoint == 0xA0 or codepoint == 0x1680
      or (codepoint >= 0x2000 and codepoint <= 0x200A) or codepoint == 0x2028
      or codepoint == 0x2029 or codepoint == 0x202F or codepoint == 0x205F or codepoint == 0x3000
    if not whitespace then
      first = first or position
      last = utf8.offset(text, 2, position) or (#text + 1)
    end
  end
  return first and text:sub(first, last - 1) or fallback
end

--- Quote sequence labels containing spaces or field syntax without changing them
local function field_argument(text)
  if text:match('^[%w_\128-\255]+$') then return text end
  return '"' .. text:gsub('\\', '\\\\'):gsub('"', '\\"') .. '"'
end

--- Return whether a Span carries the internal number marker
local function is_number_marker(span, class)
  return span.classes:includes(class or marker_class)
end

--- Report unsupported custom forms without discarding their original references
local function warn(message)
  io.stderr:write('[native-crossrefs] ' .. message .. '\n')
end

--- Keep routine field totals in verbose logs, matching Papper's logging setting
local function debug(message)
  if (os.getenv('PANDOC_TEMPLATE_LOG_LEVEL') or ''):upper() == 'DEBUG' then
    io.stderr:write('[native-crossrefs] ' .. message .. '\n')
  end
end

--- Emit a complex Word field while retaining Pandoc's formatted cached result
local function field(instruction, result)
  local inlines = pandoc.Inlines {
    pandoc.RawInline('openxml',
      '<w:r><w:fldChar w:fldCharType="begin"/></w:r>' ..
      '<w:r><w:instrText xml:space="preserve"> ' ..
      xml_escape(instruction) .. ' </w:instrText></w:r>' ..
      '<w:r><w:fldChar w:fldCharType="separate"/></w:r>')
  }
  inlines:extend(result)
  inlines:insert(pandoc.RawInline('openxml',
    '<w:r><w:fldChar w:fldCharType="end"/></w:r>'))
  return inlines
end

--- Parse string metadata into inline or block templates as crossref expects
local function parsed_template(value, fallback, blocks)
  if value == nil or type(value) == 'string' then
    local parsed = pandoc.read(value or fallback, 'markdown').blocks
    if blocks then return pandoc.MetaBlocks(parsed) end
    -- An empty custom caption template intentionally suppresses its visible title.
    if #parsed == 0 then return pandoc.MetaInlines {} end
    if #parsed ~= 1 or (parsed[1].t ~= 'Para' and parsed[1].t ~= 'Plain') then
      error('[native-crossrefs] An inline crossref template must contain one paragraph')
    end
    return pandoc.MetaInlines(parsed[1].content)
  end
  return value
end

--- Mark the i placeholder inside a template without guessing rendered numbers
local function marked_template(value, fallback, class, expression)
  local container = pandoc.Pandoc({}, { template = parsed_template(value, fallback, false) })
  return container:walk {
    Math = function(math)
      -- Number text can merge with titleDelim into one Str after crossref
      -- A Span preserves its exact boundary for a later bookmark
      if math.text == (expression or 'i') then
        return pandoc.Span({ math }, pandoc.Attr('', { class or marker_class }))
      end
    end
  }.meta.template
end

--- Prepare metadata before crossref substitutes caption and equation templates
local function prepare(doc)
  local meta = doc.meta
  meta.figureTemplate = marked_template(meta.figureTemplate,
    '$$figureTitle$$ $$i$$$$titleDelim$$ $$t$$')
  meta.tableTemplate = marked_template(meta.tableTemplate,
    '$$tableTitle$$ $$i$$$$titleDelim$$ $$t$$')
  meta.subfigureTemplate = marked_template(meta.subfigureTemplate,
    '$$figureTitle$$ $$i$$$$titleDelim$$ $$t$$. $$ccs$$')
  meta.subfigureChildTemplate = marked_template(meta.subfigureChildTemplate,
    '$$i$$', child_marker_class)
  meta.subfigureRefIndexTemplate = marked_template(meta.subfigureRefIndexTemplate,
    '$$i$$$$suf$$ ($$s$$)', parent_ref_class)
  meta.subfigureRefIndexTemplate = marked_template(meta.subfigureRefIndexTemplate,
    nil, child_ref_class, 's')
  meta.eqnIndexTemplate = marked_template(meta.eqnIndexTemplate, '($$i$$)')
  meta.secHeaderTemplate = marked_template(meta.secHeaderTemplate,
    '$$i$$$$secHeaderDelim[n]$$$$t$$')
  meta.secHeaderTemplate = marked_template(meta.secHeaderTemplate,
    nil, heading_title_class, 't')
  meta.eqnBlockTemplate = parsed_template(meta.eqnBlockTemplate, equation_template, true)
  local container = pandoc.Pandoc({}, { template = meta.eqnBlockTemplate })
  meta.eqnBlockTemplate = container:walk {
    Math = function(math)
      -- i becomes TeX/OMML and loses Span boundaries; nmi keeps a text number
      if math.text == 'i' then math.text = 'nmi'; return math end
    end
  }.meta.template
  meta.tableEqns = true
  meta[phase_key] = true
  return doc
end

--- Build number bookmarks first, then resolve both forward and backward Links
local function convert(doc)
  local targets, identifiers, counters, child_parents = {}, {}, {}, {}
  local next_bookmark, ref_count, seq_count, section_count = 0, 0, 0, 0
  local sequence_names = {
    fig = sequence_name(doc.meta.figureTitle, 'Figure'),
    tbl = sequence_name(doc.meta.tableTitle, 'Table'),
    eq = 'Equation',
  }
  -- Papper supplies a UUID namespace; standalone filter runs get their own fallback.
  local bookmark_namespace = os.getenv('PMT_DOCX_BOOKMARK_NAMESPACE')
    or pandoc.utils.sha1(tostring({}) .. os.time() .. os.clock()):sub(1, 24)
  local chapters = enabled(doc.meta.chapters, false)
  local chapter_level = tonumber(pandoc.utils.stringify(doc.meta.chaptersDepth or '1')) or 1
  local native_heading_levels = {}
  local name_in_link = enabled(doc.meta.nameInLink, false)

  --- Reserve source identifiers so generated bookmarks cannot reuse their names
  local function remember_identifier(element)
    identifiers[element.identifier] = true
  end

  --- Collect figure IDs and map each child panel to its parent figure
  local function remember_figure(figure)
    remember_identifier(figure)
    if figure.classes:includes('subfigures') then
      pandoc.Div(figure.content):walk {
        -- Child labels reference the parent's item number and their own letter.
        Figure = function(child) child_parents[child.identifier] = figure.identifier end,
      }
    end
  end

  doc:walk {
    Span = remember_identifier, Div = remember_identifier, Header = remember_identifier,
    Table = remember_identifier, Figure = remember_figure, Code = remember_identifier,
    CodeBlock = remember_identifier, Link = remember_identifier, Image = remember_identifier,
  }

  --- Allocate a short Word-safe bookmark name without colliding with source IDs
  local function bookmark_name()
    local name
    repeat
      next_bookmark = next_bookmark + 1
      -- Pandoc hashes Span identifiers starting with _, so use a letter here
      -- REF instructions must contain the exact bookmark name in the DOCX
      -- A 96-bit build namespace reduces collisions between independently built documents.
      name = ('PapperRef%s%06x'):format(bookmark_namespace, next_bookmark)
    until not identifiers[name]
    identifiers[name] = true
    return name
  end

  --- Build a SEQ and use the native chapter heading when its prefix is compatible
  local function sequence_content(kind, number, original, id)
    local prefix, digits = number:match('^(.-)(%d+)$')
    if not digits or (prefix ~= '' and not chapters) then
      warn('Keeping Pandoc numbering for ' .. id .. '; SEQ requires Arabic item numbers')
      return original
    end
    local value = tonumber(digits)
    local previous = counters[kind] or { prefix = prefix, value = 0 }
    local instruction = 'SEQ ' .. field_argument(sequence_names[kind]) .. ' \\* ARABIC'
    local chapter_number, chapter_delimiter = prefix:match('^(%d+)([^%d]+)$')
    local native_chapter = chapter_level == 1 and native_heading_levels[chapter_level]
      and chapter_number ~= nil
    if native_chapter then
      -- Word recomputes the restart after headings are inserted or moved.
      instruction = instruction .. ' \\s ' .. chapter_level
    end
    local expected = prefix ~= previous.prefix and 1 or previous.value + 1
    if value ~= expected or (not native_chapter and prefix ~= previous.prefix) then
      instruction = instruction .. ' \\r ' .. digits
    end
    counters[kind] = { prefix = prefix, value = value }
    local content = pandoc.Inlines {}
    if native_chapter then
      content:extend(field('STYLEREF ' .. chapter_level .. ' \\n \\t', { pandoc.Str(chapter_number) }))
      content:insert(pandoc.Str(chapter_delimiter))
    elseif prefix ~= '' then
      content:insert(pandoc.Str(prefix))
    end
    content:extend(field(instruction, prefix == '' and original or { pandoc.Str(digits) }))
    seq_count = seq_count + 1
    return content
  end

  --- Convert an unambiguous target number, leaving custom duplicate markers alone
  local function bookmark_number(element, id, kind)
    local count, result = 0, nil
    local class = kind == 'subfig' and child_marker_class or marker_class
    element:walk {
      -- Count before editing so ambiguous custom templates cannot emit partial fields.
      Span = function(span) if is_number_marker(span, class) then count = count + 1 end end,
    }
    if count ~= 1 then
      if count > 1 then warn('Ambiguous number template in ' .. id .. '; keeping its original numbering') end
      return element
    end
    local updated = element:walk {
      -- A single marker establishes the exact bookmark range independent of punctuation.
      Span = function(span)
        if not is_number_marker(span, class) then return nil end
        local number = pandoc.utils.stringify(span.content)
        local name = bookmark_name()
        local content = span.content
        if sequence_names[kind] then
          content = sequence_content(kind, number, content, id)
        elseif kind == 'sec' then
          section_count = section_count + 1
        end
        result = { name = name, number = number, switches = '\\h' }
        -- Pandoc allocates paired numeric IDs; Python randomizes them after writing.
        return pandoc.Span(content, pandoc.Attr(name))
      end
    }
    if result then targets[id] = result end
    return updated
  end

  --- Bookmark just the caption number while preserving the figure and its image
  local function convert_figure(figure)
    if not figure.identifier:match('^fig:') then return nil end
    local kind = child_parents[figure.identifier] and 'subfig' or 'fig'
    local caption = bookmark_number(pandoc.Div(figure.caption.long), figure.identifier, kind)
    figure.caption.long = caption.content
    -- Crossref also copies the caption into image alt text; do not duplicate SEQ
    figure.content = pandoc.Div(figure.content):walk {
      -- Clean image alt text only; nested child captions still need their markers.
      Image = function(image)
        image.caption = pandoc.Plain(image.caption):walk {
          -- Alternate descriptions must never contain duplicate SEQ/bookmark nodes.
          Span = function(span)
            if is_number_marker(span) or is_number_marker(span, child_marker_class) then
              return span.content
            end
          end,
        }.content
        return image
      end,
    }.content
    return figure
  end

  --- Bookmark the table caption number without changing body cells or attributes
  local function convert_table(tbl)
    if not tbl.identifier:match('^tbl:') then return nil end
    local caption = bookmark_number(pandoc.Div(tbl.caption.long), tbl.identifier, 'tbl')
    tbl.caption.long = caption.content
    return tbl
  end

  --- Bookmark the text number in a crossref-generated equation Div
  local function convert_equation(div)
    if not div.identifier:match('^eq:') then return nil end
    return bookmark_number(div, div.identifier, 'eq')
  end

  --- Export native outline settings and bookmark the title for REF paragraph numbers
  local function convert_header(header)
    local number_index, title_index
    local number_count, title_count = 0, 0
    for index, inline in ipairs(header.content) do
      if inline.t == 'Span' and is_number_marker(inline) then
        number_index, number_count = index, number_count + 1
      end
      if inline.t == 'Span' and inline.classes:includes(heading_title_class) then
        title_index, title_count = index, title_count + 1
      end
    end
    -- Unnumbered headings and levels beyond sectionsDepth have no number marker.
    if not number_index then return nil end
    local number = pandoc.utils.stringify(header.content[number_index].content)
    local level_count, start = 0, 1
    -- Convert the rendered numeric components to Word's level placeholders.
    local pattern = number:gsub('%d+', function(digits)
      level_count = level_count + 1
      start = tonumber(digits)
      return '%' .. level_count
    end)
    -- Duplicate placeholders cannot define a unique native paragraph-number range.
    if number_count ~= 1 or title_count ~= 1 or title_index <= number_index or header.level > 9
        or level_count ~= header.level or number:match('[^%d%.%-]') then
      warn('Custom heading numbering in ' .. header.identifier .. '; keeping Pandoc numbering')
      return bookmark_number(header, header.identifier, 'sec')
    end
    local prefix, gap = pandoc.Inlines {}, pandoc.Inlines {}
    for index = 1, number_index - 1 do prefix:insert(header.content[index]) end
    for index = number_index + 1, title_index - 1 do gap:insert(header.content[index]) end
    local delimiter = pandoc.utils.stringify(gap)
    local suffix = delimiter:match('%s$') and 'space' or 'nothing'
    pattern = pandoc.utils.stringify(prefix) .. pattern .. delimiter:gsub('%s+$', '')
    local name = bookmark_name()
    local title = pandoc.Inlines(header.content[title_index].content)
    for index = title_index + 1, #header.content do title:insert(header.content[index]) end
    local record = {
      level = header.level, number = number, start = start, pattern = pattern,
      suffix = suffix, depth = tonumber(pandoc.utils.stringify(doc.meta.sectionsDepth or '6')) or 6,
    }
    local payload = 'PMT_NATIVE_HEADING:' .. pandoc.json.encode(record)
    -- Python consumes this hidden run before the optional formatting/MathType steps.
    local marker = pandoc.RawInline('openxml',
      '<w:r><w:rPr><w:vanish/></w:rPr><w:t>' .. xml_escape(payload) .. '</w:t></w:r>')
    header.content = pandoc.Inlines { marker, pandoc.Span(title, pandoc.Attr(name)) }
    targets[header.identifier] = { name = name, number = number, switches = '\\r \\h' }
    native_heading_levels[header.level] = true
    section_count = section_count + 1
    return header
  end

  -- Collect all native headings before captions so chapter fields have complete context.
  doc = doc:walk { Header = convert_header }
  doc = doc:walk {
    traverse = 'topdown', Figure = convert_figure, Table = convert_table,
    Div = convert_equation,
  }

  --- Replace a trailing number while retaining formatting on named references
  local function replace_suffix(inlines, number, replacement)
    local last = inlines[#inlines]
    if not last then return nil end
    if last.t == 'Str' and last.text:sub(-#number) == number then
      local result = pandoc.Inlines(inlines)
      result:remove(#result)
      local prefix = last.text:sub(1, #last.text - #number)
      if prefix ~= '' then result:insert(pandoc.Str(prefix)) end
      result:extend(replacement)
      return result
    end
    if last.t == 'Span' or last.t == 'Emph' or last.t == 'Strong' or last.t == 'SmallCaps' then
      local replaced = replace_suffix(last.content, number, replacement)
      if replaced then
        last.content = replaced
        return inlines
      end
    end
    return nil
  end

  --- Resolve a subfigure reference through its parent's number and its own letter
  local function convert_child_link(link, id, target)
    local parent = targets[child_parents[id]]
    if not parent then return nil end
    local count = 0
    local updated = link:walk {
      -- Crossref reference-template markers preserve both components and formatting.
      Span = function(span)
        local destination
        if span.classes:includes(parent_ref_class) then destination = parent end
        if span.classes:includes(child_ref_class) then destination = target end
        if destination and pandoc.utils.stringify(span.content) == destination.number then
          count = count + 1
          return field('REF ' .. destination.name .. ' \\h', span.content)
        end
      end,
    }
    if count == 2 then
      ref_count = ref_count + count
      return updated.content
    end
    return nil
  end

  --- Convert numeric reference Links only after every destination is registered
  local function convert_link(link)
    local id = link.target:match('^#(.+)$')
    local kind = id and id:match('^(%a+):')
    if not sequence_names[kind] and kind ~= 'sec' then return nil end
    local target = targets[id]
    if not target then
      warn('No marked number for ' .. id .. '; keeping its Link')
      return nil
    end
    if child_parents[id] then
      local converted = convert_child_link(link, id, target)
      if converted then return converted end
      warn('Custom subfigure reference for ' .. id .. '; keeping its Link')
      return nil
    end
    if pandoc.utils.stringify(link.content) ~= target.number then
      -- nameInLink puts the prefix inside the Link; only its numeric suffix is a REF.
      if name_in_link then
        local replaced = replace_suffix(link.content, target.number,
          field('REF ' .. target.name .. ' ' .. target.switches, { pandoc.Str(target.number) }))
        if replaced then ref_count = ref_count + 1; return replaced end
      end
      warn('Custom reference text for ' .. id .. '; keeping its Link')
      return nil
    end
    ref_count = ref_count + 1
    return field('REF ' .. target.name .. ' ' .. target.switches, link.content)
  end

  doc = doc:walk { Link = convert_link }
  doc = doc:walk {
    -- Remove internal markers from skipped custom references and metadata templates.
    Span = function(span)
      if span.classes:includes(marker_class) or span.classes:includes(child_marker_class)
          or span.classes:includes(parent_ref_class) or span.classes:includes(child_ref_class)
          or span.classes:includes(heading_title_class) then
        return span.content
      end
    end,
  }
  doc.meta[phase_key] = nil
  debug(('Emitted %d REF fields, %d SEQ fields, %d section bookmarks')
    :format(ref_count, seq_count, section_count))
  return doc
end

--- Run the preparation and conversion phases only for the DOCX writer
function Pandoc(doc)
  if FORMAT ~= 'docx' or not enabled(os.getenv('PMT_DOCX_NATIVE_CROSSREFS'), false) then
    return nil
  end
  if doc.meta[phase_key] then return convert(doc) end
  return prepare(doc)
end
