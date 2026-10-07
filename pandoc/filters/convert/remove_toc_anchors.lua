-- Remove Word-generated _Toc bookmarks and their internal navigation links.
-- Process the Pandoc AST, including nested spans/links, image captions, notes,
-- tables and headings. Link wrappers are removed but their visible content and
-- formatting remain; empty bookmark spans disappear. Other identifiers (notably
-- _Ref figure/equation bookmarks) and ordinary/external links remain usable.
-- Examples (Markdown equivalents of the DOCX AST):
--   [图2‑35 各时段核密度估计 [78](#_Toc241697943)](#_Toc241697943)
--     -> 图2‑35 各时段核密度估计 78
--   []{#_Toc241697890 .anchor} -> removed
--   [[]{#_Toc241697898 .anchor}]{#_Ref181174213 .anchor}
--     -> []{#_Ref181174213 .anchor}
--   # Introduction {#_Toc123} -> # Introduction (without the _Toc identifier)
-- This removes TOC navigation markup, not the visible table-of-contents text.
-- Run before detect_figure.lua and crossrefs.lua so remaining _Ref bookmarks
-- are exposed and emptied anchor paragraphs do not separate images/captions:
--   pandoc input.docx -f docx -t markdown -L pandoc/filters/convert/remove_toc_anchors.lua

--- Identify only Word TOC bookmark names, without matching ordinary bookmarks.
local function is_toc(identifier)
  return identifier ~= nil and identifier:match('^_Toc') ~= nil
end

--- Remove a TOC identifier and its anchor class while retaining other attributes.
local function clear_anchor(element)
  if not is_toc(element.identifier) then return nil end
  element.identifier = ''
  local classes = {}
  for _, class in ipairs(element.classes) do
    if class ~= 'anchor' then classes[#classes + 1] = class end
  end
  element.classes = classes
  return element
end

--- Unwrap TOC spans unless they also carry meaningful formatting attributes.
function Span(span)
  if not is_toc(span.identifier) then return nil end
  clear_anchor(span)
  if #span.classes == 0 and #span.attributes == 0 then return span.content end
  return span
end

--- Unwrap nested TOC navigation links without losing their text or page numbers.
function Link(link)
  if link.target:match('^#_Toc') then return link.content end
  return clear_anchor(link)
end

--- Drop paragraphs emptied by bookmark removal so neighboring blocks stay adjacent.
function Blocks(blocks)
  local result = {}
  for _, block in ipairs(blocks) do
    local empty = block.t == 'Para' or block.t == 'Plain'
    if empty then
      for _, inline in ipairs(block.content) do
        if inline.t ~= 'Space' and inline.t ~= 'SoftBreak' and inline.t ~= 'LineBreak' then
          empty = false
          break
        end
      end
    end
    if not empty then result[#result + 1] = block end
  end
  return result
end

-- Attribute-bearing nodes can also carry Word bookmarks outside empty spans.
Header = clear_anchor
Div = clear_anchor
Figure = clear_anchor
Image = clear_anchor
Table = clear_anchor
Code = clear_anchor
CodeBlock = clear_anchor
