-- Fix legacy roman declarations and styled hats in inline/display math.
-- Scan TeX control sequences, comments and balanced groups, normalize \rm
-- scopes to \textrm, and move \hat inside a single math-font wrapper to avoid
-- disappearing hats in MathType previews. Authored Markdown is not rewritten.
-- Example: \hat{\mathbf{C}} becomes \mathbf{\hat{C}}.
-- Run with: pandoc input.md --lua-filter fix_math.lua -o output.docx

local style_commands = {
  mathbf = true, mathcal = true, mathbb = true, mathfrak = true,
  mathit = true, mathrm = true, mathsf = true, mathscr = true,
  mathtt = true, mathbfit = true, boldsymbol = true, bm = true,
}

-- Read a TeX control word or escaped symbol without splitting escaped braces.
local function read_command(text, position)
  local command = text:match("^\\([A-Za-z]+)", position)
  return command, position + (command and #command + 1 or 2)
end

-- Locate the end of one balanced group, ignoring escaped braces and comments.
local function group_end(text, position)
  local depth = 1
  position = position + 1
  while position <= #text do
    local character = text:sub(position, position)
    if character == "\\" then
      local _, next_position = read_command(text, position)
      position = next_position
    elseif character == "%" then
      position = (text:find("\n", position, true) or #text) + 1
    else
      if character == "{" then
        depth = depth + 1
      elseif character == "}" then
        depth = depth - 1
        if depth == 0 then return position end
      end
      position = position + 1
    end
  end
end

-- Skip spaces ignored between mathematical control words and their arguments.
local function skip_spaces(text, position)
  while text:sub(position, position):match("%s") do
    position = position + 1
  end
  return position
end

-- Move a hat through a complete chain of font wrappers in one pass.
local function wrap_hat(operand)
  local start = skip_spaces(operand, 1)
  local command, position = read_command(operand, start)
  if command and style_commands[command] then
    position = skip_spaces(operand, position)
    if operand:sub(position, position) == "{" then
      local closing = group_end(operand, position)
      -- Only a whole styled operand may move: extra terms or scripts outside
      -- that wrapper would otherwise change the hat's scope or font.
      if closing and skip_spaces(operand, closing + 1) > #operand then
        return "\\" .. command .. "{"
          .. wrap_hat(operand:sub(position + 1, closing - 1)) .. "}"
      end
    end
  end
  return "\\hat{" .. operand .. "}"
end

-- Fix hats recursively while preserving comments and rejecting malformed groups.
local function fix_hats(text)
  local output, position = {}, 1
  while position <= #text do
    local character = text:sub(position, position)
    if character == "\\" then
      local command, after_command = read_command(text, position)
      local opening = skip_spaces(text, after_command)
      if command == "hat" and text:sub(opening, opening) == "{" then
        local closing = group_end(text, opening)
        if closing == nil then return nil end
        local operand = fix_hats(text:sub(opening + 1, closing - 1))
        if operand == nil then return nil end
        output[#output + 1] = wrap_hat(operand)
        position = closing + 1
      else
        output[#output + 1] = text:sub(position, after_command - 1)
        position = after_command
      end
    elseif character == "{" then
      local closing = group_end(text, position)
      if closing == nil then return nil end
      local group = fix_hats(text:sub(position + 1, closing - 1))
      if group == nil then return nil end
      output[#output + 1] = "{" .. group .. "}"
      position = closing + 1
    elseif character == "}" then
      return nil
    elseif character == "%" then
      local ending = text:find("\n", position, true) or #text
      output[#output + 1] = text:sub(position, ending)
      position = ending + 1
    else
      output[#output + 1] = character
      position = position + 1
    end
  end
  return table.concat(output)
end

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
  local original = element.text
  if original:find("\\hat", 1, true) ~= nil then
    -- Leave unmatched braces to the normal writer instead of partially fixing them.
    element.text = fix_hats(original) or original
  end
  if element.text:find("\\rm", 1, true) ~= nil then
    element.text = normalize_roman(element.text)
  end
  if element.text ~= original then return element end
end
