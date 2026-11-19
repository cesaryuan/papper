-- Export recognized crossref groups as editable Papper Tools v2 SVGs, e.g.
-- fig:comparison -> media/subfigure-comparison.svg alongside the extracted pictures.
-- Run after crossref recovery and media_paths: image links must use their final
-- exported filenames. Stage complete SVGs for the CLI's no-clobber publication.
-- Unrepresentable captions or assets leave the entire original group intact.
local resource = dofile(pandoc.path.join {pandoc.path.directory(PANDOC_SCRIPT_FILE), '../shared/filter_resources.lua'})
local caption_text = dofile(pandoc.path.join {pandoc.path.directory(PANDOC_SCRIPT_FILE), 'caption_text.lua'})
local raster_directory = resource.getenv('PAPPER_CONVERT_SVG_RASTERS')
local destination = resource.getenv('PAPPER_CONVERT_OUTPUT_DIR')
local raster_assets = {}
local font = {family='Times New Roman', cjkFamily='SimSun', size=10, color='#000000', align='center'}
local children, filenames = {}, {}
local gap, padding, rowgap = 10, 8, 16

--- Escape XML character data, including quotes when used in an attribute.
local function xml(text, attribute)
  text = text:gsub('&', '&amp;'):gsub('<', '&lt;'):gsub('>', '&gt;')
  if attribute then text = text:gsub('"', '&quot;') end
  return text
end

