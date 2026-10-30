-- Shared OpenXML field serialization and short bookmark names for DOCX filters
-- Loaded as a Lua module by the cross-reference and bibliography filters

local helpers = {}

--- Use the same trimmed caption label in SEQ fields and native figure/table lists
function helpers.sequence_name(value, fallback)
  local text = pandoc.utils.stringify(value or fallback)
  local first, last
  for position, codepoint in utf8.codes(text) do
    -- YAML Markdown can retain boundary spaces as NBSP or ideographic spaces.
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

--- Escape field instructions and hidden marker text before embedding OpenXML
function helpers.xml_escape(text)
  return text:gsub('&', '&amp;'):gsub('<', '&lt;'):gsub('>', '&gt;')
end

--- Derive nine lowercase base32 characters from a build namespace and target index
function helpers.bookmark_suffix(namespace, index)
  local alphabet = 'abcdefghijklmnopqrstuvwxyz234567'
  local digest = pandoc.utils.sha1(namespace .. ':' .. index):sub(1, 18)
  return digest:gsub('%x%x', function(byte)
    -- Five bits per character avoid names differing only by letter case.
    local position = tonumber(byte, 16) % 32 + 1
    return alphabet:sub(position, position)
  end)
end

--- Emit a complex Word field with formatted cached results for immediate display
function helpers.field(instruction, result)
  -- Pandoc 3.12 prefixes legal Span bookmark names with _ to hide them in Word
  -- Raw OpenXML REF instructions bypass that writer, so match its target name
  -- Generated PapperRef_ identifiers use only ASCII letters, digits and underscores
  if PANDOC_VERSION >= pandoc.types.Version { 3, 12 } then
    instruction = instruction:gsub('^(REF%s+)(PapperRef_%w+)', '%1_%2')
  end
  local inlines = pandoc.Inlines {
    pandoc.RawInline('openxml',
      '<w:r><w:fldChar w:fldCharType="begin"/></w:r>' ..
      '<w:r><w:instrText xml:space="preserve"> ' ..
      helpers.xml_escape(instruction) .. ' </w:instrText></w:r>' ..
      '<w:r><w:fldChar w:fldCharType="separate"/></w:r>')
  }
  inlines:extend(result)
  inlines:insert(pandoc.RawInline('openxml',
    '<w:r><w:fldChar w:fldCharType="end"/></w:r>'))
  return inlines
end

return helpers
