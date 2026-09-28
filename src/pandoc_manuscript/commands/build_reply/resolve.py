"""Reference, citation, equation, and TXT resolution for reply builds."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Sequence

from ...docx.equation_layout import derive_docx_equation_layout, sync_eqn_block_template_with_page_margins
from ...runtime.logging import log_debug, log_warning
from ...runtime.metadata import EffectiveMetadata, PmtSettings, load_effective_metadata, write_pandoc_metadata
from ...runtime.paths import PMT_REPLY_PROBE_DIR
from ...runtime.resources import template_root
from ..common import suppress_known_external_warnings
from ..setup import pandoc_command, pandoc_tools_env
from . import line_source as reply_line_source


LABEL_CHARS_NO_DOT = r"A-Za-z0-9_:\-"
LABEL_CONTINUATION = rf"(?:[{LABEL_CHARS_NO_DOT}]|\.(?=[{LABEL_CHARS_NO_DOT}]))"
REF_PATTERN = re.compile(rf"@((?:sec|fig|tbl|eq):[A-Za-z0-9]{LABEL_CONTINUATION}*)")
REF_BOUNDARY = rf"(?![{LABEL_CHARS_NO_DOT}]|\.(?=[{LABEL_CHARS_NO_DOT}]))"
DISPLAY_EQUATION_LABEL_PATTERN = re.compile(
    rf"(?<!\$)\$\$(?!\$)(?P<math>.*?)(?<!\$)\$\$(?!\$)\s*"
    rf"\{{#(?P<label>eq:[A-Za-z0-9]{LABEL_CONTINUATION}*)(?P<attrs>[^}}]*)\}}",
    re.DOTALL,
)
CITATION_PATTERN = re.compile(r"(?<![\w:])@([A-Za-z0-9_][A-Za-z0-9_:.#/$%&+?<>~/-]*)")
CITATION_CLUSTER_PATTERN = re.compile(r"\[([^\]\n]*@[^\]\n]*)\]")
PROBE_SENTINEL = "PANDOC_REPLY_REF_PROBE"
CITATION_PROBE_SENTINEL = "PANDOC_REPLY_CITE_PROBE"
CITATION_CLUSTER_PROBE_SENTINEL = "PANDOC_REPLY_CITE_CLUSTER_PROBE"
CROSSREF_PREFIXES = ("sec:", "fig:", "tbl:", "eq:")
IMAGE_MARKDOWN_PATTERN = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<target>[^)]*)\)(?:\s*\{[^}]*\})?")
LABELED_FIGURE_CAPTION_PATTERN = re.compile(
    rf"(?P<open>!\[)(?P<caption>[^\]\r\n]*)(?P<target>\]\([^\r\n]*?\))[ \t]*"
    rf"(?P<attribute>\{{[^}}\r\n]*#(?P<label>fig:[A-Za-z0-9]{LABEL_CONTINUATION}*)[^}}\r\n]*\}})"
)
LABELED_TABLE_CAPTION_PATTERN = re.compile(
    rf"^(?P<prefix>[ \t]*(?:>[ \t]*)*(?:Table)?:[ \t]+)"
    rf"(?P<caption>.*?)(?P<attribute>[ \t]*\{{[^}}\r\n]*"
    rf"#(?P<label>tbl:[A-Za-z0-9]{LABEL_CONTINUATION}*)[^}}\r\n]*\}}[ \t]*)$",
    re.IGNORECASE | re.MULTILINE,
)
LABELED_CAPTION_ATTRIBUTE_PATTERN = re.compile(
    r"(?m)^(?P<caption>\s*(?:Table|Figure)?:\s+.*?)"
    r"[ \t]*\{#(?:tbl|fig):[A-Za-z0-9][^}\r\n]*\}[ \t]*$"
)
STANDALONE_LABEL_ATTRIBUTE_PATTERN = re.compile(r"(?m)^[ \t]*\{#(?:eq|fig|tbl):[A-Za-z0-9][^}]*\}[ \t]*\r?\n?")
REPLY_CUSTOM_STYLE_DIV_OPEN_PATTERN = re.compile(
    r"^\s*:::\s*\{[^}\n]*custom-style\s*=\s*['\"]Reply to Reviewers['\"][^}\n]*\}\s*$"
)
DIV_CLOSE_PATTERN = re.compile(r"^\s*:::\s*$")
BR_TAG_PATTERN = re.compile(r"(?i)<br\s*/?>")
ESCAPED_ORDERED_LIST_MARKER_PATTERN = re.compile(r"(?m)^(\s*\d+)\\\.(?=\s)")
ORDERED_LIST_MARKER_PATTERN = re.compile(r"^\s*\d+\.(?=\s)")
EXTRA_BLANK_LINES_PATTERN = re.compile(r"(?:[ \t]*\r?\n){3,}")
PREFIX_WORDS = {
    "sec": "Section",
    "fig": "Figure",
    "tbl": "Table",
    "eq": "Equation",
}
REPLY_EQUATION_OPENXML_PREFIX = (
    '<w:pPr><w:tabs>'
    '<w:tab w:val="center" w:leader="none" w:pos="4888" />'
    '<w:tab w:val="right" w:leader="none" w:pos="9746" />'
    "</w:tabs></w:pPr><w:r><w:tab /></w:r>"
)
REPLY_EQUATION_OPENXML_NUMBER_TAB = '<w:r><w:tab /></w:r>'
REPLY_PROBE_DIR = PMT_REPLY_PROBE_DIR


def to_pandoc_path(path: Path) -> str:
    """Return a Pandoc-friendly path string."""
    return path.as_posix()


def style_paths_for_markdown(markdown: str | Path, style_name: str = "style.yml") -> tuple[Path, ...]:
    """Return Markdown-local and working-directory style files in priority order."""
    source_style = Path(markdown).resolve().parent / style_name
    working_style = Path.cwd().resolve() / style_name
    return tuple(path for path in dict.fromkeys((source_style, working_style)) if path.is_file())


def load_reply_metadata(reply: Path, style: Path | Sequence[str | Path]) -> EffectiveMetadata:
    """Load reply-specific Papper settings and effective Pandoc metadata once."""
    return load_effective_metadata(
        reply,
        style_paths=style if not isinstance(style, (str, Path)) else (style,),
        allow_missing_header=True,
        reply=True,
        csl_resolver=lambda relative: _reply_asset_path(reply, relative).as_posix(),
    )


def _reply_asset_path(reply: Path, relative: str) -> Path:
    """Resolve reply-relative assets before falling back to packaged resources."""
    candidate = Path(relative)
    if candidate.is_absolute():
        return candidate
    for root in (reply.resolve().parent, Path.cwd().resolve()):
        resolved = root / candidate
        if resolved.exists():
            return resolved
    return template_root() / candidate


def reply_resource_path(reply: Path) -> str:
    """Return Markdown-first resource roots for reply-side Pandoc probes."""
    roots = tuple(dict.fromkeys((reply.resolve().parent, Path.cwd().resolve())))
    return os.pathsep.join(to_pandoc_path(root) for root in roots)


def write_reply_style_metadata_file(
    effective: EffectiveMetadata,
    *,
    use_mathtype: bool = False,
) -> Path:
    """Write reply Pandoc metadata without leaking Papper-owned settings."""
    metadata = derive_docx_equation_layout(
        effective.pandoc_metadata,
        use_mathtype=use_mathtype,
    )
    metadata, tab_stops = sync_eqn_block_template_with_page_margins(
        metadata,
        effective.pmt_settings,
    )
    if tab_stops is not None:
        center_tab, right_tab = tab_stops
        log_debug(
            "[DEBUG] Synced reply eqnBlockTemplate tab stops from docxPageMargins: "
            f"center={center_tab}, right={right_tab}"
        )
    flattened_style = write_pandoc_metadata(
        metadata,
        REPLY_PROBE_DIR / "pandoc.reply.generated.yml",
    )
    return flattened_style


def run_command(cmd: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command, debug-log it, and raise with captured output on failure."""
    log_debug(f"[Run] {' '.join(cmd)}")
    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    result = subprocess.CompletedProcess(
        completed.args,
        completed.returncode,
        stdout=completed.stdout,
        stderr=suppress_known_external_warnings(completed.stderr),
    )
    if result.returncode != 0:
        result.check_returncode()
    return result


