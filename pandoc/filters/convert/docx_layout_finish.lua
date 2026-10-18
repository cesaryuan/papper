-- Publish imported page stories after image recovery and media renaming.
-- Global stories become papperSettings.docxHeader/docxFooter. Section stories
-- become Markdown attributes, e.g. footerDefault="- []{t='pageNum'} -".
-- Newlines in attributes use &#10; so multiline lists/tables remain valid.

local directory = pandoc.path.directory(PANDOC_SCRIPT_FILE)
local fields = dofile(pandoc.path.join {directory, 'docx_layout_fields.lua'})
local writer = 'markdown-simple_tables-multiline_tables'

--- Serialize story content without adding local paragraph-alignment blocks.
local function story_content(stories, id)
  if not id then return '' end
  local blocks = stories[id] or pandoc.Blocks {}
  local text = pandoc.write(pandoc.Pandoc(blocks), writer, {wrap_text='none'})
    :gsub('\r\n', '\n'):gsub('\r', '\n'):gsub('%s+$', '')
  -- A single paragraph is inline Markdown in the page-story reader. All other
  -- content needs a newline so a lone list or fenced Div is parsed as blocks.
  if #blocks ~= 1 or blocks[1].t ~= 'Para' then text = text ~= '' and text .. '\n' or '' end
  return text
end

--- Render one section's supported options in a stable, readable order.
local function section_fragment(section, index, manifest, stories)
  local options = {'t="sectionBreak"', 'kind=' .. fields.quote(index == 1 and 'continuous' or section.kind)}
  for _, key in ipairs({'pageStart', 'differentFirstPage', 'headerDistance', 'footerDistance'}) do
    if section[key] ~= nil then options[#options + 1] = key .. '=' .. fields.quote(section[key]) end
  end
  for _, kind in ipairs({'header', 'footer'}) do
    local alignment
    for _, suffix in ipairs({'Default', 'Even', 'First'}) do
      local key = kind .. suffix
      local id = section.stories[key]
      if id ~= nil then
        local definition = id and manifest.stories[tonumber(id)] or {alignment='left'}
        options[#options + 1] = key .. '=' .. fields.quote(story_content(stories, id))
        -- One alignment applies to all supplied variants. Prefer the normal
        -- variant, then even/first, without wrapping individual stories in Divs.
        alignment = alignment or definition.alignment
      end
    end
    if alignment then options[#options + 1] = kind .. 'Alignment=' .. fields.quote(alignment) end
  end
  return '[]{' .. table.concat(options, ' ') .. '}'
end

--- Detach imported stories and turn their boundaries into public Papper configuration.
function Pandoc(document)
  local metadata = document.meta['papper-convert-layout']
  if not metadata then return nil end
  document.meta['papper-convert-layout'] = nil
  local manifest = pandoc.json.decode(pandoc.utils.stringify(metadata), false)
  local stories, body, active = {}, pandoc.Blocks {}, nil
  for _, block in ipairs(document.blocks) do
    local token = (block.t == 'Para' or block.t == 'Plain') and pandoc.utils.stringify(block) or ''
    local record = manifest.records[token]
    if record and record.storyBegin then
      assert(not active, 'Nested imported page-story boundaries')
      active = record.storyBegin
      stories[active] = pandoc.Blocks {}
    elseif record and record.storyEnd then
      assert(active == record.storyEnd, 'Mismatched imported page-story boundaries')
      active = nil
    elseif active then stories[active]:insert(block)
    else body:insert(block) end
  end
  assert(not active, 'Unclosed imported page story')
  document.blocks = body
  local has_empty = false
  if manifest.initial or next(manifest.records) then
    local settings = {}
    for _, kind in ipairs({'header', 'footer'}) do
      local id = manifest.globals[kind]
      local definition = id and manifest.stories[tonumber(id)] or {alignment='left'}
      local content = story_content(stories, id)
      local metadata_content
      if content == '' then
        has_empty = true
        metadata_content = pandoc.MetaString(manifest.emptyToken)
      else
        metadata_content = pandoc.MetaInlines {pandoc.RawInline('markdown', content)}
      end
      settings['docx' .. kind:sub(1, 1):upper() .. kind:sub(2)] = pandoc.MetaMap {
        content=metadata_content,
        alignment=pandoc.MetaString(definition.alignment),
      }
    end
    document.meta.papperSettings = pandoc.MetaMap(settings)
  end
  document = document:walk({RawBlock=function(block)
    local record = manifest.records[block.text]
    if record and record.section then
      return pandoc.RawBlock('markdown',
        section_fragment(manifest.sections[record.section], record.section, manifest, stories))
    end
  end})
  if has_empty then
    -- Pandoc omits empty metadata strings entirely. A missing footer content
    -- inherits the bundled page number, so write the header with placeholders
    -- and replace only collision-free empty tokens with explicit YAML ''.
    local template = pandoc.template.compile(pandoc.template.default('markdown'))
    local header = pandoc.write(pandoc.Pandoc({}, document.meta), writer,
      {template=template, wrap_text='none'}):gsub(manifest.emptyToken, "''"):gsub('%s+$', '')
    document.meta = {}
    document.blocks:insert(1, pandoc.RawBlock('markdown', header))
  end
  return document
end
