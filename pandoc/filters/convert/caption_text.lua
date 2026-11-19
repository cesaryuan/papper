-- Shared inline helpers for carrying Word caption text into Markdown attributes.
local M = {}

--- Remove a known prefix across formatted runs without flattening the rest of the caption.
function M.drop_prefix(inlines, remaining)
  local result = pandoc.List {}
  for _, inline in ipairs(inlines) do
    local length = #pandoc.utils.stringify(inline)
    if remaining == 0 or length == 0 then result:insert(inline)
    elseif remaining >= length then remaining = remaining - length
    elseif inline.t == 'Str' then
      result:insert(pandoc.Str(inline.text:sub(remaining + 1)))
      remaining = 0
    elseif inline.content then
      local copy = inline:clone()
      local content
      content, remaining = M.drop_prefix(copy.content, remaining)
      if not content then return nil end
      copy.content = content
      result:insert(copy)
    else
      return nil
    end
  end
  return result, remaining
end

--- Serialize inline formatting without folding text onto extra lines.
function M.markdown(inlines)
  return pandoc.write(pandoc.Pandoc{pandoc.Plain(inlines)}, 'markdown', {wrap_text='none'}):gsub('%s+$', '')
end

return M
