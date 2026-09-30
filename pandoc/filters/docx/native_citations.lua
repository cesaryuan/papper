-- Convert citeproc's numeric bibliography and citation links to Word SEQ/REF fields
-- Run after citeproc and citation_number_range_delimiter.lua, so CSL punctuation,
-- range collapsing, locators, and superscripts are already resolved
-- Bookmarks contain only bibliography numbers; surrounding CSL text stays intact
-- PMT_DOCX_NATIVE_CROSSREFS gates this workflow together with other native references
-- Author-date styles and unsupported labels retain their original citeproc output

local helpers = dofile(pandoc.path.join {
  pandoc.path.directory(PANDOC_SCRIPT_FILE), 'native_field_helpers.lua',
})

--- Keep routine bibliography totals in verbose build logs
local function debug(message)
  if (os.getenv('PANDOC_TEMPLATE_LOG_LEVEL') or ''):upper() == 'DEBUG' then
    io.stderr:write('[native-citations] ' .. message .. '\n')
  end
end

--- Report ambiguous numeric styles while preserving their original citation links
local function warn(message)
  io.stderr:write('[native-citations] ' .. message .. '\n')
end

--- Read a leading citation number only when one Str contains all of its digits
local function leading_number(container)
  local number = pandoc.utils.stringify(container.content):match('^[%s%p]*(%d+)%f[^%d]')
  if not number then return nil end
  local first_digits
  container:walk {
    Str = function(str)
      if not first_digits then first_digits = str.text:match('%d+') end
    end,
  }
  -- Digit runs split by formatting are ambiguous; never reference a partial number.
  return first_digits == number and number or nil
end

--- Find the CSL number label without searching dates or numbers in reference text
local function number_container(entry)
  local labels = {}
  entry:walk {
    Span = function(span)
      if span.classes:includes('csl-left-margin') then labels[#labels + 1] = span end
    end,
  }
  if #labels > 1 then return nil end
  local container = labels[1] or entry.content[1]
  if not container or (container.t ~= 'Span' and container.t ~= 'Para'
      and container.t ~= 'Plain') then return nil end
  local text = pandoc.utils.stringify(container.content)
  local number = leading_number(container)
  if not number then return nil end
  if #labels == 1 and not text:match('^%s*[%[%(]?(%d+)[%]%)]?%.?%s*$') then
    return nil
  end
  return container, number, #labels == 1
end

--- Replace only the validated leading number, preserving CSL text and inline styles
local function replace_number(container, number_inlines)
  local replaced = false
  return container:walk {
    Str = function(str)
      if replaced then return nil end
      local prefix, digits, suffix = str.text:match('^(%D*)(%d+)(.*)$')
      if not digits then return nil end
      replaced = true
      local content = pandoc.Inlines {}
      if prefix ~= '' then content:insert(pandoc.Str(prefix)) end
      content:extend(number_inlines)
      if suffix ~= '' then content:insert(pandoc.Str(suffix)) end
      return content
    end,
  }
end

--- Wrap a bibliography SEQ in a bookmark containing exactly the displayed number
local function numbered_container(container, target)
  return replace_number(container, {
    pandoc.Span(
      helpers.field('SEQ PapperBibliography \\* ARABIC', { pandoc.Str(target.number) }),
      pandoc.Attr(target.name)),
  })
end

--- Bind numeric citations to bibliography number bookmarks after CSL rendering
function Pandoc(doc)
  if FORMAT ~= 'docx' or os.getenv('PMT_DOCX_NATIVE_CROSSREFS') ~= 'true' then
    return nil
  end
  local entries, identifiers = {}, {}
  --- Reserve existing IDs, including native figure and heading bookmarks
  local function remember(element)
    identifiers[element.identifier] = true
  end
  doc:walk {
    Div = function(div)
      remember(div)
      if div.classes:includes('csl-entry') and div.identifier:match('^ref%-') then
        entries[#entries + 1] = div
      end
    end,
    Span = remember, Header = remember, Table = remember, Figure = remember,
    Code = remember, CodeBlock = remember, Link = remember, Image = remember,
  }
  if #entries == 0 then return nil end
  local numeric_links = false
  doc:walk {
    Link = function(link)
      local number = leading_number(link)
      if link.target:match('^#ref%-') and number and tonumber(number) <= #entries then
        numeric_links = true
      end
    end,
  }
  local targets = {}
  local namespace = (os.getenv('PMT_DOCX_BOOKMARK_NAMESPACE')
    or pandoc.utils.sha1(tostring({}) .. os.time() .. os.clock())) .. ':bibliography'
  local next_bookmark = 0
  for index, entry in ipairs(entries) do
    local _, number, aligned = number_container(entry)
    -- Validate the entire bibliography before editing. Mixed static/SEQ numbers
    -- would silently misnumber later entries after a field update in Word.
    if not number or tonumber(number) ~= index or number ~= tostring(index) then
      if numeric_links then
        warn('Unsupported bibliography number labels; keeping citeproc numbering and citation links')
      else
        debug('Keeping nonnumeric or unsupported CSL bibliography and citation links')
      end
      return nil
    end
    local name
    repeat
      next_bookmark = next_bookmark + 1
      name = 'PapperRef-' .. helpers.bookmark_suffix(namespace, next_bookmark)
    until not identifiers[name]
    identifiers[name] = true
    targets[entry.identifier] = { name = name, number = number, aligned = aligned }
  end
  doc = doc:walk {
    Div = function(div)
      local target = targets[div.identifier]
      if not target then return nil end
      if target.aligned then
        return div:walk {
          Span = function(span)
            if span.classes:includes('csl-left-margin') then
              return numbered_container(span, target)
            end
          end,
        }
      end
      div.content[1] = numbered_container(div.content[1], target)
      return div
    end,
  }
  local ref_count = 0
  doc = doc:walk {
    Link = function(link)
      local target = targets[link.target:match('^#(.+)$')]
      if not target or leading_number(link) ~= target.number then
        return nil
      end
      ref_count = ref_count + 1
      -- Preserve CSL superscript/italic formatting when Word refreshes the REF.
      return replace_number(pandoc.Span(link.content),
        helpers.field('REF ' .. target.name .. ' \\h \\* MERGEFORMAT',
          { pandoc.Str(target.number) })).content
    end,
  }
  debug(('Emitted %d bibliography REF fields and %d bibliography SEQ fields')
    :format(ref_count, #entries))
  return doc
end
