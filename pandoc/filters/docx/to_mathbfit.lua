-- Normalize DOCX bold mathematics inside Pandoc's existing Lua interpreter
-- Run with --lua-filter to_mathbfit.lua; prose and other TeX commands are retained

--- Replace only the historical brace-adjacent bold commands in math nodes
function Math(element)
  for _, command in ipairs({'boldsymbol', 'bm', 'symbf', 'mathbold', 'pmb', 'mathbfup'}) do
    element.text = element.text:gsub('\\' .. command .. '{', '\\mathbfit{')
  end
  return element
end