def extract_reference_labels(markdown: str) -> list[str]:
    """Return unique manuscript-style cross-reference labels used in reply text."""
    labels = sorted(set(REF_PATTERN.findall(markdown)))
    log_debug(f"[DEBUG] Found {len(labels)} manuscript-style references in reply.")
    return labels


def extract_labeled_equation_labels(markdown: str) -> list[str]:
    """Return equation labels attached to display-math blocks in reply text."""
    labels = sorted({match.group("label") for match in DISPLAY_EQUATION_LABEL_PATTERN.finditer(markdown)})
    log_debug(f"[DEBUG] Found {len(labels)} labeled reply equation block(s).")
    return labels


def extract_labeled_figure_table_labels(markdown: str) -> list[str]:
    """Return labels from reply figure and table definitions that need manuscript numbering."""
    labels = {
        match.group("label")
        for pattern in (LABELED_FIGURE_CAPTION_PATTERN, LABELED_TABLE_CAPTION_PATTERN)
        for match in pattern.finditer(markdown)
    }
    resolved_labels = sorted(labels)
    log_debug(f"[DEBUG] Found {len(resolved_labels)} labeled reply figure/table definition(s).")
    return resolved_labels


def extract_citation_keys(markdown: str) -> list[str]:
    """Return unique bibliography citation keys used in reply text."""
    keys = set(citation_keys_in_text(markdown))
    citations = sorted(keys)
    log_debug(f"[DEBUG] Found {len(citations)} bibliography citations in reply.")
    return citations


