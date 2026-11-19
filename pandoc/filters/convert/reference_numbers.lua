-- Share Word number recognition between bookmark cleanup and typed reference recovery.
local M = {}
local dashes = { '-', '‐', '‑', '‒', '–', '—', '−', '﹣', '－' }

--- Read an original-text number, retaining byte offsets across Unicode dashes.
function M.read(text, position)
  local first, ending = text:match('^%s*(%d+)()', position)
  if not first then return nil end
  local tail = text:sub(ending)
  local spaces = tail:match('^%s*')
  local number = first
  for _, dash in ipairs(dashes) do
    if tail:sub(#spaces + 1, #spaces + #dash) == dash then
      local second, second_end = text:match('^%s*(%d+)()', ending + #spaces + #dash)
      if not second then return nil end
      number, ending = first .. '-' .. second, second_end
      break
    end
  end
  -- Do not match a shorter label inside 1-14-2, 1.14 or an English word.
  tail = text:sub(ending)
  local trimmed = tail:gsub('^%s*', '')
  for _, dash in ipairs(dashes) do
    if trimmed:sub(1, #dash) == dash then return nil end
  end
  if tail:match('^%.%d') or tail:match('^[%w_]') then return nil end
  return number, ending - 1
end

--- Read a complete parenthesized formula number, including Word Unicode variants.
function M.equation(text)
  text = text:gsub('^%s+', ''):gsub('%s+$', '')
  local opening, closing = text:sub(1, 1), ')'
  if text:sub(1, 3) == '（' then opening, closing = '（', '）' end
  if opening ~= '(' and opening ~= '（' then return nil end
  local number, ending = M.read(text, #opening + 1)
  if number and text:sub(ending + 1):match('^%s*' .. (closing == ')' and '%)' or closing) .. '$') then
    return number
  end
  return nil
end

return M
