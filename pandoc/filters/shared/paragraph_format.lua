-- Apply local paragraph formatting without creating named Word styles.
-- For example, ::: {fontSize="10pt" paragraphSpacing.after="6pt"} formats
-- each ordinary paragraph. Nested Divs inherit fields and override them locally.
-- Border shorthand such as borders="0.75pt solid #666666" uses the same scopes.
-- Rust validates the attributes and renders native Word properties or paragraph CSS.
-- Tables, figures, headings, code, display equations and note contents do not inherit
-- body formatting; an explicit local Div inside a table/note can set its own format.

local directory = pandoc.path.directory(PANDOC_SCRIPT_FILE)
local attributes = dofile(pandoc.path.join {directory, 'element_attributes.lua'})
local catalog = attributes.load(pandoc.path.join {directory, 'element-attributes.json'})
local roots = {}
for key in pairs(catalog.div.properties) do
  if key ~= 'custom-style' then roots[key:match('^[^.]+')] = true end
end

-- Escape marker text for a Word text node, including JSON strings containing markup.
local function xml_text(value)
  return value:gsub('&', '&amp;'):gsub('<', '&lt;'):gsub('>', '&gt;')
end

-- Keep only typography attributes; IDs, classes and unrelated attributes retain their meaning.
local function local_attributes(div)
  local attributes, consumed = {}, {}
  for key, value in pairs(div.attributes) do
    if roots[key:match('^[^.]+')] then
      attributes[key] = value
      consumed[#consumed + 1] = key
    end
  end
  for _, key in ipairs(consumed) do div.attributes[key] = nil end
  return attributes, #consumed > 0
end

-- Exclude display math and standalone images from ordinary paragraph formatting.
local function has_display_math(inlines)
  for _, inline in ipairs(inlines) do
    if inline.t == 'Math' and inline.mathtype == 'DisplayMath' then return true end
    if inline.t ~= 'Note' and inline.content and has_display_math(inline.content) then return true end
  end
  return false
end

-- Ignore note contents when deciding whether the containing prose paragraph is eligible.
local function ordinary_paragraph(block)
  return not has_display_math(block.content)
    and not (#block.content == 1 and block.content[1].t == 'Image')
end

-- Annotate one paragraph without wrapping it in a new style or changing its contents.
local function mark_paragraph(block, scopes, base)
  local payload = pandoc.json.encode(scopes)
  local marker
  if FORMAT == 'docx' then
    marker = pandoc.RawInline('openxml', '<w:r><w:rPr><w:vanish/></w:rPr><w:t>'
      .. xml_text('PMT_PARAGRAPH_FORMAT:' .. payload) .. '</w:t></w:r>')
  else
    marker = pandoc.Span({}, pandoc.Attr('', {}, {
      ['data-papper-paragraph-format'] = payload,
      ['data-papper-paragraph-base'] = base,
    }))
  end
  block.content:insert(1, marker)
  -- Tight list items need a real paragraph so HTML can apply paragraph spacing too.
  if block.t == 'Plain' and FORMAT ~= 'docx' then return pandoc.Para(block.content) end
  return block
end

-- Propagate scopes through prose containers while isolating table cells and figure content.
local function process_blocks(blocks, scopes, base)
  for index, block in ipairs(blocks) do
    if block.t == 'Div' then
      local attributes, present = local_attributes(block)
      local nested = scopes
      if present then
        nested = {}
        for _, scope in ipairs(scopes) do nested[#nested + 1] = scope end
        nested[#nested + 1] = attributes
      end
      block.content = process_blocks(block.content, nested, block.attributes['custom-style'] or base)
    elseif block.t == 'Para' or block.t == 'Plain' then
      if #scopes > 0 and ordinary_paragraph(block) then
        blocks[index] = mark_paragraph(block, scopes, base)
      end
    elseif block.t == 'BlockQuote' then
      block.content = process_blocks(block.content, scopes, 'Block Text')
    elseif block.t == 'BulletList' or block.t == 'OrderedList' then
      for item_index, item in ipairs(block.content) do
        block.content[item_index] = process_blocks(item, scopes, 'Compact')
      end
    elseif block.t == 'DefinitionList' then
      for _, item in ipairs(block.content) do
        for definition_index, definition in ipairs(item[2]) do
          item[2][definition_index] = process_blocks(definition, scopes, base)
        end
      end
    elseif block.t == 'Table' then
      local sections = {block.head, block.foot}
      for _, body in ipairs(block.bodies) do sections[#sections + 1] = body end
      for _, section in ipairs(sections) do
        local rows = {}
        for _, row in ipairs(section.head or {}) do rows[#rows + 1] = row end
        for _, row in ipairs(section.rows or section.body or {}) do rows[#rows + 1] = row end
        for _, row in ipairs(rows) do
          for _, cell in ipairs(row.cells) do cell.contents = process_blocks(cell.contents, {}, 'Table Text') end
        end
      end
    end
  end
  return blocks
end

-- Process body prose and independently authored note blocks for DOCX and HTML only.
function Pandoc(document)
  if FORMAT ~= 'docx' and not FORMAT:match('html') then return document end
  document.blocks = process_blocks(document.blocks, {}, 'Body Text')
  return document:walk({Note = function(note)
    note.content = process_blocks(note.content, {}, 'Footnote Text')
    return note
  end})
end