--- Produce stable, indented JSON; all empty collections in this schema are arrays.
local function json(value, depth)
  if type(value) ~= 'table' then return pandoc.json.encode(value) end
  local keys, parts = {}, {}
  for key in pairs(value) do keys[#keys+1] = key end
  if #keys == 0 then return '[]' end
  table.sort(keys)
  local array = type(keys[1]) == 'number'
  local indent = string.rep('  ', depth)
  for _, key in ipairs(keys) do
    parts[#parts+1] = indent .. '  ' .. (array and '' or pandoc.json.encode(key) .. ': ') .. json(value[key], depth+1)
  end
  return (array and '[\n' or '{\n') .. table.concat(parts, ',\n') .. '\n' .. indent .. (array and ']' or '}')
end

--- Translate only editable rich-text constructs; never silently flatten a math expression or link.
local function runs(inlines, style, output)
  for _, item in ipairs(inlines) do
    local text = item.t == 'Str' and item.text or (item.t == 'Space' or item.t == 'SoftBreak') and ' '
      or item.t == 'LineBreak' and '\n'
    if text then
      local run = {text=text, bold=style.bold, italic=style.italic, script=style.script}
      local last = output[#output]
      if last and last.bold == run.bold and last.italic == run.italic and last.script == run.script then
        last.text = last.text .. text
      else output[#output+1] = run end
    else
      local next_style = {bold=style.bold, italic=style.italic, script=style.script}
      if item.t == 'Strong' then next_style.bold = true
      elseif item.t == 'Emph' then next_style.italic = true
      elseif item.t == 'Superscript' then next_style.script = 'super'
      elseif item.t == 'Subscript' then next_style.script = 'sub'
      else return false end
      if not runs(item.content, next_style, output) then return false end
    end
  end
  return true
end

--- Check whether authored labels match the editor's automatic numbering order.
local function letter(n)
  local label = ''
  while n > 0 do n = n-1; label = string.char(97+n%26) .. label; n = n//26 end
  return label
end

--- Read Word physical sizes in points when an image cannot report its intrinsic dimensions.
local function points(value)
  local n, unit = (value or ''):match('^([%d.]+)(%a+)$')
  local factor = ({['in']=72, cm=72/2.54, mm=72/25.4, pt=1, px=0.75})[unit]
  return tonumber(n) and factor and tonumber(n)*factor
end

--- Select a staged PNG and give it a stable name that preserves existing media.
local function tiff_variant(src, bytes)
  if not raster_directory or not bytes then return nil end
  local original_digest = pandoc.utils.sha1(bytes)
  if raster_assets[original_digest] then return raster_assets[original_digest] end
  local png = resource.read(pandoc.path.join {raster_directory, original_digest .. '.png'})
  if not png then return nil end
  local stem = pandoc.path.split_extension(pandoc.path.filename(src))
  local base = 'media/' .. stem .. '-svg-' .. pandoc.utils.sha1(png)
  local filename, suffix = base .. '.png', 1
  while true do
    local _, staged = pandoc.mediabag.lookup(filename)
    local disk = destination and resource.read(pandoc.path.join {destination, filename})
    if (not staged or staged == png) and (not disk or disk == png) then break end
    filename, suffix = base .. '-' .. suffix .. '.png', suffix + 1
  end
  local variant = {filename=filename, png=png}
  raster_assets[original_digest] = variant
  return variant
end

--- Collect the editable panel model and keep the source URL outside its metadata.
local function panel(image, index)
  local src = image.src:gsub('^%./', '')
  if pandoc.path.is_absolute(src) or src:match('^%a[%w+.-]*:') or src:sub(1,2)=='//' then return nil end
  local ext = src:lower():match('%.(%w+)$')
  local _, bytes = pandoc.mediabag.lookup(src)
  local variant
  if ext == 'tif' or ext == 'tiff' then
    variant = tiff_variant(src, bytes)
    if not variant then return nil end
    src, bytes = variant.filename, variant.png
  elseif not ({png=true,jpg=true,jpeg=true,svg=true,gif=true,bmp=true,avif=true,webp=true})[ext] then
    return nil
  end
  local caption = {}
  if not runs(image.caption, {}, caption) then return nil end
  local a = image.attributes
  -- The detector sets label only when it found a marker in Word. A table used
  -- only for positioning pictures must not gain new labels on SVG export.
  local label, repeat_label = a.label or '', false
  if a['caption-en'] and a['caption-en']:match('%S') then
    local translation = pandoc.read(a['caption-en'], 'markdown').blocks
    local line = translation[1]
    if #translation ~= 1 or (line.t ~= 'Plain' and line.t ~= 'Para') then return nil end
    local marker, ending = pandoc.utils.stringify(line.content):match('^%s*%(([A-Za-z]+)%)%s+()')
    local inlines = line.content
    if marker == label then
      inlines = caption_text.drop_prefix(inlines, ending - 1)
      if not inlines then return nil end
    end
    -- An authored conflicting letter is already present in the English text.
    if #caption > 0 then
      repeat_label = label ~= '' and (not marker or marker == label)
      caption[#caption+1] = {text='\n'}
    end
    if not runs(inlines, {}, caption) then return nil end
  end
  local w,h = points(a['original-width']), points(a['original-height'])
  if bytes then
    local ok, size = pcall(pandoc.image.size, bytes)
    if ok and size.width > 0 and size.height > 0 then w,h = size.width,size.height end
  end
  local weight = a.width and tonumber(a.width:match('^([%d.]+)%%$')) or nil
  if a.width and not weight or weight and weight <= 0 then return nil end
  local p = {kind='panel',id='e'..index,name=pandoc.path.filename(src),ratio=w and h and h>0 and w>0 and w/h or 1.5,
    caption=caption,label=label,labelPosition='below',repeatLabel=repeat_label or nil,gap=5}
  local crop, any = {}, false
  for _, side in ipairs({'left','top','right','bottom'}) do
    local v = tonumber(a['crop-'..side] or '0')
    if not v or v < 0 then return nil end
    crop[side], any = v, any or v ~= 0
  end
  if crop.left+crop.right >= 100 or crop.top+crop.bottom >= 100 then return nil end
  if any then p.crop = crop end
  -- The manuscript addresses sources from its root; the SVG addresses them from media/.
  local href = src:match('^media/(.+)$') or '../' .. src
  return {model=p, src=href, crop=crop, child=image.identifier, weight=weight or 1, variant=variant}
end

--- Render wrapped SVG text while retaining the original unwrapped rich runs for later editing.
local function caption_svg(x, y, width, content)
  local lines, spans, used = {}, {}, 0
  --- Finish the SVG line without injecting whitespace between its tspans.
  local function flush()
    lines[#lines+1] = string.format('    <text x="%g" y="%g" text-anchor="middle" font-family="Times New Roman" font-size="10" xml:space="preserve">%s</text>',
      x+width/2,y+10+#lines*12.5,table.concat(spans))
    spans,used = {},0
  end
  for _, run in ipairs(content) do
    local attrs = (run.bold and ' font-weight="bold"' or '') .. (run.italic and ' font-style="italic"' or '')
      .. (run.script and (' font-size="70%" baseline-shift="' .. (run.script=='super' and '-0.4em' or '0.25em') .. '"') or '')
    local buffer, face = '', nil
    --- Emit adjacent characters as one styled run, choosing Song type for Han text.
    local function emit()
      if buffer ~= '' then spans[#spans+1] = '<tspan'..(face and ' font-family="SimSun"' or '')..attrs..'>'..xml(buffer)..'</tspan>'; buffer='' end
    end
    for offset, cp in utf8.codes(run.text) do
      local ch, wide = utf8.char(cp), cp >= 0x2e80
      local advance = (wide and 10 or ch==' ' and 2.5 or 5)*(run.script and .7 or 1)
      -- Move a Latin word to the next line intact when it fits; split only oversized words.
      if cp<128 and ch:match('%w') and (offset==1 or not run.text:sub(offset-1,offset-1):match('%w')) then
        local word = run.text:match('^(%w+)',offset)
        if word and used>0 and #word*advance<=width and used+#word*advance>width then emit(); flush() end
      end
      if ch=='\n' or used>0 and used+advance>width then emit(); flush() end
      if ch~='\n' then
        if face~=wide then emit(); face=wide end
        buffer,used = buffer..ch,used+advance
      end
    end
    emit()
  end
  if #spans>0 then flush() end
  return table.concat(lines,'\n'), #lines*12.5
end

--- Prefix each language with the panel letter without storing it in the editable caption.
local function labelled_runs(panel)
  if panel.label == '' then return panel.caption end
  local prefix = '('..panel.label..')'..(#panel.caption>0 and ' ' or '')
  local content = {{text=prefix}}
  local repeat_label = panel.repeatLabel
  for _, run in ipairs(panel.caption) do
    local split = repeat_label and run.text:find('\n',1,true)
    if split then
      content[#content+1] = {text=run.text:sub(1,split),bold=run.bold,italic=run.italic,script=run.script}
      content[#content+1] = {text=prefix}
      if split < #run.text then
        content[#content+1] = {text=run.text:sub(split+1),bold=run.bold,italic=run.italic,script=run.script}
      end
      repeat_label = false
    else
      content[#content+1] = run
    end
  end
  return content
end

--- Size each authored row independently and draw its non-destructively cropped source images.
local function artwork(rows, width)
  local model={format='papper-subfigure',version=2,width=width,autoHeight=true,embedded=false,autoRenumber=true,
    background='white',font=font,layout={mode='rows',columns=#rows[1],rows={},weights={},gap=gap,rowGap=rowgap,padding=padding},elements={}}
  local has_labels, sequential = false, true
  local groups, y = {}, padding
  for _, row in ipairs(rows) do
    local total, available = 0, width-2*padding-(#row-1)*gap
    if available <= 0 then return nil end
    for _, entry in ipairs(row) do total=total+entry.weight end
    local x, bottom = padding, y
    for _, entry in ipairs(row) do
      local p,c = entry.model,entry.crop
      p.x,p.y,p.width=x,y,available*entry.weight/total
      local sw,sh = 1000,1000/p.ratio
      local vw,vh = sw*(100-c.left-c.right)/100, sh*(100-c.top-c.bottom)/100
      p.height=p.width*vh/vw
      local caption = not row.shared and labelled_runs(p) or {}
      local text, textHeight=caption_svg(p.x,p.y+p.height+p.gap,p.width,caption)
      local src=xml(entry.src,true)
      groups[#groups+1]=string.format('  <g data-element-id="%s">\n    <svg x="%g" y="%g" width="%g" height="%g" viewBox="%g %g %g %g" overflow="hidden">\n      <image width="%g" height="%g" href="%s" xlink:href="%s" preserveAspectRatio="none"/>\n    </svg>\n%s\n  </g>',
        p.id,x,y,p.width,p.height,sw*c.left/100,sh*c.top/100,vw,vh,sw,sh,src,src,text)
      model.elements[#model.elements+1]=p
      model.layout.weights[#model.layout.weights+1]=entry.weight
      has_labels = has_labels or p.label ~= ''
      sequential = sequential and p.label == letter(#model.layout.weights)
      bottom=math.max(bottom,y+p.height+(textHeight>0 and p.gap+textHeight or 0))
      x=x+p.width+gap
    end
    if row.shared then
      -- Shared text is an editable element with its own position, so use free layout
      -- to prevent the editor's panel-only reflow from covering it with the next row.
      model.layout.mode, model.autoRenumber = 'free', false
      local title = {kind='text',id='row'..(#model.layout.rows+1),x=padding,y=bottom+5,
        width=width-2*padding,runs=labelled_runs(row[1].model)}
      for _, entry in ipairs(row) do
        entry.model.labelPosition, entry.model.caption, entry.model.repeatLabel = 'none', {}, nil
        entry.model.label = row[1].model.label
      end
      local text, height = caption_svg(title.x,title.y,title.width,title.runs)
      groups[#groups+1] = string.format('  <g data-element-id="%s">\n%s\n  </g>',title.id,text)
      model.elements[#model.elements+1] = title
      bottom = title.y+height
    end
    model.layout.rows[#model.layout.rows+1]=#row
    y=bottom+rowgap
  end
  if not has_labels then model.showPanelLabels = false end
  model.autoRenumber = model.autoRenumber and sequential
  model.height=y-rowgap+padding
  return string.format('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="%gin" height="%gin" viewBox="0 0 %g %g">\n  <metadata id="papper-subfigure">\n    %s\n  </metadata>\n  <rect width="100%%" height="100%%" fill="white"/>\n%s\n</svg>\n',
    width/72,model.height/72,width,model.height,xml(json(model,0)):gsub('\n','\n    '),table.concat(groups,'\n'))
end

--- Replace only image rows followed by one overall caption, never ordinary layout Divs.
local function convert(div, width)
  if not div.identifier:match('^fig:.+') or #div.content<2 then return nil end
  local caption=div.content[#div.content]
  if caption.t~='Para' and caption.t~='Plain' then return nil end
  local rows, count={},0
  for i=1,#div.content-1 do
    local row, block={},div.content[i]
    if block.t~='Para' and block.t~='Plain' then return nil end
    for _, inline in ipairs(block.content) do
      if inline.t=='Image' then
        count=count+1
        local entry=panel(inline,count)
        if not entry then return nil end
        if inline.attributes['papper-convert-shared-row']=='title' then row.shared=true end
        row[#row+1]=entry
      elseif inline.t~='Space' and inline.t~='SoftBreak' then return nil end
    end
    if #row==0 then return nil end
    rows[#rows+1]=row
  end
  if count<2 then return nil end
  local svg=artwork(rows,width)
  if not svg then return nil end
  local base='subfigure-'..div.identifier:sub(5):gsub('[<>:"/\\|?*%c]', '-')
  local filename='media/'..base..'.svg'
  assert(not filenames[filename:lower()] and not pandoc.mediabag.lookup(filename),
    'Conflicting subfigure filename: '..filename)
  filenames[filename:lower()]=true
  -- --extract-media stages the SVG with its source pictures for the same no-clobber publication.
  pandoc.mediabag.insert(filename,'image/svg+xml',svg)
  for _,row in ipairs(rows) do for _,entry in ipairs(row) do
    if entry.variant then
      pandoc.mediabag.insert(entry.variant.filename, 'image/png', entry.variant.png)
    end
    if entry.child~='' then children[entry.child]={id=div.identifier,label=entry.model.label} end
  end end
  local image=pandoc.Image(caption.content, filename, '', div.attr)
  image.attributes.width=string.format('%.6gin',width/72)
  return pandoc.Para{image}
end

--- Keep parent references valid, including forward Word links recovered as raw Markdown citations.
function Pandoc(doc)
  local width = tonumber(pandoc.utils.stringify(doc.meta['papper-subfigure-width'] or '')) or 160*72/25.4
  doc.meta['papper-subfigure-width']=nil
  local remaining = false
  doc=doc:walk({Div=function(div)
    local uneven=div.attributes['papper-convert-uneven-rows']
    div.attributes['papper-convert-uneven-rows']=nil
    local image = convert(div,width)
    if not image and div.identifier:match('^fig:') then
      remaining = true
      resource.warn('Keeping crossref layout for '..div.identifier..': unsupported figure contents')
      -- Keep the title on the first picture if a linked asset or caption prevents SVG export.
      for i=1,#div.content-1 do
        local block = div.content[i]
        if block.t=='Para' or block.t=='Plain' then
          local inlines = block.content
          for _, item in ipairs(inlines) do
            if item.t=='Image' then item.attributes['papper-convert-shared-row']=nil end
          end
          block.content = inlines
          div.content[i] = block
        end
      end
      if uneven then
        local blocks=pandoc.List{}
        for i=1,#div.content-1 do
          for _,item in ipairs(div.content[i].content) do
            if item.t=='Image' then item.attributes.width='100%'; blocks:insert(pandoc.Para{item}) end
          end
        end
        blocks:insert(div.content[#div.content])
        div.content=blocks
        return div
      end
      return div
    end
    return image
  end})
  if remaining and doc.meta.subfigGrid == nil then doc.meta.subfigGrid=pandoc.MetaBool(true) end
  return doc:walk({
    RawInline=function(raw)
      local target=raw.format=='markdown' and children[raw.text:match('^%[@([^%]]+)%]$')]
      if not target then return nil end
      local parent = pandoc.RawInline('markdown','[@'..target.id..']')
      if target.label == '' then return parent end
      return {parent,pandoc.Space(),pandoc.Str('('..target.label..')')}
    end,
    Cite=function(cite)
      for _,citation in ipairs(cite.citations) do
        local target=children[citation.id]
        if target then
          citation.id=target.id
          if target.label ~= '' then
            if #cite.citations==1 and #citation.prefix==0 and #citation.suffix==0 then
              return {cite,pandoc.Space(),pandoc.Str('('..target.label..')')}
            end
            citation.suffix:insert(1,pandoc.Str('('..target.label..')'))
            citation.suffix:insert(1,pandoc.Space())
          end
        end
      end
      return cite
    end,
    Link=function(link)
      local target=children[link.target:match('^#(.+)$')]
      if target then link.target='#'..target.id; return link end
    end})
end
