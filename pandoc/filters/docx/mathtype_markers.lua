-- Preserve each formula's original LaTeX and inline/display context for DOCX.
--
-- This filter must run before pandoc-crossref. With `eqnBlockInlineMath: true`,
-- pandoc-crossref converts display equations to InlineMath for tab-based DOCX
-- layout, so a later filter can no longer recover the original math style.
-- The emitted hidden OpenXML run is consumed by the Python MathType pass and
-- is otherwise inert. Marker emission is enabled only for MathType builds.

local marker_prefix = "MTLATEX:"
local markers_enabled = (os.getenv("PMT_ENABLE_MATHTYPE_MARKERS") or ""):lower() == "true"

-- Escape formula source before embedding it in a WordprocessingML text node.
local function xml_escape(text)
  return text
    :gsub("&", "&amp;")
    :gsub("<", "&lt;")
    :gsub(">", "&gt;")
end

-- Build the hidden OpenXML run consumed by the Python MathType pass.
local function marker_run(latex, kind)
  -- A trailing TeX control-space (backslash + space) is significant source text.
  local marker = marker_prefix .. kind .. ":" .. latex
  local xml = table.concat({
    '<w:r>',
    '<w:rPr><w:vanish/></w:rPr>',
    '<w:t xml:space="preserve">',
    xml_escape(marker),
    '</w:t>',
    '</w:r>',
  })
  return pandoc.RawInline("openxml", xml)
end

-- Prefix one formula with a marker while its original math style is available.
local function mark_math(math)
  local kind = math.mathtype == "DisplayMath" and "display" or "inline"
  return { marker_run(math.text, kind), math }
end

-- Mark formulas in the user-facing abstract without walking crossref templates.
local function mark_abstract(meta)
  local abstract = meta.abstract
  local abstract_type = pandoc.utils.type(abstract)
  if abstract_type == "Blocks" or abstract_type == "Inlines" then
    meta.abstract = abstract:walk({ Math = mark_math })
  end
  return meta
end

-- Walk document content without interpreting math-like crossref metadata.
function Pandoc(document)
  if FORMAT ~= "docx" or not markers_enabled then
    return nil
  end

  -- Do not walk all metadata: pandoc-crossref's templates contain math-like
  -- placeholders that would become bogus formula markers.
  document.meta = mark_abstract(document.meta)

  -- Walk document blocks separately. Walking all metadata would interpret the
  -- pandoc-crossref `eqnBlockTemplate` placeholders as real formulas and emit
  -- bogus `display:t` / `display:nmi` markers into the generated equation row.
  local body = pandoc.Div(document.blocks):walk({ Math = mark_math })
  document.blocks = body.content
  return document
end