def citation_keys_in_text(text: str) -> list[str]:
    """Return bibliography citation keys from a text fragment in encounter order."""
    keys: list[str] = []
    seen: set[str] = set()
    for key in CITATION_PATTERN.findall(text):
        if key.startswith(CROSSREF_PREFIXES) or key in seen:
            continue
        keys.append(key)
        seen.add(key)
    return keys


def extract_citation_clusters(markdown: str) -> list[str]:
    """Return bracketed citation clusters such as `[@a; @b]` from reply text."""
    clusters: list[str] = []
    seen: set[str] = set()
    for match in CITATION_CLUSTER_PATTERN.finditer(markdown):
        cluster = match.group(0)
        if cluster in seen or not citation_keys_in_text(match.group(1)):
            continue
        clusters.append(cluster)
        seen.add(cluster)
    log_debug(f"[DEBUG] Found {len(clusters)} bracketed citation clusters in reply.")
    return clusters


def inline_to_text(inline: dict[str, Any]) -> str:
    """Convert a Pandoc JSON inline node to reply-safe Markdown text."""
    tag = inline.get("t")
    content = inline.get("c")

    if tag == "Str":
        return str(content)
    if tag == "Space":
        return " "
    if tag in {"SoftBreak", "LineBreak"}:
        return " "
    if tag in {"Code", "Math"} and isinstance(content, list):
        return str(content[-1])
    if tag == "Superscript":
        return f"^{inlines_to_text(content or [])}^"
    if tag in {"Emph", "Strong", "Span", "SmallCaps", "Strikeout", "Subscript"}:
        if tag == "Span" and isinstance(content, list) and len(content) >= 2:
            return inlines_to_text(content[1])
        return inlines_to_text(content or [])
    if tag == "Link" and isinstance(content, list) and len(content) >= 2:
        return inlines_to_text(content[1])
    if tag == "Cite" and isinstance(content, list) and len(content) >= 2:
        return inlines_to_text(content[1])
    if tag == "Quoted" and isinstance(content, list) and len(content) >= 2:
        return inlines_to_text(content[1])
    if tag == "RawInline" and isinstance(content, list) and len(content) >= 2:
        return str(content[1])
    return ""


def inlines_to_text(inlines: list[dict[str, Any]]) -> str:
    """Flatten Pandoc JSON inlines into normalized reply Markdown text."""
    text = "".join(inline_to_text(inline) for inline in inlines)
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def split_probe_inlines(inlines: list[dict[str, Any]], sentinel: str) -> tuple[str, list[dict[str, Any]]] | None:
    """Return a probe label and display tail from a matching Pandoc paragraph."""
    if not inlines or inlines[0].get("t") != "Str" or inlines[0].get("c") != sentinel:
        return None

    index = 1
    while index < len(inlines) and inlines[index].get("t") in {"Space", "SoftBreak", "LineBreak"}:
        index += 1
    if index >= len(inlines) or inlines[index].get("t") != "Str":
        return None

    label = str(inlines[index].get("c"))
    index += 1
    while index < len(inlines) and inlines[index].get("t") in {"Space", "SoftBreak", "LineBreak"}:
        index += 1
    return label, inlines[index:]


