---
title: Custom footer
papperSettings:
  docxFooter:
    content: 'Confidential - []{t="pageNum"} -'
    alignment: right
---

# First page

The footer contains decorative text around a dynamic page number.

```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```

# Second page

The same footer uses the current page number on each page.
