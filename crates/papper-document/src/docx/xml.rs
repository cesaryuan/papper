//! Edit OOXML trees while retaining namespaces, mixed text, and unknown elements.

use anyhow::{Context, Result};
use std::collections::BTreeMap;

/// Retain the non-element XML nodes that can occur inside Word document parts.
#[derive(Clone, Debug)]
pub(crate) enum Node {
    Element(Element),
    Text(String),
    Comment(String),
    Processing(String),
}

/// Keep namespace-qualified names and ordered children without a Word object model.
#[derive(Clone, Debug)]
pub(crate) struct Element {
    pub name: String,
    pub attrs: BTreeMap<String, String>,
    pub children: Vec<Node>,
}

impl Element {
    /// Create a qualified element with no attributes or children.
    pub fn new(name: &str) -> Self {
        Self {
            name: name.into(),
            attrs: BTreeMap::new(),
            children: Vec::new(),
        }
    }

    /// Create a qualified element with convenient attribute pairs.
    pub fn with_attrs(name: &str, attrs: &[(&str, &str)]) -> Self {
        let mut element = Self::new(name);
        for (key, value) in attrs {
            element.set(key, value);
        }
        element
    }

    /// Add or replace one attribute without removing unrelated Word metadata.
    pub fn set(&mut self, name: &str, value: impl ToString) {
        self.attrs.insert(name.into(), value.to_string());
    }

    /// Read one namespace-qualified attribute.
    pub fn attr(&self, name: &str) -> Option<&str> {
        self.attrs.get(name).map(String::as_str)
    }

    /// Append a newly created element after the existing children.
    pub fn push(&mut self, child: Element) {
        self.children.push(Node::Element(child));
    }

    /// Read a direct child element by its qualified name.
    pub fn child(&self, name: &str) -> Option<&Element> {
        self.elements().find(|element| element.name == name)
    }

    /// Mutably access a direct child element by its qualified name.
    pub fn child_mut(&mut self, name: &str) -> Option<&mut Element> {
        self.elements_mut().find(|element| element.name == name)
    }

    /// Visit direct element children while leaving comments and text in place.
    pub fn elements(&self) -> impl Iterator<Item = &Element> {
        self.children.iter().filter_map(|node| {
            if let Node::Element(element) = node {
                Some(element)
            } else {
                None
            }
        })
    }

    /// Visit mutable direct element children without reordering other XML nodes.
    pub fn elements_mut(&mut self) -> impl Iterator<Item = &mut Element> {
        self.children.iter_mut().filter_map(|node| {
            if let Node::Element(element) = node {
                Some(element)
            } else {
                None
            }
        })
    }

    /// Remove direct children with the given name, preserving all other content.
    pub fn remove(&mut self, name: &str) {
        self.children
            .retain(|node| !matches!(node, Node::Element(element) if element.name == name));
    }

    /// Return a child or append it, matching the existing low-level XML helpers.
    pub fn ensure(&mut self, name: &str) -> &mut Element {
        if self.child(name).is_none() {
            self.push(Element::new(name));
        }
        self.child_mut(name).expect("child was added")
    }

    /// Insert schema-ordered Word properties as python-docx's native setters do.
    pub fn word(&mut self, name: &str) -> &mut Element {
        if self.child(name).is_none() {
            let rank = property_rank(&self.name, name);
            let index = self
                .children
                .iter()
                .position(|node| match node {
                    Node::Element(element) => property_rank(&self.name, &element.name) > rank,
                    _ => false,
                })
                .unwrap_or(self.children.len());
            self.children
                .insert(index, Node::Element(Element::new(name)));
        }
        self.child_mut(name).expect("child was added")
    }

    /// Return all visible Word text recursively, including hyperlink runs.
    pub fn text(&self) -> String {
        let mut value = String::new();
        self.visit(&mut |element| {
            if element.name == "w:t" {
                value.push_str(&element.direct_text());
            }
        });
        value
    }

    /// Return this element's immediate XML text without concatenating descendants.
    pub fn direct_text(&self) -> String {
        self.children
            .iter()
            .filter_map(|node| {
                if let Node::Text(text) = node {
                    Some(text.as_str())
                } else {
                    None
                }
            })
            .collect()
    }

    /// Recursively inspect every element in document order.
    pub fn visit(&self, action: &mut impl FnMut(&Element)) {
        action(self);
        for child in self.elements() {
            child.visit(action);
        }
    }

