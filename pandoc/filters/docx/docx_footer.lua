-- Render configured Markdown with Pandoc's reader and the main DOCX writer.
-- For example, **Draft** - []{t="pageNum" format="upperRoman"} - becomes
-- formatted footer blocks with a dynamic Roman page number. Native processing
-- moves the marked blocks and their relationships out of the document body.
-- Append only after manuscript numbering/citations, but before image filters.

local directory = pandoc.path.directory(PANDOC_SCRIPT_FILE)
local resource = dofile(pandoc.path.join {directory, '../shared/filter_resources.lua'})

local formats = {
  decimal = 'Arabic', upperRoman = 'ROMAN', lowerRoman = 'roman',
  upperLetter = 'ALPHABETIC', lowerLetter = 'alphabetic',
  decimalEnclosedDash = 'ArabicDash', ordinal = 'Ordinal',
  cardinalText = 'CardText', ordinalText = 'OrdText', hex = 'Hex',
  Arabic = 'Arabic', ROMAN = 'ROMAN', roman = 'roman',
  ALPHABETIC = 'ALPHABETIC', alphabetic = 'alphabetic',
  ArabicDash = 'ArabicDash', Ordinal = 'Ordinal', CardText = 'CardText',
  OrdText = 'OrdText', Hex = 'Hex',
}

local first_values = {
  Arabic = '1', ROMAN = 'I', roman = 'i', ALPHABETIC = 'A', alphabetic = 'a',
  ArabicDash = '- 1 -', Ordinal = '1st', CardText = 'one', OrdText = 'first', Hex = '1',
}

--- Keep page placeholders as styled runs so enclosing bold/italic survives the DOCX writer
local function page_span(span)
  if span.attributes.t ~= 'pageNum' then return nil end
  if #span.content ~= 0 then
    error('docxFooter pageNum spans must be empty: []{t="pageNum"}')
  end
  for key, _ in pairs(span.attributes) do
    if key ~= 't' and key ~= 'format' then
      error('Unknown docxFooter pageNum attribute: ' .. key .. '; use format for numbering')
    end
  end
  local name = span.attributes.format
  local switch = name and formats[name] or ''
  if name and not formats[name] then
    error('Unsupported docxFooter pageNum format: ' .. name
      .. '; expected decimal, upperRoman, lowerRoman, upperLetter, lowerLetter, '
      .. 'decimalEnclosedDash, ordinal, cardinalText, ordinalText, hex, or a corresponding Word switch')
  end
  local record = pandoc.json.encode({switch = switch, result = first_values[switch] or '1'})
  return pandoc.Span({pandoc.Str('PMT_FOOTER_PAGE:' .. record)},
    pandoc.Attr('', {}, {['custom-style'] = 'page number'}))
end

--- Emit exact block boundaries that native processing removes before publishing
local function boundary(name)
  return pandoc.RawBlock('openxml', '<w:p><w:pPr><w:rPr><w:vanish/></w:rPr></w:pPr>'
    .. '<w:r><w:rPr><w:vanish/></w:rPr><w:t>PMT_DOCX_FOOTER_' .. name .. '</w:t></w:r></w:p>')
end

--- Reject note references because Word cannot display them in a footer story
local function reject_note()
  error('docxFooter does not support footnotes or endnotes; Word disallows notes in footers')
end

--- Keep footer content independent of manuscript citeproc and bibliography numbering
local function reject_citation()
  error('docxFooter does not support bibliography citations; use ordinary text or links')
end

--- Parse the unmodified configuration string independently of manuscript metadata and filters
function Pandoc(doc)
  if FORMAT ~= 'docx' then return nil end
  local path = resource.getenv('PMT_DOCX_FOOTER_CONTENT_PATH')
  if not path or path == '' then return nil end
  local content = assert(resource.read(path), 'Cannot read docxFooter content: ' .. path)
  -- Parse exactly like abstract metadata: a single-line scalar is inline Markdown,
  -- so the established '- []{t="pageNum"} -' remains decoration rather than a list.
  -- Multiline strings become block Markdown and may contain real lists or tables.
  local parsed = pandoc.read('---\nfooter: ' .. pandoc.json.encode(content) .. '\n---\n', 'markdown')
  local value = parsed.meta.footer
  local blocks = pandoc.utils.type(value) == 'Inlines' and {pandoc.Para(value)} or value
  local footer = pandoc.Pandoc(blocks):walk({
    Span = page_span,
    Note = reject_note,
    Cite = reject_citation,
  })
  doc.blocks:insert(boundary('BEGIN'))
  doc.blocks:insert(pandoc.Div(footer.blocks, pandoc.Attr('', {}, {['custom-style'] = 'Footer'})))
  doc.blocks:insert(boundary('END'))
  return doc
end
