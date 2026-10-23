-- Renders ```mermaid code blocks with mermaid-cli (mmdc)
--
-- HTML and slide formats get inline SVG; LaTeX/PDF a vector PDF; other formats
-- (docx, odt, pptx, epub, ...) a PNG. Markdown-like outputs keep the code block,
-- since their viewers render mermaid themselves. Images go into pandoc's media
-- bag, so they are embedded in the output.
--
-- Block attributes: caption="..." makes a figure, width="80%" sizes the image,
-- an #id is kept for cross-references.
--
-- Environment: PANDOC_MCP_MMDC (executable), PANDOC_MCP_MERMAID_THEME,
-- PANDOC_MCP_PUPPETEER_CONFIG (optional JSON file for headless Chromium).

local mmdc = os.getenv("PANDOC_MCP_MMDC") or "mmdc"
local theme = os.getenv("PANDOC_MCP_MERMAID_THEME") or "default"
local puppeteer_config = os.getenv("PANDOC_MCP_PUPPETEER_CONFIG")
local count = 0

local function warn(message)
  if pandoc.log and pandoc.log.warn then
    pandoc.log.warn(message)
  else
    io.stderr:write("[WARNING] " .. message .. "\n")
  end
end

local function target()
  if FORMAT:match("^markdown") or FORMAT:match("^gfm") or FORMAT:match("^commonmark")
      or FORMAT == "ipynb" or FORMAT == "plain" or FORMAT == "native" or FORMAT == "json" then
    return nil
  end
  if FORMAT:match("html") or FORMAT:match("^revealjs") or FORMAT:match("^slid")
      or FORMAT == "s5" or FORMAT == "dzslides" then
    return "svg"
  end
  if FORMAT:match("latex") or FORMAT:match("beamer") then
    return "pdf"
  end
  return "png"
end

local function render(code, format, id)
  return pandoc.system.with_temporary_directory("mermaid", function(dir)
    local input = pandoc.path.join({dir, "diagram.mmd"})
    local output = pandoc.path.join({dir, "diagram." .. format})
    local f = assert(io.open(input, "w"))
    f:write(code)
    f:close()
    local args = {"--input", input, "--output", output, "--theme", theme,
                  "--backgroundColor", format == "svg" and "transparent" or "white", "--quiet"}
    if format == "png" then table.insert(args, "--scale"); table.insert(args, "2") end
    if format == "pdf" then table.insert(args, "--pdfFit") end
    if format == "svg" then table.insert(args, "--svgId"); table.insert(args, id) end
    if puppeteer_config and puppeteer_config ~= "" then
      table.insert(args, "--puppeteerConfigFile"); table.insert(args, puppeteer_config)
    end
    pandoc.pipe(mmdc, args, "")
    local result = assert(io.open(output, "rb"))
    local data = result:read("a")
    result:close()
    return data
  end)
end

function CodeBlock(block)
  if not block.classes:includes("mermaid") then return nil end
  local format = target()
  if not format then return nil end

  count = count + 1
  local id = "mermaid-" .. count
  local ok, data = pcall(render, block.text, format, id)
  if not ok then
    warn("Mermaid diagram " .. count .. " could not be rendered and was left as code: "
         .. tostring(data))
    return nil
  end

  local caption = block.attributes.caption
  local content
  if format == "svg" then
    local style = block.attributes.width and (' style="width:' .. block.attributes.width .. '"') or ""
    content = pandoc.RawBlock("html", '<div class="mermaid-diagram"' .. style .. ">" .. data .. "</div>")
  else
    local name = id .. "." .. format
    pandoc.mediabag.insert(name, format == "pdf" and "application/pdf" or "image/png", data)
    local attributes = {}
    if block.attributes.width then attributes.width = block.attributes.width end
    content = pandoc.Plain({pandoc.Image({}, name, "", pandoc.Attr("", {}, attributes))})
  end

  if caption then
    return pandoc.Figure(content, {pandoc.Plain(pandoc.read(caption, "markdown").blocks[1].content)},
                         pandoc.Attr(block.identifier))
  end
  return content.t == "Plain" and pandoc.Para(content.content) or content
end