    /// Apply a recursive edit without changing element order.
    pub fn visit_mut(&mut self, action: &mut impl FnMut(&mut Element)) {
        action(self);
        for child in self.elements_mut() {
            child.visit_mut(action);
        }
    }

    /// Detect descendants such as math, tabs, fields, and embedded OLE objects.
    pub fn contains(&self, name: &str) -> bool {
        self.name == name || self.elements().any(|element| element.contains(name))
    }

    /// Serialize XML with the original namespace declarations and qualified names.
    pub fn bytes(&self) -> Vec<u8> {
        let mut output =
            String::from("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n");
        self.write(&mut output);
        output.into_bytes()
    }

    /// Write one element with XML escaping and preserve whitespace-only text.
    fn write(&self, output: &mut String) {
        output.push('<');
        output.push_str(&self.name);
        for (key, value) in &self.attrs {
            output.push(' ');
            output.push_str(key);
            output.push_str("=\"");
            escape(value, true, output);
            output.push('"');
        }
        if self.children.is_empty() {
            output.push_str("/>");
            return;
        }
        output.push('>');
        for child in &self.children {
            match child {
                Node::Element(element) => element.write(output),
                Node::Text(text) => escape(text, false, output),
                Node::Comment(text) => {
                    output.push_str("<!--");
                    output.push_str(text);
                    output.push_str("-->");
                }
                Node::Processing(text) => {
                    output.push_str("<?");
                    output.push_str(text);
                    output.push_str("?>");
                }
            }
        }
        output.push_str("</");
        output.push_str(&self.name);
        output.push('>');
    }
}

/// Parse one OOXML part while retaining inherited and locally declared namespaces.
pub(crate) fn parse(bytes: &[u8]) -> Result<Element> {
    let text = std::str::from_utf8(bytes).context("DOCX XML part is not UTF-8")?;
    let document = roxmltree::Document::parse(text).context("DOCX contains malformed XML")?;
    let mut root = from_node(document.root_element());
    // The existing Word model ignores indentation between element-only nodes.
    // Keep literal run text and generic footnote/unknown parts' mixed whitespace.
    if matches!(
        root.name.as_str(),
        "w:document"
            | "w:styles"
            | "w:numbering"
            | "w:ftr"
            | "w:hdr"
            | "w:settings"
            | "w:comments"
            | "cp:coreProperties"
    ) {
        strip_blank_text(&mut root);
    }
    Ok(root)
}

/// Remove XML formatting whitespace while preserving explicit space-bearing run text.
fn strip_blank_text(element: &mut Element) {
    if element.attr("xml:space") == Some("preserve")
        || matches!(element.name.as_str(), "w:t" | "w:instrText" | "m:t")
    {
        return;
    }
    element
        .children
        .retain(|node| !matches!(node, Node::Text(text) if text.trim().is_empty()));
    for child in element.elements_mut() {
        strip_blank_text(child);
    }
}

/// Convert a borrowed XML node into an owned tree for deterministic modifications.
fn from_node(node: roxmltree::Node<'_, '_>) -> Element {
    let tag = node.tag_name();
    let mut element = Element::new(&qualified(node, tag.namespace(), tag.name(), false));
    for namespace in node.namespaces() {
        let inherited = node
            .parent_element()
            .and_then(|parent| parent.lookup_namespace_uri(namespace.name()));
        if inherited != Some(namespace.uri()) {
            let key = namespace
                .name()
                .map(|prefix| format!("xmlns:{prefix}"))
                .unwrap_or_else(|| "xmlns".into());
            element.set(&key, namespace.uri());
        }
    }
    for attribute in node.attributes() {
        element.set(
            &qualified(node, attribute.namespace(), attribute.name(), true),
            attribute.value(),
        );
    }
    for child in node.children() {
        if child.is_element() {
            element.push(from_node(child));
        } else if child.is_text() {
            element
                .children
                .push(Node::Text(child.text().unwrap_or_default().into()));
        } else if child.is_comment() {
            element
                .children
                .push(Node::Comment(child.text().unwrap_or_default().into()));
        } else if let Some(instruction) = child.pi() {
            let text = instruction
                .value
                .map(|value| format!("{} {value}", instruction.target))
                .unwrap_or_else(|| instruction.target.into());
            element.children.push(Node::Processing(text));
        }
    }
    element
}

