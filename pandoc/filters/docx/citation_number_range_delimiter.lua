-- Read request-local Unicode environment values; Windows Lua os.getenv retains stale CRT values in a resident worker.
local request_environment = pandoc.system.environment()

-- Apply the Papper-configured delimiter to citeproc numeric citation ranges.
-- CSL requires collapsed citation-number ranges to use an en dash, so this
-- narrowly adjusts the rendered citation output after citeproc has run.

local EN_DASH = "–"
local delimiter = nil

local function trim(value)
  -- Keep metadata parsing predictable when YAML scalars include whitespace.
  return value:gsub("^%s+", ""):gsub("%s+$", "")
end

local function environment_string(name)
  -- Papper-owned filter settings stay outside the Pandoc metadata namespace.
  local value = request_environment[name]
  if value == nil then
    return nil
  end

  local text = trim(value)
  if text == "" then
    return nil
  end

  return text
end

local function numeric_inline_text(inline)
  -- Linked citations render numbers inside Link nodes when link-citations is true.
  if inline == nil then
    return nil
  end

  local text = nil
  if inline.t == "Link" then
    text = pandoc.utils.stringify(inline.content)
  elseif inline.t == "Str" then
    text = inline.text
  end

  if text ~= nil and text:match("^%d+$") then
    return text
  end

  return nil
end

local function replace_en_dash_ranges(text)
  -- Replace only digit-en-dash-digit ranges, preserving other dash usage.
  return text:gsub("(%d)" .. EN_DASH .. "(%d)", function(left, right)
    return left .. delimiter .. right
  end)
end

local function is_numeric_citation_payload(text)
  -- Covers unlinked citations that citeproc may render as a single Str.
  return text:match("^%[%d[%d%s,;%-" .. EN_DASH .. "]*%]$") ~= nil
end

local function has_str_at(inlines, index, value)
  -- Check bracket neighbors around split citation-number text.
  local inline = inlines[index]
  return inline ~= nil and inline.t == "Str" and inline.text == value
end

local function rewrite_split_numeric_payload(inlines, index)
  -- Covers split unlinked citations such as Str("[") Str("2–4") Str("]").
  local current = inlines[index]
  if current.t ~= "Str" then
    return
  end

  if has_str_at(inlines, index - 1, "[")
      and has_str_at(inlines, index + 1, "]")
      and current.text:match("^%d[%d%s,;%-" .. EN_DASH .. "]*$") then
    current.text = replace_en_dash_ranges(current.text)
  end
end

local function rewrite_linked_range_marker(inlines, index)
  -- Covers link-citations output: Link("2"), Str("–"), Link("4").
  local current = inlines[index]
  if current.t == "Str"
      and current.text == EN_DASH
      and numeric_inline_text(inlines[index - 1])
      and numeric_inline_text(inlines[index + 1]) then
    inlines[index] = pandoc.Str(delimiter)
  end
end

local function rewrite_citation_ranges(inlines)
  -- Walk inline runs after citeproc and update only numeric citation ranges.
  if delimiter == nil or delimiter == EN_DASH then
    return nil
  end

  for index, inline in ipairs(inlines) do
    if inline.t == "Str" and is_numeric_citation_payload(inline.text) then
      inline.text = replace_en_dash_ranges(inline.text)
    end

    rewrite_split_numeric_payload(inlines, index)
    rewrite_linked_range_marker(inlines, index)
  end

  return inlines
end

function Pandoc(doc)
  delimiter = environment_string("PMT_CITATION_NUMBER_RANGE_DELIMITER")
  if delimiter == nil then
    return nil
  end

  return doc:walk({
    Inlines = rewrite_citation_ranges,
  })
end
