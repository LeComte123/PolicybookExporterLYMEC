-- Resolution texts never contain program code. A code block (typewriter font) only appears when text
-- is indented too far, e.g. deeply nested lists; read such a block as normal text again.
local function as_text(code)
  local blocks = pandoc.read(code.text, "markdown-smart").blocks
  return blocks:walk({ CodeBlock = as_text })  -- the text is shorter each time: this ends
end

function CodeBlock(code)
  return as_text(code)
end
