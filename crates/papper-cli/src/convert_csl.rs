//! Recover Zotero's style identity and stage a portable CSL beside imported Markdown.

use anyhow::{Context, Result};
use papper_core::paths::{atomic_write, home_dir};
use roxmltree::Document;
use serde_yaml::{Mapping, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashSet};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::time::Duration;

/// Staged metadata and optional style, published only after the DOCX conversion succeeds.
pub(crate) struct RestoredCsl {
    pub metadata: PathBuf,
    pub csl: Option<PathBuf>,
}

/// Zotero stores a style identity and optional citation locale, not the complete CSL.
struct Preferences {
    id: String,
    locale: Option<String>,
}

impl Preferences {
    /// Join numbered DOCX properties before reading Zotero's XML or JSON document data.
    fn read(source: &Path) -> Result<Option<Self>> {
        let mut archive = zip::ZipArchive::new(std::fs::File::open(source)?)?;
        let member = match archive.by_name("docProps/custom.xml") {
            Ok(member) => member,
            Err(zip::result::ZipError::FileNotFound) => return Ok(None),
            Err(error) => return Err(error.into()),
        };
        let mut xml = String::new();
        member.take(4 * 1024 * 1024).read_to_string(&mut xml)?;
        let document = Document::parse(&xml).context("Invalid DOCX custom properties")?;
        let mut pieces = BTreeMap::new();
        for property in document
            .root_element()
            .children()
            .filter(|node| node.is_element())
        {
            let Some(suffix) = property
                .attribute("name")
                .and_then(|name| name.strip_prefix("ZOTERO_PREF_"))
            else {
                continue;
            };
            if let Ok(index) = suffix.parse::<u32>() {
                let text: String = property
                    .descendants()
                    .filter_map(|node| node.text().filter(|_| node.is_text()))
                    .collect();
                pieces.insert(index, text);
            }
        }
        if pieces.is_empty() {
            return Ok(None);
        }
        let data: String = pieces.into_values().collect();
        let (id, locale) = if data.trim_start().starts_with('{') {
            let value: serde_json::Value =
                serde_json::from_str(&data).context("Invalid Zotero document preferences")?;
            (
                value["style"]["styleID"].as_str().unwrap_or("").to_owned(),
                value["style"]["locale"].as_str().map(str::to_owned),
            )
        } else {
            let document = Document::parse(&data).context("Invalid Zotero document preferences")?;
            let Some(style) = document
                .descendants()
                .find(|node| node.has_tag_name("style"))
            else {
                return Ok(None);
            };
            (
                style.attribute("id").unwrap_or("").to_owned(),
                style.attribute("locale").map(str::to_owned),
            )
        };
        Ok((!id.trim().is_empty()).then_some(Self {
            id,
            locale: locale.filter(|value| !value.is_empty()),
        }))
    }
}

/// Resolve local modifications first, then cached or public styles and their parent chain.
struct CslResolver {
    directories: Vec<PathBuf>,
    cache: PathBuf,
    offline: bool,
}

impl CslResolver {
    /// Discover Zotero's configured and default data locations without opening its database.
    fn new(data_dir: Option<&Path>, offline: bool) -> Self {
        let mut directories = Vec::new();
        if let Some(data) = data_dir {
            directories.push(data.join("styles"));
        } else if let Some(home) =
            std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
        {
            let home = PathBuf::from(home);
            let profile_roots = if cfg!(windows) {
                vec![
                    std::env::var_os("APPDATA")
                        .map(PathBuf::from)
                        .unwrap_or_else(|| home.join("AppData/Roaming"))
                        .join("Zotero/Zotero"),
                ]
            } else if cfg!(target_os = "macos") {
                vec![home.join("Library/Application Support/Zotero")]
            } else {
                vec![home.join(".zotero/zotero"), home.join(".zotero")]
            };
            for root in profile_roots {
                Self::profile_directories(&root, &mut directories);
            }
            directories.push(home.join("Zotero/styles"));
        }
        let hidden: Vec<_> = directories.iter().map(|path| path.join("hidden")).collect();
        directories.extend(hidden);
        Self {
            directories,
            cache: home_dir().join("cache/csl"),
            offline,
        }
    }

