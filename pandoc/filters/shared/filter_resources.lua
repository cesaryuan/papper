-- Shared resource handling for Papper's in-process Lua filters
-- Paths are resolved in manuscript search order; cache validation uses bytes so
-- same-size edits with restored timestamps cannot retain stale illustrations

local M = {}
local path, system = pandoc.path, pandoc.system
local environment = system.environment()

--- Read Unicode environment values through Haskell instead of Lua's Windows ANSI getenv
function M.getenv(name)
  return environment[name]
end

--- Emit actionable filter warnings while respecting the existing log threshold
function M.warn(message)
  local level = (M.getenv('PANDOC_TEMPLATE_LOG_LEVEL') or 'INFO'):upper()
  if level ~= 'ERROR' and level ~= 'CRITICAL' then
    io.stderr:write('[WARN] ' .. message .. '\n')
  end
end

--- Interpret opt-in settings with the established boolean spellings
function M.boolean(value)
  local text = tostring(value or ''):match('^%s*(.-)%s*$'):lower()
  return text == '1' or text == 'true' or text == 'yes' or text == 'y' or text == 'on'
end

--- Reject nonfinite/nonpositive numeric overrides instead of breaking a build
function M.positive(value, fallback)
  local number = tonumber(value)
  if number and number > 0 and number < math.huge then return number end
  return fallback
end

--- Resolve an absolute normalized path without changing Unicode bytes
function M.absolute(value)
  local absolute = path.is_absolute(value) and value
    or path.join {system.get_working_directory(), value}
  local normalized = path.normalize(absolute)
  if normalized:sub(1, 4) == '\\\\?\\' then normalized = normalized:sub(5) end
  return normalized
end

--- Emit forward slashes accepted by Pandoc on every supported platform
function M.pandoc_path(value)
  return value:gsub('\\', '/')
end

--- Read exact file bytes; a missing resource is distinguishable from an empty file
function M.read(value)
  -- Lua's C io.open cannot reliably open Unicode paths on Windows; Pandoc's
  -- Haskell file API uses native Unicode paths and returns unchanged bytes
  local ok, bytes = pcall(system.read_file, value)
  return ok and bytes or nil
end

--- Test regular readable files without loading potentially large image contents
function M.is_file(value)
  if not path.exists(value) then return false end
  local directory = pcall(system.list_directory, value)
  return not directory
end

--- Read ordered resource roots using the platform's search-path separator
function M.roots(environment)
  local roots, seen = {}, {}
  local configured = M.getenv(environment) or '.'
  local paths = path.split_search_path(configured)
  if configured:match('^%s*%[') then
    local ok, decoded = pcall(pandoc.json.decode, configured)
    if ok and type(decoded) == 'table' then paths = decoded end
  end
  for _, raw in ipairs(paths) do
    if raw ~= '' then
      local absolute = M.absolute(raw)
      if not seen[absolute] then roots[#roots + 1], seen[absolute] = absolute, true end
    end
  end
  if #roots == 0 then roots[1] = M.absolute('.') end
  return roots
end

--- Decode local image URLs while leaving remote schemes and data URIs untouched
function M.local_path(value)
  if not value or value == '' then return nil end
  local clean = value:match('^[^?#]*')
  if clean:sub(1, 5):lower() == 'file:' then
    clean = clean:sub(6)
    if clean:sub(1, 3) == '///' then
      clean = clean:sub(4)
      if not clean:match('^%a:[/\\]') then clean = '/' .. clean end
    end
  elseif clean:match('^[^/\\:]+:') and not clean:match('^%a:[/\\]') then
    return nil
  end
  if clean == '' then return nil end
  return (clean:gsub('%%(%x%x)', function(hex) return string.char(tonumber(hex, 16)) end))
end

--- Resolve the first existing file without letting lower-priority roots shadow it
function M.resolve(value, roots)
  if path.is_absolute(value) then
    local absolute = M.absolute(value)
    return M.is_file(absolute) and absolute or nil
  end
  for _, root in ipairs(roots) do
    local candidate = M.absolute(path.join {root, value})
    if M.is_file(candidate) then return candidate end
  end
  return nil
end

--- Identify both supported SVG source extensions case-insensitively
function M.is_svg(value)
  local extension = value:match('%.([^./\\]+)$')
  return extension and (extension:lower() == 'svg' or extension:lower() == 'svgz')
end

--- Select the familiar relative cache layout, disambiguating outside-project files
function M.cache_path(source, output, roots, extension)
  for _, root in ipairs(roots) do
    local relative = path.make_relative(source, root)
    if not path.is_absolute(relative) and relative ~= '..'
      and not relative:match('^%.%.[/\\]') then
      local stem = path.split_extension(relative)
      return path.join {output, stem .. '.' .. extension}
    end
  end
  local stem = path.split_extension(path.filename(source))
  -- SHA1 is provided by Pandoc and avoids a separate process for path identities
  return path.join {output, stem .. '-' .. pandoc.utils.sha1(source):sub(1, 12) .. '.' .. extension}
end

--- Publish output by a same-directory rename and remove temporary files on failure
function M.atomic_write(target, bytes)
  local directory = path.directory(target)
  system.make_directory(directory, true)
  system.with_temporary_directory(directory, '.papper-write', function(temporary)
    local staged = path.join {temporary, 'content'}
    system.write_file(staged, bytes)
    if system.rename then
      system.rename(staged, target)
    else
      local renamed, message = os.rename(staged, target)
      assert(renamed, message)
    end
  end)
end

--- Record content identities independent of timestamps and file-size coincidences
function M.fingerprint(source, bytes)
  bytes = bytes or assert(M.read(source), 'Cannot read resource: ' .. source)
  return {path = source, size = #bytes, sha1 = pandoc.utils.sha1(bytes)}
end

--- Reuse only complete cache pairs with the current content/options fingerprint
function M.cache_matches(target, expected)
  if not M.is_file(target) then return false end
  local raw = M.read(target .. '.meta.json')
  if not raw then return false end
  local ok, metadata = pcall(pandoc.json.decode, raw)
  return ok and type(metadata) == 'table' and metadata.fingerprint == expected
end

--- Publish the sidecar after the illustration so interrupted work is never a hit
function M.publish_metadata(target, fingerprint, details)
  details.fingerprint = fingerprint
  M.atomic_write(target .. '.meta.json', pandoc.json.encode(details))
end

--- Locate the small pixel renderer rather than aliasing the complete Papper CLI
function M.svg_helper()
  local explicit = M.getenv('PAPPER_SVG_RENDERER')
  if explicit and explicit ~= '' then return explicit end
  local root = path.directory(path.directory(path.directory(path.directory(PANDOC_SCRIPT_FILE))))
  local suffix = (system.os == 'mingw32' or system.os == 'windows') and '.exe' or ''
  local candidate = path.join {root, 'bin', 'papper-svg' .. suffix}
  return M.is_file(candidate) and candidate or 'papper-svg' .. suffix
end

return M
