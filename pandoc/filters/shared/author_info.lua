-- Render manuscript authors, shared affiliations, and correspondence before writing.
-- For example, authors: [{name: Alice, affiliations: [a], corresponding: true}]
-- with affiliations: {a: Example University} produces Alice with a superscript a,
-- an italic institution paragraph, and a contact note. DOCX uses native Notes so
-- correspondence and ordinary manuscript footnotes share Pandoc's safe allocation.
-- A hidden mark request in each correspondence Note lets DOCX packaging set its
-- custom * mark without inserting author content again or changing body notes.
-- Templates place the author content immediately below the title; fragment previews
-- omit metadata, and reply defaults omit this filter entirely.

-- Read metadata as plain text, retaining XML-sensitive characters for writer escaping.
local function text(value)
  if value == nil then return "" end
  if type(value) == "boolean" then return value and "True" or "False" end
  if pandoc.utils.type(value) == "Inlines" then
    -- Pandoc parses YAML strings as Markdown; stringify normally drops raw tags.
    -- Institution/contact text such as Lab <A> must retain those literal characters.
    value = pandoc.Span(value):walk({ RawInline = function(inline)
      return pandoc.Str(inline.text)
    end })
  end
  return pandoc.utils.stringify(value):match("^%s*(.-)%s*$")
end

-- Distinguish metadata lists from strings represented as lists of inline nodes.
local function is_list(value)
  return value ~= nil and pandoc.utils.type(value) == "List"
end

-- Generate alphabetic labels, including aa after z for large institution lists.
local function alpha(index)
  local label = ""
  while index > 0 do
    index = index - 1
    label = string.char(97 + index % 26) .. label
    index = math.floor(index / 26)
  end
  return label
end

-- Sort numeric keys numerically before alphabetic keys, preserving explicit labels.
local function sorted_keys(values)
  local keys = {}
  for key in pairs(values) do keys[#keys + 1] = key end
  table.sort(keys, function(left, right)
    local a, b = tonumber(left), tonumber(right)
    if a and b and a ~= b then return a < b end
    if a and not b then return true end
    if b and not a then return false end
    return left < right
  end)
  return keys
end

-- Resolve keyed and inline institutions once, without overwriting sparse explicit keys.
local function normalize(meta)
  local affiliations, by_text, authors = {}, {}, {}
  local raw = meta.affiliations or meta.affiliation
  if raw and pandoc.utils.type(raw) == "table" then
    for _, key in ipairs(sorted_keys(raw)) do
      local institution = text(raw[key])
      if institution ~= "" then
        affiliations[key], by_text[institution] = institution, key
      end
    end
  end
  local next_label = 1
  local raw_authors = meta.authors or meta.author
  if not is_list(raw_authors) then return authors, affiliations end
  for _, value in ipairs(raw_authors) do
    if pandoc.utils.type(value) == "table" then
      local author = { values = value, keys = {} }
      local institutions = value.affiliations or value.affiliation
      if not is_list(institutions) then institutions = { institutions } end
      for _, institution in ipairs(institutions) do
        local name = text(institution)
        if name ~= "" then
          local key = affiliations[name] and name or by_text[name]
          if not key then
            -- Sparse keyed metadata may already occupy the next alphabetic label.
            while affiliations[alpha(next_label)] do next_label = next_label + 1 end
            key = alpha(next_label)
            next_label = next_label + 1
            affiliations[key], by_text[name] = name, key
          end
          author.keys[#author.keys + 1] = key
        end
      end
      authors[#authors + 1] = author
    end
  end
  return authors, affiliations
end

-- Recognize boolean true or a nonempty custom correspondence sentence.
local function corresponding(author)
  local value = author.values.corresponding
  return value == true or (value ~= nil and value ~= false and text(value) ~= "")
end

-- Use the custom sentence verbatim or generate contact details from the first institution.
local function contact(author, affiliations)
  local values = author.values
  if values.corresponding ~= true then return text(values.corresponding) end
  local message = "Correspondence to: Dr. " .. text(values.name)
  if text(values.title) ~= "" then message = message .. " (" .. text(values.title) .. ")" end
  if affiliations[author.keys[1]] then message = message .. ", " .. affiliations[author.keys[1]] end
  if text(values.email) ~= "" then message = message .. ". E-mail: " .. text(values.email) .. "." end
  return message
end

-- Wrap a paragraph with a Word style and HTML class without embedding output XML.
local function paragraph(inlines, style, class)
  return pandoc.Div({ pandoc.Para(inlines) }, pandoc.Attr("", { class }, { ["custom-style"] = style }))
end

-- Export author content for the DOCX/HTML templates to place below the title.
function Pandoc(document)
  if FORMAT ~= "docx" and not FORMAT:match("html") then return nil end
  local authors, affiliations = normalize(document.meta)
  if #authors == 0 then return nil end
  local line, blocks, message = pandoc.List(), pandoc.List(), nil
  for index, author in ipairs(authors) do
    if index > 1 then line:insert(pandoc.Str(", ")) end
    line:insert(pandoc.Span({ pandoc.Str(text(author.values.name)) }, pandoc.Attr("", { "author-name" })))
    if #author.keys > 0 then line:insert(pandoc.Superscript({ pandoc.Str(table.concat(author.keys, ",")) })) end
    if corresponding(author) then
      local sentence = contact(author, affiliations)
      if FORMAT == "docx" then
        line:insert(pandoc.Note({ pandoc.Para({
          pandoc.RawInline("openxml", '<w:r><w:rPr><w:vanish/></w:rPr>'
            .. '<w:t>PMT_CORRESPONDENCE_MARK:*</w:t></w:r>'),
          pandoc.Span({ pandoc.Str(sentence) },
          pandoc.Attr("", {}, { ["custom-style"] = "Correspondence Text" })) }) }))
      else
        line:insert(pandoc.Superscript({ pandoc.Str("*") }))
        message = message or sentence
      end
    end
  end
  if FORMAT ~= "docx" then blocks:insert(paragraph(line, "Author", "author")) end
  for _, key in ipairs(sorted_keys(affiliations)) do
    blocks:insert(paragraph({ pandoc.Superscript({ pandoc.Str(key) }), pandoc.Space(),
      pandoc.Str(affiliations[key]) }, "Affiliation", "affiliation"))
  end
  if message then
    blocks:insert(paragraph({ pandoc.Superscript({ pandoc.Str("*") }), pandoc.Str(message) },
      "Correspondence", "corresponding-author"))
  end
  if FORMAT == "docx" then
    -- Pandoc collects DOCX footnotes before rendering arbitrary metadata fields.
    -- Its native author field registers Notes in time; a custom MetaBlocks field
    -- would emit dangling references with no corresponding footnotes package entry.
    document.meta.author = pandoc.MetaList({ pandoc.MetaInlines(line) })
    document.meta["papper-affiliations"] = pandoc.MetaBlocks(blocks)
  else
    document.meta.author = nil
    document.meta["papper-authors"] = pandoc.MetaBlocks({ pandoc.Div(blocks, pandoc.Attr("", { "papper-authors" })) })
  end
  return document
end