def extract_probe_map(
    document: dict[str, Any],
    sentinel: str,
    labels: list[str],
    unresolved_markers: tuple[str, ...],
) -> dict[str, str]:
    """Extract label-to-display text mappings from Pandoc JSON probe blocks."""
    requested = set(labels)
    resolved: dict[str, str] = {}

    for block in document.get("blocks", []):
        if block.get("t") != "Para":
            continue
        inlines = block.get("c", [])
        probe = split_probe_inlines(inlines, sentinel)
        if probe is None:
            continue
        label, display_inlines = probe
        display = inlines_to_text(display_inlines)
        if display and label in requested and not any(marker in display for marker in unresolved_markers):
            resolved[label] = display

    return resolved


def write_probe_file(name: str, lines: list[str]) -> Path:
    """Write a stable probe file under .papper without relying on tempfile ACLs."""
    REPLY_PROBE_DIR.mkdir(parents=True, exist_ok=True)
    probe_path = REPLY_PROBE_DIR / name
    probe_path.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    return probe_path


def resolve_reference_map(
    manuscript: Path,
    style: Path,
    labels: list[str],
    from_format: str,
) -> dict[str, str]:
    """Resolve reply labels using the manuscript's pandoc-crossref numbering."""
    if not labels:
        return {}

    probe_path = write_probe_file(
        "reference-probe.md",
        [f"{PROBE_SENTINEL} {label} @{label}" for label in labels],
    )
    cmd = [
        pandoc_command(),
        "--metadata-file",
        str(style),
        "--resource-path",
        reply_resource_path(manuscript),
        "-f",
        from_format,
        "-t",
        "json",
        "--filter",
        "pandoc-crossref",
        str(manuscript),
        str(probe_path),
    ]
    result = run_command(cmd, env=pandoc_tools_env())
    if result.stderr.strip():
        log_warning(result.stderr.strip())

    document = json.loads(result.stdout)
    resolved = extract_probe_map(document, PROBE_SENTINEL, labels, ("¿",))
    missing = [label for label in labels if label not in resolved]
    if missing:
        log_warning("[WARN] These labels were not resolved from manuscript.md:")
        for label in missing:
            log_warning(f"  - {label}")
    log_debug(f"[DEBUG] Resolved {len(resolved)} references from manuscript numbering.")
    return resolved


def resolve_citation_map(
    manuscript: Path,
    style: Path,
    citations: list[str],
    from_format: str,
) -> dict[str, str]:
    """Resolve bibliography citations using the manuscript's citeproc numbering."""
    if not citations:
        return {}

    probe_path = write_probe_file(
        "citation-probe.md",
        [f"{CITATION_PROBE_SENTINEL} {key} [@{key}]" for key in citations],
    )
    cmd = [
        pandoc_command(),
        "--metadata-file",
        str(style),
        "--resource-path",
        reply_resource_path(manuscript),
        "-f",
        from_format,
        "-t",
        "json",
        "--filter",
        "pandoc-crossref",
        "--citeproc",
        str(manuscript),
        str(probe_path),
    ]
    result = run_command(cmd, env=pandoc_tools_env())
    if result.stderr.strip():
        log_warning(result.stderr.strip())

    document = json.loads(result.stdout)
    resolved = extract_probe_map(document, CITATION_PROBE_SENTINEL, citations, ("???",))
    missing = [key for key in citations if key not in resolved]
    if missing:
        log_warning("[WARN] These citation keys were not resolved from manuscript bibliography:")
        for key in missing:
            log_warning(f"  - {key}")
    log_debug(f"[DEBUG] Resolved {len(resolved)} citations from manuscript citeproc output.")
    return resolved


