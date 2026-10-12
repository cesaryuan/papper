-- Render configured Markdown with Pandoc's reader and the main DOCX writer.
-- For example, **Draft** - []{t="pageNum" format="upperRoman"} - becomes
-- formatted header/footer blocks with a dynamic Roman page number. Native processing
-- moves the marked blocks and their relationships out of the document body.
-- Append only after manuscript numbering/citations, but before image filters.

local directory = pandoc.path.directory(PANDOC_SCRIPT_FILE)
local resource = dofile(pandoc.path.join {directory, '../shared/filter_resources.lua'})
local fields = dofile(pandoc.path.join {directory, 'docx_fields.lua'})

local setting = "docxFooter"

--- Emit exact block boundaries that native processing removes before publishing
local function boundary(kind, name)
  return pandoc.RawBlock('openxml', '<w:p><w:pPr><w:rPr><w:vanish/></w:rPr></w:pPr>'
    .. '<w:r><w:rPr><w:vanish/></w:rPr><w:t>PMT_DOCX_' .. kind .. '_' .. name .. '</w:t></w:r></w:p>')
end

--- Reject note references because Word cannot display them in a header/footer story
local function reject_note()
  error(setting .. ' does not support footnotes or endnotes; Word disallows notes in headers/footers')
end

--- Keep page-story content independent of manuscript citeproc and bibliography numbering
local function reject_citation()
  error(setting .. ' does not support bibliography citations; use ordinary text or links')
end

--- Parse each story independently and append it for the shared DOCX writer
local function append_content(doc, kind, style)
  setting = 'docx' .. style
  local path = resource.getenv('PMT_DOCX_' .. kind .. '_CONTENT_PATH')
  if not path or path == '' then return end
  local content = assert(resource.read(path), 'Cannot read ' .. setting .. ' content: ' .. path)
  -- Parse exactly like abstract metadata: a single-line scalar is inline Markdown,
  -- so the established '- []{t="pageNum"} -' remains decoration rather than a list.
  -- Multiline strings become block Markdown and may contain real lists or tables.
  local parsed = pandoc.read('---\nfooter: ' .. pandoc.json.encode(content) .. '\n---\n', 'markdown')
  local value = parsed.meta.footer
  local blocks = pandoc.utils.type(value) == 'Inlines' and {pandoc.Para(value)} or value
  local footer = pandoc.Pandoc(blocks):walk({
    Span = function(span)
      local result = fields.span(span, setting)
      if span.attributes.t and not result then error('Unsupported ' .. setting .. ' directive: ' .. span.attributes.t) end
      return result
    end,
    Note = reject_note,
    Cite = reject_citation,
  })
  doc.blocks:insert(boundary(kind, 'BEGIN'))
  doc.blocks:insert(pandoc.Div(footer.blocks, pandoc.Attr('', {}, {['custom-style'] = style})))
  doc.blocks:insert(boundary(kind, 'END'))
end

--- Render configured page stories after manuscript filters and before image handling
function Pandoc(doc)
  if FORMAT ~= 'docx' then return nil end
  append_content(doc, 'HEADER', 'Header')
  append_content(doc, 'FOOTER', 'Footer')
  return doc
end
