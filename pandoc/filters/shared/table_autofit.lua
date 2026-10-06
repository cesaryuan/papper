-- Supply default table autofit attributes for both DOCX and HTML output.
-- Walk body blocks before pandoc-crossref, leaving metadata layout templates
-- and later-generated equation/subfigure tables alone. Papper
-- passes the top-level tableAutofit setting through PMT_TABLE_AUTOFIT; explicit
-- table attributes win and the none mode leaves unconfigured tables unchanged.

-- Add a writer-visible attribute without overriding an authored autofit mode.
local function default_autofit(tbl, mode)
  if tbl.attributes.autofit == nil then
    tbl.attributes.autofit = mode
  end
  return tbl
end

-- Read request-local settings before walking only tables authored in Markdown.
function Pandoc(document)
  local mode = pandoc.system.environment().PMT_TABLE_AUTOFIT or "window"
  if mode == "none" then
    return document
  end
  if mode ~= "window" and mode ~= "content" and mode ~= "fixed" then
    error("tableAutofit must be window, content, fixed, or none")
  end
  -- Walking the full document also reaches eqnBlockTemplate metadata tables,
  -- whose attributes crossref copies into generated equations. Restrict defaults to the body.
  document.blocks = pandoc.Pandoc(document.blocks):walk({
    -- Capture this request's mode without leaking it into a persistent worker's next build.
    Table = function(tbl) return default_autofit(tbl, mode) end,
  }).blocks
  return document
end