def resolve_citation_cluster_map(
    manuscript: Path,
    style: Path,
    citation_clusters: list[str],
    from_format: str,
) -> dict[str, str]:
    """Resolve bracketed citation clusters so citeproc keeps sorting and delimiters."""
    if not citation_clusters:
        return {}

    cluster_ids = [str(index) for index, _ in enumerate(citation_clusters)]
    probe_path = write_probe_file(
        "citation-cluster-probe.md",
        [
            f"{CITATION_CLUSTER_PROBE_SENTINEL} {cluster_id} {cluster}"
            for cluster_id, cluster in zip(cluster_ids, citation_clusters)
        ],
    )
    cmd = [
        pandoc_command(),
        "--metadata-file",
        str(style),
        "--resource-path",
        reply_resource_path(manuscript),
        "-f",
        from_format,
        "-t",
        "json",
        "--filter",
        "pandoc-crossref",
        "--citeproc",
        str(manuscript),
        str(probe_path),
    ]
    result = run_command(cmd, env=pandoc_tools_env())
    if result.stderr.strip():
        log_warning(result.stderr.strip())

    document = json.loads(result.stdout)
    resolved_by_id = extract_probe_map(document, CITATION_CLUSTER_PROBE_SENTINEL, cluster_ids, ("???",))
    resolved = {
        cluster: resolved_by_id[cluster_id]
        for cluster_id, cluster in zip(cluster_ids, citation_clusters)
        if cluster_id in resolved_by_id
    }
    missing = [cluster for cluster_id, cluster in zip(cluster_ids, citation_clusters) if cluster_id not in resolved_by_id]
    if missing:
        log_warning("[WARN] These citation clusters were not resolved from manuscript bibliography:")
        for cluster in missing:
            log_warning(f"  - {cluster}")
    log_debug(f"[DEBUG] Resolved {len(resolved)} citation clusters from manuscript citeproc output.")
    return resolved


def number_only(label: str, display: str) -> str:
    """Strip a cross-reference prefix when reply prose already supplies it."""
    prefix = PREFIX_WORDS.get(label.split(":", 1)[0])
    if not prefix:
        return display
    pattern = re.compile(rf"^{re.escape(prefix)}\s+", re.IGNORECASE)
    return pattern.sub("", display).strip()


def replace_references(markdown: str, reference_map: dict[str, str]) -> str:
    """Replace reply reference tokens with manuscript-derived display numbers."""
    resolved = markdown
    for label in sorted(reference_map, key=len, reverse=True):
        display = reference_map[label]
        short = number_only(label, display)
        escaped = re.escape(label)
        prefix = PREFIX_WORDS.get(label.split(":", 1)[0])

        if prefix:
            resolved = re.sub(
                rf"\b{prefix}\s+\[@{escaped}\]",
                f"{prefix} {short}",
                resolved,
                flags=re.IGNORECASE,
            )
            resolved = re.sub(
                rf"\b{prefix}\s+@{escaped}{REF_BOUNDARY}",
                f"{prefix} {short}",
                resolved,
                flags=re.IGNORECASE,
            )

        resolved = re.sub(rf"\[@{escaped}\]", display, resolved)
        resolved = re.sub(rf"@{escaped}{REF_BOUNDARY}", display, resolved)

    return resolved


def caption_has_number_prefix(caption: str, kind: str, label_prefix: str, display: str) -> bool:
    """Return whether a reply caption already starts with a manual or resolved number prefix."""
    stripped = caption.lstrip()
    if re.match(rf"{re.escape(display)}(?=\s|$)", stripped, flags=re.IGNORECASE):
        return True
    return bool(
        re.match(
            rf"{kind}\s+(?:\[?@{label_prefix}:[A-Za-z0-9]|[A-Za-z]*\d)",
            stripped,
            flags=re.IGNORECASE,
        )
    )


def add_manuscript_caption_numbers(markdown: str, reference_map: dict[str, str]) -> str:
    """Prefix copied reply figure/table captions with their manuscript-derived numbers."""
    figure_count = 0
    table_count = 0

    def replace_figure(match: re.Match[str]) -> str:
        """Add the figure number and attach its attribute block directly for Pandoc parsing."""
        nonlocal figure_count
        label = match.group("label")
        display = reference_map.get(label)
        caption = match.group("caption")
        if not display:
            return match.group(0)
        suffix = f"{match.group('target')}{match.group('attribute')}"
        if caption_has_number_prefix(caption, "Figure", "fig", display):
            return f"{match.group('open')}{caption}{suffix}"
        figure_count += 1
        separator = " " if caption else ""
        return f"{match.group('open')}{display}{separator}{caption}{suffix}"

    def replace_table(match: re.Match[str]) -> str:
        """Add the resolved manuscript table number unless the caption already has one."""
        nonlocal table_count
        label = match.group("label")
        display = reference_map.get(label)
        caption = match.group("caption")
        if not display or caption_has_number_prefix(caption, "Table", "tbl", display):
            return match.group(0)
        table_count += 1
        separator = " " if caption else ""
        return f"{match.group('prefix')}{display}{separator}{caption}{match.group('attribute')}"

    resolved = LABELED_FIGURE_CAPTION_PATTERN.sub(replace_figure, markdown)
    resolved = LABELED_TABLE_CAPTION_PATTERN.sub(replace_table, resolved)
    if figure_count or table_count:
        log_debug(
            "[DEBUG] Added manuscript numbering to copied reply captions: "
            f"figures={figure_count}, tables={table_count}"
        )
    return resolved