    /// Follow profiles.ini so a custom Zotero data directory is preferred over its default.
    fn profile_directories(root: &Path, directories: &mut Vec<PathBuf>) {
        let Ok(ini) = std::fs::read_to_string(root.join("profiles.ini")) else {
            return;
        };
        let preference = regex::Regex::new(
            r#"user_pref\("extensions\.zotero\.dataDir",\s*("(?:\\.|[^"\\])*")\s*\)"#,
        )
        .unwrap();
        let mut profiles: Vec<_> = ini
            .split('[')
            .filter(|section| section.starts_with("Profile"))
            .collect();
        profiles.sort_by_key(|section| !section.lines().any(|line| line.trim() == "Default=1"));
        for section in profiles {
            let Some(path) = section
                .lines()
                .find_map(|line| line.trim().strip_prefix("Path="))
            else {
                continue;
            };
            let profile = root.join(path);
            if let Ok(prefs) = std::fs::read_to_string(profile.join("prefs.js"))
                && !prefs.contains("user_pref(\"extensions.zotero.useDataDir\", false)")
                && let Some(captures) = preference.captures(&prefs)
                && let Ok(data) = serde_json::from_str::<String>(&captures[1])
            {
                directories.push(PathBuf::from(data).join("styles"));
            }
            // Older profiles can keep the complete data directory under the profile.
            directories.push(profile.join("zotero/styles"));
        }
    }

    /// Read and validate a CSL identity independently of its filename or XML namespace prefix.
    fn style_id(text: &str) -> Result<String> {
        let document = Document::parse(text).context("Invalid CSL XML")?;
        let root = document.root_element();
        anyhow::ensure!(
            root.has_tag_name(("http://purl.org/net/xbiblio/csl", "style")),
            "Not a CSL style"
        );
        let info = root
            .children()
            .find(|node| node.has_tag_name("info"))
            .context("CSL info is missing")?;
        let id = info
            .children()
            .find(|node| node.has_tag_name("id"))
            .and_then(|node| node.text())
            .context("CSL style ID is missing")?;
        Ok(id.trim().to_owned())
    }

    /// Treat Zotero's HTTP/HTTPS and www aliases as the same identity, preserving custom IDs.
    fn identity(id: &str) -> &str {
        id.trim()
            .trim_end_matches('/')
            .strip_prefix("https://")
            .or_else(|| id.trim().trim_end_matches('/').strip_prefix("http://"))
            .unwrap_or(id.trim().trim_end_matches('/'))
            .trim_start_matches("www.")
    }

    /// Find an installed style by its embedded ID, including files renamed or edited by users.
    fn local(&self, id: &str) -> Option<String> {
        for directory in &self.directories {
            let Ok(entries) = std::fs::read_dir(directory) else {
                continue;
            };
            let mut paths: Vec<_> = entries
                .flatten()
                .map(|entry| entry.path())
                .filter(|path| {
                    path.extension()
                        .is_some_and(|extension| extension.eq_ignore_ascii_case("csl"))
                })
                .collect();
            paths.sort();
            for path in paths {
                if let Ok(text) = std::fs::read_to_string(&path)
                    && Self::style_id(&text)
                        .is_ok_and(|found| Self::identity(&found) == Self::identity(id))
                {
                    println!("[convert] Using local CSL {}", path.display());
                    return Some(text);
                }
            }
        }
        None
    }

    /// Map only public repository identities to filenames; never fetch arbitrary document URLs.
    fn public_slug(id: &str) -> Result<&str> {
        let identity = Self::identity(id);
        let slug = identity
            .strip_prefix("zotero.org/styles/")
            .or_else(|| identity.strip_prefix("citationstyles.org/styles/"))
            .context("Custom CSL style is not installed locally; use --zotero-data-dir")?;
        anyhow::ensure!(
            !slug.is_empty()
                && slug
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_')),
            "Invalid public CSL style ID"
        );
        Ok(slug)
    }

    /// Obtain a complete style without letting damaged cache entries override a local copy.
    fn load(&self, id: &str) -> Result<String> {
        if let Some(text) = self.local(id) {
            return Ok(text);
        }
        let key: String = Sha256::digest(Self::identity(id).as_bytes())
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        let cached = self.cache.join(format!("{key}.csl"));
        if let Ok(text) = std::fs::read_to_string(&cached)
            && Self::style_id(&text).is_ok_and(|found| Self::identity(&found) == Self::identity(id))
        {
            return Ok(text);
        }
        anyhow::ensure!(
            !self.offline,
            "CSL is not available locally or in cache (--offline)"
        );
        let slug = Self::public_slug(id)?;
        let mut last_error = None;
        for folder in ["", "dependent/"] {
            let url = format!(
                "https://raw.githubusercontent.com/citation-style-language/styles/master/{folder}{slug}.csl"
            );
            let downloaded = Self::download(&url).and_then(|text| {
                anyhow::ensure!(
                    Self::identity(&Self::style_id(&text)?) == Self::identity(id),
                    "Downloaded CSL identity does not match {id}"
                );
                Ok(text)
            });
            match downloaded {
                Ok(text) => {
                    if let Err(error) = atomic_write(&cached, text.as_bytes()) {
                        eprintln!("[WARN] Could not cache CSL: {error}");
                    }
                    println!(
                        "[convert] Downloaded current public CSL {id}; the DOCX does not identify its original revision"
                    );
                    return Ok(text);
                }
                Err(error) => last_error = Some(error),
            }
        }
        Err(last_error.unwrap())
    }

