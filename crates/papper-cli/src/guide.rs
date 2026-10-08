//! Browse embedded authoring guides by Markdown heading, with compact topic indexes.

use crate::{GuideArgs, GuideSection};
use anyhow::{Result, bail, ensure};
use pulldown_cmark::{Event, HeadingLevel, Parser, Tag, TagEnd};
use std::io::Write;
use std::ops::Range;

/// Keep one heading's address, source range, and first-paragraph description together.
struct Topic {
    title: String,
    slug: String,
    path: String,
    level: HeadingLevel,
    range: Range<usize>,
    summary: String,
}

/// Index real Markdown headings while retaining the original examples for topic output.
struct Guide<'a> {
    name: &'static str,
    title: &'static str,
    source: &'a str,
    topics: Vec<Topic>,
}

impl<'a> Guide<'a> {
    /// Extract heading ranges and plain descriptions without interpreting fenced examples.
    fn parse(name: &'static str, title: &'static str, source: &'a str) -> Self {
        let mut topics = Vec::<Topic>::new();
        let mut heading = None;
        let mut paragraph = false;
        for (event, range) in Parser::new(source).into_offset_iter() {
            match event {
                Event::Start(Tag::Heading { level, .. }) => {
                    heading = Some((level, range.start, String::new()));
                    paragraph = false;
                }
                Event::End(TagEnd::Heading(_)) => {
                    if let Some((level, start, title)) = heading.take()
                        && level != HeadingLevel::H1
                    {
                        topics.push(Topic {
                            slug: slug(&title),
                            title,
                            path: String::new(),
                            level,
                            range: start..source.len(),
                            summary: String::new(),
                        });
                    }
                }
                Event::Start(Tag::Paragraph) => {
                    paragraph = topics.last().is_some_and(|topic| topic.summary.is_empty());
                }
                Event::End(TagEnd::Paragraph) => paragraph = false,
                Event::Text(text) | Event::Code(text) => {
                    if let Some((_, _, title)) = &mut heading {
                        title.push_str(&text);
                    } else if paragraph && let Some(topic) = topics.last_mut() {
                        topic.summary.push_str(&text);
                    }
                }
                Event::SoftBreak | Event::HardBreak => {
                    if paragraph && let Some(topic) = topics.last_mut() {
                        topic.summary.push(' ');
                    }
                }
                _ => {}
            }
        }
        let mut parents = Vec::<(HeadingLevel, String)>::new();
        for index in 0..topics.len() {
            let end = topics[index + 1..]
                .iter()
                .find(|next| next.level <= topics[index].level)
                .map_or(source.len(), |next| next.range.start);
            let topic = &mut topics[index];
            topic.range.end = end;
            while parents
                .last()
                .is_some_and(|(level, _)| *level >= topic.level)
            {
                parents.pop();
            }
            topic.path = parents.last().map_or_else(
                || topic.slug.clone(),
                |(_, parent)| format!("{parent}/{}", topic.slug),
            );
            parents.push((topic.level, topic.path.clone()));
            topic.summary = summary(&topic.summary);
        }
        Self {
            name,
            title,
            source,
            topics,
        }
    }

    /// Print discoverable topic paths and bounded descriptions rather than full examples.
    fn index(&self) -> String {
        let mut output = format!(
            "# {}\n\nRead a topic: `papper guide {} <topic>`. Full guide: `papper guide {} --full`.\n\n",
            self.title, self.name, self.name,
        );
        for topic in self
            .topics
            .iter()
            .filter(|topic| topic.level == HeadingLevel::H2)
        {
            output.push_str(&format!(
                "- `{}` ({}): {}\n",
                topic.path, topic.title, topic.summary,
            ));
        }
        output
    }

    /// Resolve an exact path or an unambiguous heading name without guessing on invalid input.
    fn find(&self, query: &str) -> Result<&Topic> {
        let key = query.split('/').map(slug).collect::<Vec<_>>().join("/");
        if let Some(topic) = self.topics.iter().find(|topic| topic.path == key) {
            return Ok(topic);
        }
        let matches: Vec<_> = self
            .topics
            .iter()
            .filter(|topic| topic.slug == key)
            .collect();
        match matches.as_slice() {
            [topic] => Ok(topic),
            [] => bail!(
                "Unknown {} topic: {query}. Run `papper guide {}` to list topics.",
                self.name,
                self.name,
            ),
            _ => bail!(
                "Ambiguous {} topic: {query}. Use one of: {}",
                self.name,
                matches
                    .iter()
                    .map(|topic| topic.path.as_str())
                    .collect::<Vec<_>>()
                    .join(", "),
            ),
        }
    }