def compact_display_math_for_inline(math: str) -> str:
    """Collapse display-math line breaks so Pandoc keeps the tab-layout formula inline."""
    return re.sub(r"[ \t]*\r?\n[ \t]*", " ", math.strip())


def equation_label_number(label: str, reference_map: dict[str, str]) -> str | None:
    """Return a parenthesized equation number resolved from manuscript crossrefs."""
    display = reference_map.get(label)
    if not display:
        return None

    short = number_only(label, display)
    if re.fullmatch(r"\(.+\)", short):
        return short
    return f"({short})"


def raw_openxml_inline(xml: str) -> str:
    """Wrap a small OpenXML fragment as a Pandoc raw inline."""
    return f"`{xml}`{{=openxml}}"


def equation_openxml_prefix_from_metadata(
    pandoc_metadata: dict[str, Any],
    pmt_settings: PmtSettings | None = None,
) -> str:
    """Return the reply equation OpenXML prefix aligned with current metadata."""
    synced_metadata, _ = sync_eqn_block_template_with_page_margins(
        {**pandoc_metadata, "eqnBlockTemplate": REPLY_EQUATION_OPENXML_PREFIX},
        pmt_settings or PmtSettings.model_validate({}),
    )
    template = synced_metadata.get("eqnBlockTemplate")
    return template if isinstance(template, str) else REPLY_EQUATION_OPENXML_PREFIX


def replace_labeled_equation_blocks(
    markdown: str,
    reference_map: dict[str, str],
    pandoc_metadata: dict[str, Any] | None = None,
    pmt_settings: PmtSettings | None = None,
) -> str:
    """Render labeled reply equations with manuscript numbers and Word tab stops."""
    replacements = 0
    openxml_prefix = equation_openxml_prefix_from_metadata(pandoc_metadata or {}, pmt_settings)

    def replace_match(match: re.Match[str]) -> str:
        """Return a tab-layout equation paragraph or keep unresolved syntax unchanged."""
        nonlocal replacements
        label = match.group("label")
        number = equation_label_number(label, reference_map)
        if number is None:
            return match.group(0)

        replacements += 1
        math = compact_display_math_for_inline(match.group("math"))
        return (
            f"{raw_openxml_inline(openxml_prefix)}"
            f"${math}$"
            f"{raw_openxml_inline(REPLY_EQUATION_OPENXML_NUMBER_TAB)}"
            f"{number}"
        )

    resolved = DISPLAY_EQUATION_LABEL_PATTERN.sub(replace_match, markdown)
    if replacements:
        log_debug(f"[DEBUG] Formatted {replacements} labeled reply equation block(s) with manuscript numbering.")
    return resolved


def strip_labeled_equation_attributes(markdown: str) -> str:
    """Remove reply-side equation labels while preserving display-math Markdown."""

    def replace_match(match: re.Match[str]) -> str:
        """Return the original display equation without the Pandoc label attribute."""
        return f"$${match.group('math')}$$"

    return DISPLAY_EQUATION_LABEL_PATTERN.sub(replace_match, markdown)


def image_placeholder(match: re.Match[str]) -> str:
    """Return a readable placeholder for an image removed from TXT output."""
    alt = match.group("alt").strip()
    target = match.group("target").strip().split(None, 1)[0]
    label = alt or target or "image"
    return f"[Image: {label}]"


def strip_reply_custom_style_divs(markdown: str) -> str:
    """Remove reply-only custom-style div wrappers from TXT output."""
    stripped_lines: list[str] = []
    reply_div_depth = 0
    for line in markdown.splitlines(keepends=True):
        line_text = line.strip()
        if REPLY_CUSTOM_STYLE_DIV_OPEN_PATTERN.fullmatch(line_text):
            reply_div_depth += 1
            continue
        if reply_div_depth and DIV_CLOSE_PATTERN.fullmatch(line_text):
            reply_div_depth -= 1
            continue
        stripped_lines.append(line)
    return "".join(stripped_lines)


def normalize_txt_markdown_spacing(markdown: str) -> str:
    """Collapse cleanup leftovers to at most one blank line."""
    return EXTRA_BLANK_LINES_PATTERN.sub("\n\n", markdown).strip("\r\n")


