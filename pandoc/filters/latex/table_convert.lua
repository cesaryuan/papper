-- Render Papper's table floats and two-column figures in Pandoc's Lua interpreter
-- The writer runs in-process: each figure no longer launches another Pandoc process
-- The deterministic tabular layout retains historical cell formatting and captions

local resource = dofile(pandoc.path.join {
  pandoc.path.directory(PANDOC_SCRIPT_FILE), '../shared/filter_resources.lua',
})

--- Preserve the previous ordered escaping of literal LaTeX cell text
local function literal(text)
  for _, pair in ipairs({{'\\', '\\textbackslash{}'}, {'_', '\\_'}, {'%%', '\\%'},
    {'%$', '\\$'}, {'#', '\\#'}, {'&', '\\&'}, {'{', '\\{'}, {'}', '\\}'}}) do
    text = text:gsub(pair[1], function() return pair[2] end)
  end
  return text
end

--- Retain formulas, emphasis, code and images in deterministic table cells
local function inlines_text(inlines)
  local parts = {}
  for _, inline in ipairs(inlines) do
    local kind, text = inline.tag, ''
    if kind == 'Str' then text = literal(inline.text)
    elseif kind == 'Space' then text = ' '
    elseif kind == 'Quoted' then text = '"' .. inlines_text(inline.content) .. '"'
    elseif kind == 'Strong' then text = '\\textbf{' .. inlines_text(inline.content) .. '}'
    elseif kind == 'Emph' then text = '\\textit{' .. inlines_text(inline.content) .. '}'
    elseif kind == 'Code' then text = '\\texttt{' .. inline.text .. '}'
    elseif kind == 'Math' then
      local delimiter = inline.mathtype == 'InlineMath' and '$' or '$$'
      text = delimiter .. inline.text .. delimiter
    elseif kind == 'Image' then text = '\\includegraphics[width=0.95\\linewidth]{' .. inline.src .. '}'
    elseif kind == 'RawInline' then text = inline.format == 'latex' and inline.text or ''
    else text = pandoc.utils.stringify(inline) end
    parts[#parts + 1] = text
  end
  return table.concat(parts)
end

--- Extract paragraph/plain cell blocks with the historical single-space join
local function cell_text(cell)
  local parts = {}
  for _, block in ipairs(cell.contents) do
    if block.tag == 'Para' or block.tag == 'Plain' then
      parts[#parts + 1] = inlines_text(block.content)
    end
  end
  return table.concat(parts, ' ')
end

--- Render cells in document order without mistaking table attributes for content
local function row_cells(row)
  local cells = {}
  for _, cell in ipairs(row.cells) do cells[#cells + 1] = cell_text(cell) end
  return cells
end

--- Strip crossref's historical four-inline generated caption prefix
local function strip_caption(caption)
  local text = pandoc.utils.stringify(caption.long)
  if text:sub(1, 7) == 'Figure ' or text:sub(1, 6) == 'Table ' then
    local first = caption.long[1]
    if first and first.content then
      local inlines = first.content
      for _ = 1, math.min(4, #inlines) do inlines:remove(1) end
      first.content = inlines
      caption.long[1] = first
    end
  end
end

--- Produce table/table* floats preserving caption, alignment and image column widths
function Table(element)
  if FORMAT ~= 'latex' then return nil end
  strip_caption(element.caption)
  local alignments, headers, rows = {}, {}, {}
  for _, column in ipairs(element.colspecs) do
    alignments[#alignments + 1] = column[1] == 'AlignRight' and 'r'
      or column[1] == 'AlignCenter' and 'c' or 'l'
  end
  if element.head.rows[1] then headers = row_cells(element.head.rows[1]) end
  for _, body in ipairs(element.bodies) do
    for _, row in ipairs(body.body) do rows[#rows + 1] = row_cells(row) end
  end
  if #headers == 0 and #rows == 0 then
    resource.warn('Empty table found, skipping')
    return element
  end
  local has_images = false
  for _, row in ipairs(rows) do
    for column, text in ipairs(row) do
      if text:find('\\includegraphics', 1, true) then
        has_images = true
        if alignments[column] then alignments[column] = 'm{2.8cm}' end
      end
    end
  end
  if #alignments == 0 then for _ = 1, #headers do alignments[#alignments + 1] = 'l' end end
  local environment = (#headers > 5 or has_images) and 'table*' or 'table'
  if element.attributes.twocol then
    environment = element.attributes.twocol == 'false' and 'table' or 'table*'
  end
  local lines = {'\\begin{' .. environment .. '}[htbp]', '\\centering',
    '\\caption{' .. pandoc.utils.stringify(element.caption.long) .. '}',
    '\\label{' .. element.identifier .. '}', '\\begin{tabular}{' .. table.concat(alignments, ' ') .. '}',
    '\\toprule'}
  if #headers > 0 then
    local styled = {}
    for _, header in ipairs(headers) do styled[#styled + 1] = '\\textbf{' .. header .. '}' end
    lines[#lines + 1] = table.concat(styled, ' & ') .. ' \\\\'
    lines[#lines + 1] = '\\midrule'
  end
  for _, row in ipairs(rows) do lines[#lines + 1] = table.concat(row, ' & ') .. ' \\\\' end
  lines[#lines + 1], lines[#lines + 2], lines[#lines + 3] = '\\bottomrule', '\\end{tabular}',
    '\\end{' .. environment .. '}'
  return pandoc.RawBlock('latex', table.concat(lines, '\n'))
end

--- Render wide figures in-process and retain the previous width of narrow figures
function Figure(element)
  if FORMAT ~= 'latex' then return nil end
  strip_caption(element.caption)
  local first
  element:walk {Image = function(image) if not first then first = image end end}
  if first and first.attributes.twocol == 'true' then
    local text = pandoc.write(pandoc.Pandoc {element}, 'latex')
      :gsub('\r\n', '\n'):gsub('\n$', ''):gsub('{figure}', '{figure*}')
    return pandoc.RawBlock('latex', text)
  end
  local changed = false
  return element:walk {Image = function(image)
    if not changed then image.attributes.width, changed = '95%', true end
    return image
  end}
end
