-- Mark Pandoc's nested subfigure layout tables for neutral HTML styling.
--
-- DOCX subfigure cleanup removes the table style and cell margins from the
-- layout table produced by pandoc-crossref. HTML emits the same layout as a
-- table nested in `figure.subfigures`, so a dedicated class lets CSS apply the
-- equivalent reset without changing regular manuscript tables.

local subfigure_class = "subfigures"
local layout_table_class = "pmt-subfigure-table"

-- Return table rows across Pandoc table sections and AST field variants.
local function section_rows(section)
  if section == nil then
    return {}
  end
  return section.rows or section.body or {}
end

-- Return whether an Attr contains the requested CSS class.
local function has_class(attributes, class_name)
  for _, class in ipairs((attributes and attributes.classes) or {}) do
    if class == class_name then
      return true
    end
  end
  return false
end

-- Add one CSS class to an AST element while preserving its other attributes.
local function add_class(element, class_name)
  element.attr = element.attr or pandoc.Attr()
  if not has_class(element.attr, class_name) then
    element.attr.classes:insert(class_name)
  end
end

-- Mark nested tables in block containers, including tables inside table cells.
local function mark_layout_tables(blocks)
  for _, block in ipairs(blocks or {}) do
    if block.t == "Table" then
      add_class(block, layout_table_class)
      for _, row in ipairs(section_rows(block.head)) do
        for _, cell in ipairs(row.cells or {}) do
          mark_layout_tables(cell.contents)
        end
      end
      for _, body in ipairs(block.bodies or {}) do
        for _, row in ipairs(section_rows(body)) do
          for _, cell in ipairs(row.cells or {}) do
            mark_layout_tables(cell.contents)
          end
        end
      end
      for _, row in ipairs(section_rows(block.foot)) do
        for _, cell in ipairs(row.cells or {}) do
          mark_layout_tables(cell.contents)
        end
      end
    elseif block.t == "Figure" or block.t == "Div" or block.t == "BlockQuote" then
      mark_layout_tables(block.content)
    elseif block.t == "BulletList" or block.t == "OrderedList" then
      for _, item in ipairs(block.content or {}) do
        mark_layout_tables(item)
      end
    end
  end
end

-- Mark only the layout table belonging to a pandoc-crossref subfigure figure.
function Figure(figure)
  if not has_class(figure.attr, subfigure_class) then
    return nil
  end

  mark_layout_tables(figure.content)
  return figure
end