    /// Fetch bounded CSL XML through the same proxy selection as Papper's other downloads.
    fn download(url: &str) -> Result<String> {
        let response = crate::tools::download_agent(Duration::from_secs(8), url)?
            .get(url)
            .set("User-Agent", "papper")
            .call()?;
        let mut text = String::new();
        response
            .into_reader()
            .take(4 * 1024 * 1024)
            .read_to_string(&mut text)?;
        Ok(text)
    }

    /// Flatten dependent styles so subsequent builds need no parent-style network access.
    fn resolve(&self, id: &str, visited: &mut HashSet<String>) -> Result<String> {
        anyhow::ensure!(
            visited.len() < 16 && visited.insert(Self::identity(id).to_owned()),
            "Cyclic or excessively deep CSL dependency: {id}"
        );
        let text = self.load(id)?;
        let document = Document::parse(&text)?;
        let root = document.root_element();
        let parent = root
            .children()
            .filter(|node| node.has_tag_name("info"))
            .flat_map(|info| info.children())
            .find(|node| {
                node.has_tag_name("link") && node.attribute("rel") == Some("independent-parent")
            })
            .and_then(|node| node.attribute("href"));
        if let Some(parent) = parent {
            let resolved = self.resolve(parent, visited)?;
            return Self::with_locale(&resolved, root.attribute("default-locale"));
        }
        anyhow::ensure!(
            root.children().any(|node| node.has_tag_name("citation")),
            "Independent CSL has no citation rules"
        );
        Ok(text)
    }

    /// Carry a dependent style or document's explicit citation locale into the portable CSL.
    fn with_locale(text: &str, locale: Option<&str>) -> Result<String> {
        let Some(locale) = locale else {
            return Ok(text.to_owned());
        };
        anyhow::ensure!(
            !locale.is_empty()
                && locale
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-'),
            "Invalid CSL locale"
        );
        let document = Document::parse(text)?;
        let root = document.root_element();
        // Edit only the root start tag, retaining comments, macros and exact local rules.
        let start = root.range().start;
        let end = start
            + text[start..]
                .find('>')
                .context("CSL root tag is incomplete")?;
        let attribute = regex::Regex::new(r#"\s+default-locale\s*=\s*(?:"[^"]*"|'[^']*')"#)?;
        let tag = attribute.replace_all(&text[start..end], "");
        Ok(format!(
            "{}{} default-locale=\"{}\"{}",
            &text[..start],
            tag,
            locale,
            &text[end..]
        ))
    }
}

impl RestoredCsl {
    /// Stage style metadata even when the original CSL cannot be obtained, keeping import usable.
    pub fn prepare(
        source: &Path,
        output: &Path,
        data_dir: Option<&Path>,
        offline: bool,
    ) -> Result<Option<Self>> {
        let Some(preferences) = Preferences::read(source)? else {
            return Ok(None);
        };
        println!("[convert] Zotero CSL identity: {}", preferences.id);
        let mut metadata = Mapping::new();
        metadata.insert(
            Value::from("zotero-style-id"),
            Value::from(preferences.id.clone()),
        );
        if let Some(locale) = &preferences.locale {
            metadata.insert(
                Value::from("zotero-style-locale"),
                Value::from(locale.clone()),
            );
        }
        let resolver = CslResolver::new(data_dir, offline);
        let csl = match resolver
            .resolve(&preferences.id, &mut HashSet::new())
            .and_then(|text| CslResolver::with_locale(&text, preferences.locale.as_deref()))
        {
            Ok(text) => {
                let csl = output.with_extension("csl");
                atomic_write(&csl, text.as_bytes())?;
                metadata.insert(
                    Value::from("csl"),
                    Value::from(csl.file_name().unwrap().to_string_lossy().into_owned()),
                );
                Some(csl)
            }
            Err(error) => {
                eprintln!(
                    "[WARN] Could not restore CSL {}: {error:#}. Style identity is retained as zotero-style-id; set csl manually before building to preserve the original citation formatting",
                    preferences.id
                );
                None
            }
        };
        let metadata_file = output.with_extension("csl.yml");
        atomic_write(&metadata_file, serde_yaml::to_string(&metadata)?.as_bytes())?;
        Ok(Some(Self {
            metadata: metadata_file,
            csl,
        }))
    }
}
