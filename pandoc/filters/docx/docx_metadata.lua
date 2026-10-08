-- Preserve DOCX-only metadata that Pandoc's writer cannot keep on its own.
--
-- This filter emits hidden WordprocessingML markers for:
-- 1. table attributes consumed by native DOCX postprocessing,
-- 2. revised native Word display equations marked with `revision=true`,
-- MathType source markers live in mathtype_markers.lua because they must run
-- before pandoc-crossref preserves display equations as inline DOCX math.

local table_marker_prefix = "PMT_TABLE_METADATA:"
local equation_marker_prefix = "PMT_EQUATION_METADATA:"
local captioned_table_index = 0

local supported_table_keys = {
  ["cell_margin"] = true,
  ["cell-margin"] = true,
  ["cell_margin_top"] = true,
  ["cell-margin-top"] = true,
  ["cell_margin_bottom"] = true,
  ["cell-margin-bottom"] = true,
  ["cell_margin_left"] = true,
  ["cell-margin-left"] = true,
  ["cell_margin_right"] = true,
  ["cell-margin-right"] = true,
  ["cell_spacing"] = true,
  ["cell-spacing"] = true,
  ["row_height"] = true,
  ["row-height"] = true,
  ["revision_columns"] = true,
  ["revision-columns"] = true,
  ["revision_rows"] = true,
  ["revision-rows"] = true,
  ["custom_text_style"] = true,
  ["custom-text-style"] = true,
  ["alignment"] = true,
  ["autofit"] = true,
}

local function xml_escape(text)
  return text
    :gsub("&", "&amp;")
    :gsub("<", "&lt;")
    :gsub(">", "&gt;")
end

local function hidden_paragraph_marker(prefix, record)
  local payload = prefix .. pandoc.json.encode(record)
  local xml = '<w:p>'
    .. '<w:pPr><w:rPr><w:vanish/><w:specVanish/></w:rPr></w:pPr>'
    .. '<w:r><w:rPr><w:vanish/><w:specVanish/></w:rPr>'
    .. '<w:t>' .. xml_escape(payload) .. '</w:t></w:r>'
    .. '</w:p>'
  return pandoc.RawBlock("openxml", xml)
end

local function normalize_table_key(key)
  return key:gsub("-", "_")
end

local function table_caption_text(table)
  if table.caption == nil then
    return ""
  end
  return pandoc.utils.stringify(table.caption)
end

local function exported_table_attributes(table)
  local attrs = {}
  for key, value in pairs(table.attributes or {}) do
    if supported_table_keys[key] then
      attrs[normalize_table_key(key)] = tostring(value)
    end
  end
  return attrs
end

local function has_table_attributes(attrs)
  for _, _ in pairs(attrs) do
    return true
  end
  return false
end

local function inline_contains_display_math(inline)
  if inline.t == "Math" then
    return inline.mathtype == "DisplayMath"
  end
  if inline.t ~= "Span" then
    return false
  end
  for _, child in ipairs(inline.content) do
    if inline_contains_display_math(child) then
      return true
    end
  end
  return false
end

-- Match display math in ordinary paragraphs and table-cell Plain blocks.
local function block_is_display_equation(block)
  if block.t ~= "Para" and block.t ~= "Plain" then
    return false
  end
  for _, inline in ipairs(block.content) do
    if inline_contains_display_math(inline) then
      return true
    end
  end
  return false
end

-- Mark the formula inside its cell after crossref creates an equation table.
local function mark_revised_equation(block)
  if block_is_display_equation(block) then
    return {
      hidden_paragraph_marker(equation_marker_prefix, { revision = "true" }),
      block,
    }
  end
end

function Table(table)
  if FORMAT ~= "docx" then
    return nil
  end

  local attrs = exported_table_attributes(table)
  if not has_table_attributes(attrs) then
    return nil
  end

  captioned_table_index = captioned_table_index + 1
  -- Figure writers turn separate child blocks into separate layout cells.
  -- Keep the marker and its table in one Div to avoid an empty column and shrunken images.
  return pandoc.Div({
    hidden_paragraph_marker(table_marker_prefix, {
      index = captioned_table_index,
      id = table.identifier or "",
      caption = table_caption_text(table),
      attributes = attrs,
    }),
    table,
  })
end

-- Consume revision wrappers and keep markers next to their nested formulas.
function Div(div)
  if FORMAT ~= "docx" then
    return nil
  end

  local revision_value = (div.attributes or {})["revision"]
  if revision_value == nil or revision_value:lower() ~= "true" then
    return nil
  end

  -- Numbered native equations use nested Divs and table cells by default.
  -- Looking only at immediate Para children silently lost their revision mark.
  return div:walk({ Para = mark_revised_equation, Plain = mark_revised_equation }).content
end
