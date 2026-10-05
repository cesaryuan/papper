-- Render caption-en attributes as a second caption sharing the primary number.
-- Run before crossref to preserve translated Markdown and mark its number template,
-- then after number normalization and before DOCX native field conversion.
-- Crossref sees only the primary caption, so translations never increment counters
-- or become duplicate list-of-figures/table entries.

local phase_key = 'pmt-bilingual-captions'
local number_class = 'pmt-bilingual-number'
local mirror_class = 'pmt-native-caption-reference'
local resources = dofile(pandoc.path.join {
  pandoc.path.directory(PANDOC_SCRIPT_FILE), 'filter_resources.lua',
})

--- Parse a caption attribute as one inline Markdown paragraph, rejecting block content.
local function translated_inlines(text, id)
  local blocks = pandoc.read(text, 'markdown').blocks
  if #blocks == 0 then return nil end
  if #blocks ~= 1 or (blocks[1].t ~= 'Para' and blocks[1].t ~= 'Plain') then
    error('[bilingual-captions] caption-en must contain one inline paragraph: ' .. id)
  end
  return blocks[1].content
end

--- Preserve an exact number boundary even when crossref joins its punctuation.
local function marked_template(value, fallback)
  if value == nil or type(value) == 'string' then
    local blocks = pandoc.read(value or fallback, 'markdown').blocks
    if #blocks > 1 or (#blocks == 1 and blocks[1].t ~= 'Para' and blocks[1].t ~= 'Plain') then
      error('[bilingual-captions] A caption template must contain one inline paragraph')
    end
    value = pandoc.MetaInlines(#blocks > 0 and blocks[1].content or {})
  end
  return pandoc.Pandoc({}, { template = value }):walk {
    Math = function(math)
      if math.text == 'i' then
        return pandoc.Span({ math }, pandoc.Attr('', { number_class }))
      end
    end,
  }.meta.template
end

--- Save translated AST by target ID and consume the public attributes before crossref.
local function prepare(doc)
  local captions = {}
  local kinds = {}

  --- Extract attributes from either a table or a figure's direct image.
  local function collect(element, kind)
    local text = element.attributes['caption-en']
    element.attributes['caption-en'] = nil
    if kind == 'fig' then
      for _, block in ipairs(element.content) do
        if block.t == 'Plain' or block.t == 'Para' then
          for _, inline in ipairs(block.content) do
            if inline.t == 'Image' then
              text = text or inline.attributes['caption-en']
              inline.attributes['caption-en'] = nil
            end
          end
        end
      end
    end
    if text == nil or text:match('^%s*$') then return element end
    local id = element.identifier
    if not id:match('^' .. kind .. ':') then
      error('[bilingual-captions] caption-en requires a ' .. kind .. ': identifier')
    end
    local translation = translated_inlines(text, id)
    if translation then
      if captions[id] then error('[bilingual-captions] Duplicate caption identifier: ' .. id) end
      captions[id] = pandoc.MetaMap {
        translation = pandoc.MetaInlines(translation),
        primary = pandoc.MetaBlocks(element.caption.long),
      }
      kinds[kind] = true
    end
    return element
  end

  doc = doc:walk {
    Figure = function(figure) return collect(figure, 'fig') end,
    Table = function(tbl) return collect(tbl, 'tbl') end,
  }
  -- Remaining attributes belong to inline images or grouped figures, not numbered objects.
  doc:walk {
    Image = function(image)
      local text = image.attributes['caption-en']
      if text and not text:match('^%s*$') then
        error('[bilingual-captions] caption-en requires an ordinary standalone numbered figure')
      end
    end,
    Div = function(div)
      local text = div.attributes['caption-en']
      if text and not text:match('^%s*$') then
        error('[bilingual-captions] caption-en on grouped/wrapping Divs is unsupported; attach it to a figure or table')
      end
    end,
  }
  if not next(captions) then return doc end
  if kinds.fig then
    doc.meta.figureTemplate = marked_template(doc.meta.figureTemplate,
      '$$figureTitle$$ $$i$$$$titleDelim$$ $$t$$')
  end
  if kinds.tbl then
    doc.meta.tableTemplate = marked_template(doc.meta.tableTemplate,
      '$$tableTitle$$ $$i$$$$titleDelim$$ $$t$$')
  end
  doc.meta[phase_key] = pandoc.MetaMap(captions)
  return doc
end

--- Keep both caption languages in the same semantic style and native container.
local function caption_block(content, kind, english)
  local class = english and 'caption-en' or 'caption-primary'
  if FORMAT == 'docx' then
    local style = kind == 'fig' and 'Image Caption' or 'Table Caption'
    return pandoc.Div({ pandoc.Para(content) },
      pandoc.Attr('', { class }, { ['custom-style'] = style, lang = english and 'en' or nil }))
  end
  return pandoc.Plain({ pandoc.Span(content,
    pandoc.Attr('', { class }, english and { lang = 'en' } or {})) })
end

--- Add translated captions from crossref's exact number without a second counter.
local function render(doc)
  local captions = doc.meta[phase_key]
  local rendered = {}
  local native = FORMAT == 'docx' and resources.boolean(resources.getenv('PMT_DOCX_NATIVE_CROSSREFS'))

  --- Assemble one caption and preserve direct image descriptions separately.
  local function append(element, kind)
    local stored = captions[element.identifier]
    if not stored then return nil end
    local numbers = {}
    pandoc.Div(element.caption.long):walk {
      Span = function(span)
        if span.classes:includes(number_class) then
          numbers[#numbers + 1] = pandoc.utils.stringify(span.content)
        end
      end,
    }
    -- Duplicate/omitted placeholders in custom templates cannot identify one number.
    if #numbers ~= 1 then
      error('[bilingual-captions] Bilingual captions require exactly one $$i$$ in the caption template: '
        .. element.identifier)
    end
    local primary = pandoc.Inlines {}
    for index, block in ipairs(element.caption.long) do
      if block.t ~= 'Plain' and block.t ~= 'Para' then
        error('[bilingual-captions] Unsupported primary caption block: ' .. element.identifier)
      end
      if index > 1 then primary:insert(pandoc.Space()) end
      primary:extend(block.content)
    end
    local number = pandoc.Inlines { pandoc.Str(numbers[1]) }
    if native then number = { pandoc.Span(number, pandoc.Attr('', { mirror_class })) } end
    local english = pandoc.Inlines { pandoc.Str(kind == 'fig' and 'Fig.' or 'Table'), pandoc.Space() }
    english:extend(number)
    english:insert(pandoc.Space())
    english:extend(stored.translation)
    element.caption.long = {
      caption_block(primary, kind, false), caption_block(english, kind, true),
    }
    rendered[element.identifier] = true
    if kind == 'fig' then
      -- Crossref copies numbered captions into alt text; restore the author's description.
      local description = pandoc.Inlines {}
      for _, block in ipairs(stored.primary) do
        if block.content then description:extend(block.content) end
      end
      for _, block in ipairs(element.content) do
        if block.t == 'Plain' or block.t == 'Para' then
          for _, inline in ipairs(block.content) do
            if inline.t == 'Image' then inline.caption = description end
          end
        end
      end
    end
    return element
  end

  doc = doc:walk {
    Figure = function(figure) return append(figure, 'fig') end,
    Table = function(tbl) return append(tbl, 'tbl') end,
  }
  for id, _ in pairs(captions) do
    if not rendered[id] then
      error('[bilingual-captions] Crossref removed the caption target; grouped subfigures are unsupported: ' .. id)
    end
  end
  doc.meta[phase_key] = nil
  if FORMAT:match('^html') then
    -- Emit CSS only for documents with translations, preserving existing single-caption output.
    local css = pandoc.MetaBlocks { pandoc.RawBlock('html', [[<style>
figcaption > .caption-primary, figcaption > .caption-en,
caption > .caption-primary, caption > .caption-en {
  display: block;
}
</style>]]) }
    local includes = doc.meta['header-includes']
    if includes == nil then includes = pandoc.MetaList {}
    elseif pandoc.utils.type(includes) ~= 'List' then includes = pandoc.MetaList { includes } end
    includes:insert(1, css)
    doc.meta['header-includes'] = includes
  end
  -- Unwrap markers everywhere, including crossref-generated lists and image alt text.
  return doc:walk {
    Span = function(span)
      if span.classes:includes(number_class) then return span.content end
    end,
  }
end

--- Run preparation and rendering as two ordered passes around crossref.
function Pandoc(doc)
  if doc.meta[phase_key] then return render(doc) end
  return prepare(doc)
end
