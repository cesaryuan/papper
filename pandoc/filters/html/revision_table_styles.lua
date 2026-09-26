-- Add the DOCX revision character style to changed HTML table cells.
--
-- Pandoc preserves table metadata as attributes on the HTML table, but CSS
-- cannot select a numeric row or column from those values. This filter applies
-- the existing Revision Char style to the selected cells before HTML writing.

local revision_style = "Revision Char"

-- Read one revision attribute while accepting both underscore and hyphen keys.
local function attribute(attributes, key, alternate)
  return attributes[key] or (alternate and attributes[alternate])
end

-- Parse the same positive, 1-based indices accepted by the DOCX postprocessor.
local function selected_indices(value)
  local selected = {}
  value = tostring(value or ""):gsub("，", ","):gsub("；", ",")
  for token in value:gmatch("[^,%s]+") do
    local index = tonumber(token)
    if index and index >= 1 and index % 1 == 0 then
      selected[index] = true
    end
  end
  return selected
end

-- Apply the existing character style so the shared HTML CSS colors the cell.
local function mark_cell(cell)
  cell.attr = cell.attr or pandoc.Attr()
  cell.attr.attributes["custom-style"] = revision_style
end

-- Return rows for Pandoc's table head, body, and foot section variants.
local function section_rows(section)
  if section == nil then
    return {}
  end
  return section.rows or section.body or {}
end

-- Mark HTML table cells selected by revision_rows/revision_columns metadata.
function Table(table)
  local attributes = table.attributes or {}
  local rows_value = attribute(attributes, "revision_rows", "revision-rows")
  local columns_value = attribute(attributes, "revision_columns", "revision-columns")
  if rows_value == nil and columns_value == nil then
    return nil
  end

  local all_rows = {}
  for _, row in ipairs(section_rows(table.head)) do
    all_rows[#all_rows + 1] = row
  end
  for _, body in ipairs(table.bodies or {}) do
    for _, row in ipairs(section_rows(body)) do
      all_rows[#all_rows + 1] = row
    end
  end
  for _, row in ipairs(section_rows(table.foot)) do
    all_rows[#all_rows + 1] = row
  end

  local normalized_rows_value = tostring(rows_value or ""):match("^%s*(.-)%s*$")
  local normalized_columns_value = tostring(columns_value or ""):match("^%s*(.-)%s*$")
  local rows = normalized_rows_value == "*" and nil or selected_indices(rows_value)
  local columns = normalized_columns_value == "*" and nil or selected_indices(columns_value)
  local all_rows_selected = normalized_rows_value == "*"
  local all_columns_selected = normalized_columns_value == "*"

  for row_index, row in ipairs(all_rows) do
    for column_index, cell in ipairs(row.cells or {}) do
      if all_rows_selected or all_columns_selected
        or (rows and rows[row_index]) or (columns and columns[column_index]) then
        mark_cell(cell)
      end
    end
  end

  return table
end
