-- Normalize Chinese section numbering in the Pandoc AST.
-- Enabled by PMT_CHINESE_MODE or Chinese `lang`; otherwise leaves the AST unchanged.
-- In level 2+ headings, change a leading nested number such as `3-1` to `3.1`.
-- In paragraphs, plain blocks, and line blocks, change `节 3-1` to `节 3.1`,
-- including linked references and non-breaking spaces. Figure, table, and
-- equation numbers such as `3-1` are left unchanged.

local enabled = false

-- Return whether an environment value represents an enabled flag.
local function is_truthy(value)
  if value == nil then
    return false
  end
  local text = tostring(value):lower()
  return text == "true" or text == "yes" or text == "1"
end

-- Return whether a metadata language value denotes Chinese text.
local function is_chinese_language(value)
  if value == nil then
    return false
  end
  local text = tostring(value):lower():gsub("_", "-")
  return text == "zh" or text == "zhcn" or text:match("^zh%-") ~= nil
end

-- Replace nested numeric hyphens with the dot notation used in Chinese text.
local function normalize_nested_number(text)
  return text:gsub("(%d+[%d%-]*%-%d+)", function(value)
    return value:gsub("%-", ".")
  end)
end

-- Normalize a section reference while preserving its surrounding text.
local function normalize_section_reference(text)
  -- Without links, crossref keeps the non-breaking space and number in one Str.
  local normalized = text:gsub("(节\194\160)(%d+[%d%-]*%-%d+)", function(prefix, number)
    return prefix .. normalize_nested_number(number)
  end)
  return normalized:gsub("(节%s+)(%d+[%d%-]*%-%d+)", function(prefix, number)
    return prefix .. normalize_nested_number(number)
  end)
end

-- Normalize the leading nested number in a level 2+ heading.
local function normalize_header_inlines(inlines)
  for _, inline in ipairs(inlines) do
    if inline.t == "Str" then
      -- Preserve the DOCX behavior: only a leading nested number in level 2+
      -- headings is normalized, so ordinary dates in a title stay unchanged.
      local number, suffix = inline.text:match("^(%d+[%d%-]*%-%d+)(.*)$")
      if number ~= nil and (suffix == "" or suffix:match("^[%s%.。]")) then
        inline.text = normalize_nested_number(number) .. suffix
        return inlines
      end
    elseif inline.content ~= nil then
      normalize_header_inlines(inline.content)
    end
  end
  return inlines
end

-- Pandoc-crossref may place a non-breaking space in a separate Str.
local function is_section_separator(inline)
  if inline == nil then
    return false
  end
  if inline.t == "Space" then
    return true
  end
  if inline.t ~= "Str" then
    return false
  end
  local without_nbsp = inline.text:gsub("\194\160", "")
  return without_nbsp:match("^%s*$") ~= nil
end

-- Match a section prefix even when crossref keeps the non-breaking space in it.
local function ends_with_section_prefix(text)
  local with_spaces = text:gsub("\194\160", " ")
  return with_spaces:match("节%s*$") ~= nil
end

-- Convert a section number inside plain text or a cross-reference link.
local function normalize_section_number_inline(inline)
  if inline == nil then
    return
  end
  if inline.t == "Str" then
    inline.text = normalize_nested_number(inline.text)
  elseif inline.t == "Link" then
    for _, child in ipairs(inline.content) do
      if child.t == "Str" then
        child.text = normalize_nested_number(child.text)
      end
    end
  end
end

-- Normalize section references in ordinary block inline content.
local function normalize_body_inlines(inlines)
  for index, inline in ipairs(inlines) do
    if inline.t == "Str" then
      inline.text = normalize_section_reference(inline.text)
    elseif inline.t == "Link" or inline.t == "Span" or inline.t == "Emph" or inline.t == "Strong" then
      inline.content = normalize_body_inlines(inline.content)
    end

    -- Crossref can attach a non-breaking space to `节` before the linked number.
    if inline.t == "Str" and ends_with_section_prefix(inline.text) then
      local number_index = index + 1
      if is_section_separator(inlines[number_index]) then
        number_index = number_index + 1
      end
      normalize_section_number_inline(inlines[number_index])
    end
  end
  return inlines
end

function Meta(meta)
  enabled = is_truthy(os.getenv("PMT_CHINESE_MODE"))
  if not enabled and meta.lang ~= nil then
    enabled = is_chinese_language(pandoc.utils.stringify(meta.lang))
  end
  return meta
end

function Pandoc(document)
  if not enabled then
    return document
  end

  return document:walk {
    Header = function(header)
      if header.level >= 2 then
        header.content = normalize_header_inlines(header.content)
      end
      return header
    end,
    Para = function(paragraph)
      paragraph.content = normalize_body_inlines(paragraph.content)
      return paragraph
    end,
    Plain = function(plain)
      plain.content = normalize_body_inlines(plain.content)
      return plain
    end,
    LineBlock = function(line_block)
      for index, line in ipairs(line_block.content) do
        line_block.content[index] = normalize_body_inlines(line)
      end
      return line_block
    end,
  }
end
