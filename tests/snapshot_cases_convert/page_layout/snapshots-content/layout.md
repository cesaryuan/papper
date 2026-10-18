---
papperSettings:
  docxFooter:
    alignment: center
    content: Page **[]{t="pageNum" format="upperRoman"}** of []{t="field" instruction="NUMPAGES" result="12"}
  docxHeader:
    alignment: right
    content: |
      **Header \"quoted\" &** *italic* [Project](https://example.org/paper?draft=1&view=full)

      ![](media/image1-dee5c401fed04b20cd5314251bddd4ece4752084.png){width="0.25in" height="0.125in"}

      +-----------+-----------+
      | Key       | Value     |
      +===========+===========+
      | Version   | Draft     |
      +-----------+-----------+

      Chapter: **[]{t="field" instruction="STYLEREF 1" result="Chapter 4"}**

      $x$
title: Layout specimen
---

[]{t="sectionBreak" kind="continuous" differentFirstPage="true" headerDistance="28.0pt" footerDistance="32.0pt" headerEven="Even header" headerFirst="Opening header" headerAlignment="left" footerEven="Even footer" footerFirst="" footerAlignment="left"}

# Chapter 4

First section

Before[]{t="pageBreak"}After cached pagination

Body page **[]{t="pageNum"}**

[]{t="pageBreak"}Style page start

Explicit override

[]{t="sectionBreak" kind="continuous" pageStart="3" differentFirstPage="false" headerDistance="28.0pt" footerDistance="32.0pt" footerDefault="" footerAlignment="left"}

Second section

Roman page []{t="pageNum" format="lowerRoman"}

[]{t="sectionBreak" kind="oddPage" differentFirstPage="true" headerDistance="40.0pt" footerDistance="24.0pt" headerDefault="New normal header" headerFirst="Opening header" headerAlignment="left" footerFirst="" footerAlignment="left"}

Third section

[]{t="sectionBreak" kind="evenPage" differentFirstPage="false" headerDistance="40.0pt" footerDistance="24.0pt" headerDefault="" headerEven="" headerAlignment="left"}

Fourth section

[]{t="sectionBreak" kind="nextPage" differentFirstPage="false" headerDistance="40.0pt" footerDistance="24.0pt" footerDefault="Final - []{t=&quot;pageNum&quot; format=&quot;lowerLetter&quot;}" footerAlignment="left"}

Fifth section