/// Recover a namespace prefix while keeping XML's predefined xml attributes legal.
fn qualified(
    node: roxmltree::Node<'_, '_>,
    namespace: Option<&str>,
    name: &str,
    attribute: bool,
) -> String {
    let Some(namespace) = namespace else {
        return name.into();
    };
    if namespace == "http://www.w3.org/XML/1998/namespace" {
        return format!("xml:{name}");
    }
    if !attribute && node.lookup_namespace_uri(None) == Some(namespace) {
        return name.into();
    }
    node.namespaces()
        .find(|entry| entry.uri() == namespace && entry.name().is_some())
        .and_then(|entry| entry.name())
        .map(|prefix| format!("{prefix}:{name}"))
        .unwrap_or_else(|| name.into())
}

/// Escape XML values, retaining tabs/newlines in attributes as numeric references.
fn escape(text: &str, attribute: bool, output: &mut String) {
    for character in text.chars() {
        match character {
            '&' => output.push_str("&amp;"),
            '<' => output.push_str("&lt;"),
            '>' => output.push_str("&gt;"),
            '"' if attribute => output.push_str("&quot;"),
            '\r' => output.push_str("&#13;"),
            '\n' if attribute => output.push_str("&#10;"),
            '\t' if attribute => output.push_str("&#9;"),
            _ => output.push(character),
        }
    }
}

/// Locate Word property order for the parts modified by the document pipeline.
fn property_rank(parent: &str, name: &str) -> usize {
    let order = match parent {
        "w:p" => "w:pPr",
        "w:r" => "w:rPr",
        "w:tbl" => "w:tblPr w:tblGrid",
        "w:tc" => "w:tcPr",
        "w:tr" => "w:trPr",
        "w:trPr" => {
            "w:cnfStyle w:divId w:gridBefore w:gridAfter w:wBefore w:wAfter w:cantSplit w:trHeight w:tblHeader w:tblCellSpacing w:jc w:hidden w:ins w:del w:trPrChange"
        }
        "w:pPr" => {
            "w:pStyle w:keepNext w:keepLines w:pageBreakBefore w:framePr w:widowControl w:numPr w:suppressLineNumbers w:pBdr w:shd w:tabs w:suppressAutoHyphens w:kinsoku w:wordWrap w:overflowPunct w:topLinePunct w:autoSpaceDE w:autoSpaceDN w:bidi w:adjustRightInd w:snapToGrid w:spacing w:ind w:contextualSpacing w:mirrorIndents w:suppressOverlap w:jc w:textDirection w:textAlignment w:textboxTightWrap w:outlineLvl w:divId w:cnfStyle w:rPr w:sectPr w:pPrChange"
        }
        "w:rPr" => {
            "w:rStyle w:rFonts w:b w:bCs w:i w:iCs w:caps w:smallCaps w:strike w:dstrike w:outline w:shadow w:emboss w:imprint w:noProof w:snapToGrid w:vanish w:webHidden w:color w:spacing w:w w:kern w:position w:sz w:szCs w:highlight w:u w:effect w:bdr w:shd w:fitText w:vertAlign w:rtl w:cs w:em w:lang w:eastAsianLayout w:specVanish w:oMath w:rPrChange"
        }
        "w:style" => {
            "w:name w:aliases w:basedOn w:next w:link w:autoRedefine w:hidden w:uiPriority w:semiHidden w:unhideWhenUsed w:qFormat w:locked w:personal w:personalCompose w:personalReply w:rsid w:pPr w:rPr w:tblPr w:trPr w:tcPr w:tblStylePr"
        }
        "w:numPr" => "w:ilvl w:numId w:numberingChange w:ins",
        "w:tblPr" => {
            "w:tblStyle w:tblpPr w:tblOverlap w:bidiVisual w:tblStyleRowBandSize w:tblStyleColBandSize w:tblW w:jc w:tblCellSpacing w:tblInd w:tblBorders w:shd w:tblLayout w:tblCellMar w:tblLook w:tblCaption w:tblDescription w:tblPrChange"
        }
        "w:tcPr" => {
            "w:cnfStyle w:tcW w:gridSpan w:hMerge w:vMerge w:tcBorders w:shd w:noWrap w:tcMar w:textDirection w:tcFitText w:vAlign w:hideMark w:headers w:tcPrChange"
        }
        "w:sectPr" => {
            "w:headerReference w:footerReference w:footnotePr w:endnotePr w:type w:pgSz w:pgMar w:paperSrc w:pgBorders w:lnNumType w:pgNumType w:cols w:formProt w:vAlign w:noEndnote w:titlePg w:textDirection w:bidi w:rtlGutter w:docGrid w:printerSettings w:sectPrChange"
        }
        _ => "",
    };
    order
        .split_whitespace()
        .position(|entry| entry == name)
        .unwrap_or(usize::MAX)
}
