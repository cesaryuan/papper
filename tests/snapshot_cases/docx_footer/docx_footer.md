---
title: Custom footer
papperSettings:
  mathtype: false
  docxHeader:
    content: '**Review copy** - []{t="pageNum"} -'
    alignment: left
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

[]{t="sectionBreak" headerDefault="**Odd A**" headerEven="*Even B*" headerDistance="3cm" footerDistance="2.3cm"}

# Odd and even headers

This section has two headers and keeps one common footer.

[]{t="pageBreak"}

Second page with the alternate header.

[]{t="pageBreak"}

Third page with the other header.

[]{t="sectionBreak" headerDefault='**Default header** [Link](https://example.org/default)' headerEven='*Even header* [Link](https://example.org/even)' headerFirst='**First header**&#10;&#10;| First | Header |&#10;| :---- | -----: |&#10;| Table | Cell |&#10;' footerDefault='Default - []{t="pageNum"} -' footerEven='Even - []{t="pageNum"} -' footerFirst='**First** - []{t="pageNum" format="upperRoman"} -' headerDistance="25mm" footerDistance="0.75in"}

# Three independent variants

This page uses the first-page stories with a Markdown table and formatted field.

[]{t="pageBreak"}

This page uses one of the default/even stories.

[]{t="pageBreak"}

This page uses the other default/even stories.

[]{t="sectionBreak"}

# Inherited variants

All page variants, first-page mode, and distances continue from the previous section.

[]{t="sectionBreak" headerLink="false" footerLink="false"}

# Independent copies

All three page variants have independent copies with preserved content and links.

[]{t="sectionBreak" header="Unified header" footer='Unified []{t="pageNum"}' differentFirstPage="false" headerDistance="0pt" footerDistance="18pt"}

# Uniform again

This section restores a common header and footer, and turns off first-page differences.

[]{t="pageBreak"}

The next page also uses the uniform content.

[]{t="sectionBreak" headerEven=""}

# Clear one variant

The even-page header is empty while the default header and footer remain inherited.

[]{t="pageBreak"}

The cleared even page keeps its footer and displays no header.
