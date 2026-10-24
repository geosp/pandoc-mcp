-- Resolves images against the resources passed to convert_document
--
-- The server copies the requested stored documents into one directory
-- (PANDOC_MCP_RESOURCES_DIR). An image whose file name matches one of them
-- (![chart](chart.png), ./chart.png or images/chart.png) is embedded: as a
-- data URI for HTML outputs, through pandoc's media bag otherwise. Any other
-- image is left alone, so under --sandbox it still cannot reach server files.

local dir = os.getenv("PANDOC_MCP_RESOURCES_DIR")

local MIME_TYPES = {
  png = "image/png", jpg = "image/jpeg", jpeg = "image/jpeg", gif = "image/gif",
  svg = "image/svg+xml", webp = "image/webp", pdf = "application/pdf",
}

local function read(name)
  local f = io.open(pandoc.path.join({dir, name}), "rb")
  if not f then return nil end
  local data = f:read("a")
  f:close()
  return data
end

function Image(img)
  if not dir then return nil end
  local name = pandoc.path.filename((img.src:gsub("[?#].*$", "")))
  if name == "" or name:match("^%.") then return nil end
  local data = read(name)
  if not data then return nil end

  local mime = MIME_TYPES[(name:match("%.([^.]+)$") or ""):lower()] or "application/octet-stream"
  if FORMAT:match("html") or FORMAT:match("^revealjs") or FORMAT:match("^slid")
      or FORMAT == "s5" or FORMAT == "dzslides" then
    img.src = pandoc.mediabag.make_data_uri(mime, data)
  else
    pandoc.mediabag.insert(name, mime, data)
    img.src = name
  end
  return img
end