def unescape_ordered_list_markers(markdown: str) -> str:
    """Restore escaped ordered-list markers such as `1\\.` in TXT output."""
    return ESCAPED_ORDERED_LIST_MARKER_PATTERN.sub(r"\1.", markdown)


def ensure_blank_line_before_ordered_lists(markdown: str) -> str:
    """Reinsert one blank line before an ordered-list item after prose or captions."""
    lines = markdown.splitlines()
    normalized_lines: list[str] = []
    for line in lines:
        if (
            ORDERED_LIST_MARKER_PATTERN.match(line)
            and normalized_lines
            and normalized_lines[-1].strip()
            and not ORDERED_LIST_MARKER_PATTERN.match(normalized_lines[-1])
        ):
            normalized_lines.append("")
        normalized_lines.append(line)
    return "\n".join(normalized_lines)


def render_reply_txt_markdown(markdown: str) -> str:
    """Prepare resolved reply Markdown for journal TXT submission."""
    text = strip_reply_custom_style_divs(markdown)
    text = BR_TAG_PATTERN.sub("", text)
    text = unescape_ordered_list_markers(text)
    text = ensure_blank_line_before_ordered_lists(text)
    text = IMAGE_MARKDOWN_PATTERN.sub(image_placeholder, text)
    text = strip_labeled_equation_attributes(text)
    text = LABELED_CAPTION_ATTRIBUTE_PATTERN.sub(lambda match: match.group("caption"), text)
    text = STANDALONE_LABEL_ATTRIBUTE_PATTERN.sub("", text)
    text = normalize_txt_markdown_spacing(text)
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def replace_citations(
    markdown: str,
    citation_map: dict[str, str],
    citation_cluster_map: dict[str, str] | None = None,
) -> str:
    """Replace bibliography citation tokens with manuscript-derived numbers."""
    cluster_map = citation_cluster_map or {}
    protected_clusters: list[str] = []

    def replace_cluster(match: re.Match[str]) -> str:
        """Replace a complete citation cluster before touching bare keys."""
        cluster = match.group(0)
        display = cluster_map.get(cluster)
        if display:
            return display

        placeholder = f"@@PMT_CITE_CLUSTER_{len(protected_clusters)}@@"
        protected_clusters.append(cluster)
        return placeholder

    resolved = CITATION_CLUSTER_PATTERN.sub(replace_cluster, markdown)
    for key in sorted(citation_map, key=len, reverse=True):
        display = citation_map[key]
        escaped = re.escape(key)
        resolved = re.sub(rf"\[@{escaped}\]", display, resolved)
        resolved = re.sub(rf"(?<![\w:])@{escaped}\b", display, resolved)

    for index, cluster in enumerate(protected_clusters):
        resolved = resolved.replace(f"@@PMT_CITE_CLUSTER_{index}@@", cluster)
    return resolved


def resolve_reply_markdown(
    reply_text: str,
    manuscript: Path,
    manuscript_line_source: Path,
    flattened_style: Path,
    effective: EffectiveMetadata,
    from_format: str,
    *,
    format_labeled_equations: bool,
) -> str:
    """Resolve manuscript-derived reply placeholders before writing an output format."""
    labels = extract_reference_labels(reply_text)
    labels += extract_labeled_figure_table_labels(reply_text)
    if format_labeled_equations:
        labels += extract_labeled_equation_labels(reply_text)
    labels = sorted(set(labels))
    citations = extract_citation_keys(reply_text)
    citation_clusters = extract_citation_clusters(reply_text)
    reference_map = resolve_reference_map(manuscript, flattened_style, labels, from_format)
    citation_map = resolve_citation_map(manuscript, flattened_style, citations, from_format)
    citation_cluster_map = resolve_citation_cluster_map(manuscript, flattened_style, citation_clusters, from_format)
    resolved_text = reply_line_source.resolve_line_regexes(reply_text, manuscript_line_source)
    if format_labeled_equations:
        resolved_text = replace_labeled_equation_blocks(
            resolved_text,
            reference_map,
            effective.pandoc_metadata,
            effective.pmt_settings,
        )
    resolved_text = add_manuscript_caption_numbers(resolved_text, reference_map)
    resolved_text = replace_references(resolved_text, reference_map)
    return replace_citations(resolved_text, citation_map, citation_cluster_map)
