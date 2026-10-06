-- Apply a shared TableSubfigure attribute to subfigure layout tables.
-- Run after pandoc-crossref, which marks grouped Figure nodes with the
-- subfigures class. Walking that Figure also styles tables nested in cells;
-- DOCX uses the reference table style; HTML keeps data-custom-style for CSS.

-- Select the shared table style while preserving other table attributes.
local function style_layout_table(tbl)
  tbl.attributes["custom-style"] = "TableSubfigure"
  return tbl
end

-- Style only tables contained in a crossref subfigure group.
function Figure(figure)
  if not figure.classes:includes("subfigures") then
    return nil
  end
  return figure:walk({ Table = style_layout_table })
end
