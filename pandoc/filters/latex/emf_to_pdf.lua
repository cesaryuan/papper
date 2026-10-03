-- Use the PDF companion of an EMF resource when producing LaTeX
-- This preserves the previous case-sensitive suffix rule and all image metadata

--- Replace the final .emf suffix without changing remote fragments or image captions
function Image(element)
  element.src = element.src:gsub('%.emf$', '.pdf')
  return element
end
