-- Recover a Word paragraph's bottom edge as a Papper border, e.g.
-- ::: {borders.bottom="3pt thinThickSmallGap auto" borders.space="1pt"}.
-- The temporary marker keeps the line attached to the paragraph during import.

local directory = pandoc.path.directory(PANDOC_SCRIPT_FILE)
local xml = dofile(pandoc.path.join {directory, 'docx_layout_xml.lua'})
local borders = {}
borders.marker_class = 'papper-import-paragraph-border'
local line_styles = {}
for name in ([=[single thick double dotted dashed dotDash dotDotDash triple
  thinThickSmallGap thickThinSmallGap thinThickThinSmallGap thinThickMediumGap
  thickThinMediumGap thinThickThinMediumGap thinThickLargeGap thickThinLargeGap
  thinThickThinLargeGap wave doubleWave dashSmallGap dashDotStroked
  threeDEmboss threeDEngrave outset inset]=]):gmatch('%S+') do
  line_styles[name] = true
end

--- Convert direct bottom borders before Pandoc turns them into standalone rules.
function borders.prepare(paragraph, insert_marker, marker)
  local group = xml.child(xml.child(paragraph, 'pPr'), 'pBdr')
  local bottom = xml.child(group, 'bottom')
  -- An empty bordered paragraph may be an actual horizontal separator in Word.
  if not bottom or not xml.text(paragraph):find('%S') then return false end
  local style = xml.attr(bottom, 'val')
  local attrs = {}
  if style == 'nil' or style == 'none' then
    attrs['borders.bottom'] = 'none'
  else
    local width = tonumber(xml.attr(bottom, 'sz') or '4')
    local color = xml.attr(bottom, 'color') or 'auto'
    if not line_styles[style] or not width or width % 1 ~= 0 or width < 2 or width > 96 then return false end
    if #color == 6 and color:match('^%x+$') then color = '#' .. color
    elseif color ~= 'auto' then return false end
    attrs['borders.bottom'] = tostring(width / 8):gsub('%.0$', '') .. 'pt ' .. style .. ' ' .. color
    local space = tonumber(xml.attr(bottom, 'space'))
    if space and space % 1 == 0 and space >= 0 and space <= 31 then
      attrs['borders.space'] = tostring(space) .. 'pt'
    end
  end
  for index, child in ipairs(group.children) do
    if child == bottom then table.remove(group.children, index); break end
  end
  insert_marker(paragraph, marker({border=attrs}))
  return true
end

--- Restore border formatting after caption, equation, and media recovery.
function borders.restore(document)
  --- Move the annotation from the text into a local paragraph-formatting Div.
  local function paragraph(block)
    for index, inline in ipairs(block.content) do
      if inline.t == 'Span' and inline.classes:includes(borders.marker_class) then
        block.content:remove(index)
        return pandoc.Div({pandoc.Para(block.content)}, pandoc.Attr('', {}, inline.attributes))
      end
    end
  end
  document = document:walk({Para=paragraph, Plain=paragraph})
  -- Headings and other semantic blocks do not accept local paragraph borders.
  return document:walk({Span=function(span)
    if span.classes:includes(borders.marker_class) then return {} end
  end})
end

return borders