    /// Return a topic's own text and child index, or the complete guide when no topic is selected.
    fn content(&self, query: Option<&str>) -> Result<String> {
        let mut range = 0..self.source.len();
        let mut navigation = String::new();
        if let Some(query) = query {
            let topic = self.find(query)?;
            range = topic.range.clone();
            let descendants = self.topics.iter().filter(|child| {
                child.range.start > topic.range.start && child.range.start < topic.range.end
            });
            for child in descendants {
                range.end = range.end.min(child.range.start);
                if child
                    .path
                    .rsplit_once('/')
                    .is_some_and(|(parent, _)| parent == topic.path)
                {
                    navigation.push_str(&format!(
                        "- `{}` ({}): {}\n",
                        child.path, child.title, child.summary,
                    ));
                }
            }
        }
        let mut output = command_links(&self.source[range]).trim_end().to_owned();
        if !navigation.is_empty() {
            output.push_str(&format!(
                "\n\nRead a subtopic: `papper guide {} <topic>`.\n\n",
                self.name,
            ));
            output.push_str(&navigation);
        }
        Ok(output.trim_end().to_owned() + "\n")
    }
}

/// Normalize heading titles and user input to lowercase, hyphen-separated topic addresses.
fn slug(title: &str) -> String {
    // Display numbering is not part of a topic's address, e.g. "2. Cell Merging".
    let title = title.trim();
    let title = title
        .split_once(". ")
        .filter(|(prefix, _)| {
            !prefix.is_empty() && prefix.chars().all(|character| character.is_ascii_digit())
        })
        .map_or(title, |(_, remainder)| remainder);
    let mut result = String::new();
    for character in title.chars().flat_map(char::to_lowercase) {
        if character.is_alphanumeric() {
            result.push(character);
        } else if !result.is_empty() && !result.ends_with('-') {
            result.push('-');
        }
    }
    result.trim_end_matches('-').to_owned()
}

/// Bound a plain-text first paragraph at a word boundary to keep indexes inexpensive.
fn summary(paragraph: &str) -> String {
    let mut output = String::new();
    for word in paragraph.split_whitespace() {
        if !output.is_empty() && output.chars().count() + word.chars().count() + 1 > 160 {
            output.push_str("...");
            break;
        }
        if !output.is_empty() {
            output.push(' ');
        }
        output.push_str(word);
    }
    output
}

/// Replace guide-file links using parser source ranges, preserving fenced code and other links.
fn command_links(text: &str) -> String {
    let mut replacements = Vec::new();
    for (event, range) in Parser::new(text).into_offset_iter() {
        if let Event::Start(Tag::Link { dest_url, .. }) = event {
            let (file, anchor) = dest_url
                .split_once('#')
                .map_or((dest_url.as_ref(), None), |(file, anchor)| {
                    (file, Some(anchor))
                });
            let guide = match file {
                "manuscript-syntax.md" => "syntax",
                "style-configuration.md" => "style",
                _ => continue,
            };
            let command = anchor.map_or_else(
                || format!("`papper guide {guide}`"),
                |anchor| format!("`papper guide {guide} {anchor}`"),
            );
            replacements.push((range, command));
        }
    }
    let mut output = text.to_owned();
    for (range, command) in replacements.into_iter().rev() {
        output.replace_range(range, &command);
    }
    output
}

/// Browse guides embedded in the executable without loading engines or project resources.
pub fn print(args: GuideArgs) -> Result<()> {
    ensure!(
        args.section != GuideSection::All || args.subsection.is_none(),
        "Select syntax or style before a topic, e.g. `papper guide syntax equations`."
    );
    let syntax = Guide::parse(
        "syntax",
        "Manuscript Syntax",
        include_str!("../../../docs/manuscript-syntax.md"),
    );
    let style = Guide::parse(
        "style",
        "Style Configuration",
        include_str!("../../../docs/style-configuration.md"),
    );
    let guides: &[&Guide<'_>] = match args.section {
        GuideSection::All => &[&syntax, &style],
        GuideSection::Syntax => &[&syntax],
        GuideSection::Style => &[&style],
    };
    let mut output = String::new();
    for guide in guides {
        if !output.is_empty() {
            output.push('\n');
        }
        if args.full || args.subsection.is_some() {
            output.push_str(&guide.content(args.subsection.as_deref())?);
        } else {
            output.push_str(&guide.index());
        }
    }
    std::io::stdout().lock().write_all(output.as_bytes())?;
    Ok(())
}
