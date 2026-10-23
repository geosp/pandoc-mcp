-- Turns a paragraph containing only \newpage or \pagebreak into a page break in docx, odt, html and LaTeX/PDF
local breaks = {
  docx = pandoc.RawBlock("openxml", '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'),
  odt = pandoc.RawBlock("opendocument", '<text:p text:style-name="Pagebreak"/>'),
  html = pandoc.RawBlock("html", '<div style="page-break-after: always;"></div>'),
  latex = pandoc.RawBlock("latex", "\\newpage{}"),
}

local function page_break()
  if FORMAT:match("docx") then return breaks.docx end
  if FORMAT:match("odt") then return breaks.odt end
  if FORMAT:match("html") then return breaks.html end
  if FORMAT:match("latex") or FORMAT:match("beamer") then return breaks.latex end
  return pandoc.HorizontalRule()
end

local function is_break(text)
  return text == "\\newpage" or text == "\\pagebreak"
end

function RawBlock(el)
  if el.format:match("tex") and is_break(el.text) then return page_break() end
end

function Para(el)
  if #el.content == 1 and el.content[1].t == "RawInline" and is_break(el.content[1].text) then
    return page_break()
  end
end
