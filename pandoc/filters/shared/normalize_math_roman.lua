-- Normalize the legacy TeX \rm declaration in inline and display math.
-- Scan control sequences, comments and nested brace groups so \textrm{...}
-- covers the declaration's remaining group, rather than just its next token.
-- Run with: pandoc input.md --lua-filter normalize_math_roman.lua -o output.docx
-- Prose/code and other commands (including \rm-prefixed names) are preserved.

-- Wrap each declaration's scope while retaining existing TeX group boundaries.
local function normalize_roman(text)
  local output, scopes = {}, {0}
  local position = 1
  while position <= #text do
    local character = text:sub(position, position)
    if character == "\\" then
      local command = text:match("^\\([A-Za-z]+)", position)
      if command == "rm" then
        output[#output + 1] = "\\textrm{"
        scopes[#scopes] = scopes[#scopes] + 1
        position = position + 3
        -- TeX discards whitespace after a control word; do not turn it into text spaces.
        while text:sub(position, position):match("%s") do
          position = position + 1
        end
      else
        -- Consume control symbols together, so escaped braces/percent signs and
        -- a literal double backslash cannot change grouping or match \rm.
        local length = command ~= nil and #command + 1 or 2
        output[#output + 1] = text:sub(position, position + length - 1)
        position = position + length
      end
    elseif character == "%" then
      local newline = text:find("\n", position, true)
      output[#output + 1] = text:sub(position, newline or #text)
      -- A trailing comment must not swallow a newly inserted closing brace.
      if newline == nil and scopes[#scopes] > 0 then
        output[#output + 1] = "\n"
      end
      position = (newline or #text) + 1
    elseif character == "{" then
      output[#output + 1] = character
      scopes[#scopes + 1] = 0
      position = position + 1
    elseif character == "}" then
      if #scopes == 1 then
        return text
      end
      output[#output + 1] = string.rep("}", scopes[#scopes]) .. character
      scopes[#scopes] = nil
      position = position + 1
    else
      output[#output + 1] = character
      position = position + 1
    end
  end
  -- Leave malformed groups to Pandoc's normal error handling rather than repairing them.
  if #scopes ~= 1 then
    return text
  end
  output[#output + 1] = string.rep("}", scopes[1])
  return table.concat(output)
end

-- Normalize only math nodes, including formulas in metadata such as abstracts.
function Math(element)
  if element.text:find("\\rm", 1, true) ~= nil then
    element.text = normalize_roman(element.text)
    return element
  end
end
