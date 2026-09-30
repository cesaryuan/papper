-- Shared OpenXML field serialization and short bookmark names for DOCX filters
-- Loaded as a Lua module by the cross-reference and bibliography filters

local helpers = {}

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
