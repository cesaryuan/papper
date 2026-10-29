//! Apply Word crop rectangles to vector canvases without rendering or replacing drawing content.

use anyhow::{Context, Result, bail, ensure};

/// Select a native vector format, leaving raster decoding to the existing PNG crop path.
pub(super) fn crop(
    bytes: &[u8],
    target: &str,
    crop: [i32; 4],
) -> Result<Option<(Vec<u8>, &'static str)>> {
    let extension = target.rsplit('.').next().unwrap_or("").to_ascii_lowercase();
    match extension.as_str() {
        "svg" => Ok(Some((crop_svg(bytes, crop)?, "svg"))),
        "emf" => Ok(Some((crop_emf(bytes, crop)?, "emf"))),
        "wmf" => Ok(Some((WmfCrop::apply(bytes, crop)?, "wmf"))),
        _ => Ok(None),
    }
}

/// Keep the original SVG viewport inside a smaller outer canvas, including its letterboxing.
fn crop_svg(bytes: &[u8], crop: [i32; 4]) -> Result<Vec<u8>> {
    let source = std::str::from_utf8(bytes).context("SVG is not UTF-8")?;
    let document = roxmltree::Document::parse_with_options(
        source,
        roxmltree::ParsingOptions {
            allow_dtd: true,
            ..Default::default()
        },
    )?;
    let root = document.root_element();
    ensure!(
        root.has_tag_name(("http://www.w3.org/2000/svg", "svg")),
        "Invalid SVG root"
    );
    // usvg only measures the viewport here. Serialize the original XML, never its
    // normalized tree, so fonts, paths, styles and existing raster resources survive.
    let size = usvg::Tree::from_str(source, &usvg::Options::default())?.size();
    let width = f64::from(size.width());
    let height = f64::from(size.height());
    let [left, top, right, bottom] = crop.map(|side| f64::from(side) / 100_000.0);
    let visible_width = width * (1.0 - left - right);
    let visible_height = height * (1.0 - top - bottom);
    ensure!(
        visible_width > 0.0 && visible_height > 0.0,
        "Empty SVG crop"
    );
    let range = root.range();
    let mut original = source[range.clone()].to_owned();
    // x/y have no effect on an outermost SVG, but would offset a nested SVG.
    let mut edits = Vec::new();
    for coordinate in ["x", "y"] {
        if root.attribute(coordinate).is_some() {
            let (mut position, value) = super::attribute_edit(root, source, coordinate, "0");
            position.start -= range.start;
            position.end -= range.start;
            edits.push((position, value));
        }
    }
    edits.sort_by_key(|(position, _)| position.start);
    for (position, value) in edits.into_iter().rev() {
        original.replace_range(position, &value);
    }
    // The intermediate viewport keeps percentage dimensions relative to the full
    // source canvas. Editing only viewBox would stretch preserveAspectRatio='none'
    // drawings and move letterboxed content. It also clips negative-crop padding.
    let output = format!(
        "{}<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{visible_width}\" height=\"{visible_height}\" \
         viewBox=\"{} {} {visible_width} {visible_height}\" \
         style=\"width:{visible_width}px!important;height:{visible_height}px!important;overflow:hidden!important\">\
         <svg width=\"{width}\" height=\"{height}\" viewBox=\"0 0 {width} {height}\" \
         style=\"x:0!important;y:0!important;width:{width}px!important;height:{height}px!important;overflow:hidden!important\">\
         {original}</svg></svg>{}",
        &source[..range.start],
        width * left,
        height * top,
        &source[range.end..],
    );
    Ok(output.into_bytes())
}

/// Calculate integer metafile edges without overflowing coordinates or collapsing a canvas.
fn cropped_bounds(bounds: [i32; 4], crop: [i32; 4]) -> Result<[i32; 4]> {
    let [left, top, right, bottom] = bounds.map(f64::from);
    ensure!(right > left && bottom > top, "Metafile has an empty canvas");
    let values = [
        left + (right - left) * f64::from(crop[0]) / 100_000.0,
        top + (bottom - top) * f64::from(crop[1]) / 100_000.0,
        right - (right - left) * f64::from(crop[2]) / 100_000.0,
        bottom - (bottom - top) * f64::from(crop[3]) / 100_000.0,
    ];
    let mut result = [0; 4];
    for (slot, value) in result.iter_mut().zip(values) {
        ensure!(
            value.round() >= i32::MIN as f64 && value.round() <= i32::MAX as f64,
            "Metafile crop exceeds coordinate range"
        );
        *slot = value.round() as i32;
    }
    ensure!(
        result[2] > result[0] && result[3] > result[1],
        "Crop is smaller than metafile coordinate precision"
    );
    Ok(result)
}

/// Intersect source and cropped canvases, leaving outside-source padding empty.
fn intersection(first: [i32; 4], second: [i32; 4]) -> [i32; 4] {
    let left = first[0].max(second[0]);
    let top = first[1].max(second[1]);
    [
        left,
        top,
        first[2].min(second[2]).max(left),
        first[3].min(second[3]).max(top),
    ]
}

/// Adjust an EMF's physical frame and add a base clip while preserving every drawing record.
fn crop_emf(bytes: &[u8], crop: [i32; 4]) -> Result<Vec<u8>> {
    ensure!(
        bytes.len() >= 88
            && u32_at(bytes, 0)? == 1
            && bytes.get(40..44) == Some(b" EMF".as_slice()),
        "Invalid EMF header"
    );
    let header_size = u32_at(bytes, 4)? as usize;
    ensure!(
        header_size >= 88 && header_size <= bytes.len() && header_size.is_multiple_of(4),
        "Invalid EMF header size"
    );
    ensure!(
        u32_at(bytes, 48)? as usize == bytes.len(),
        "Invalid EMF byte count"
    );
    let mut offset = header_size;
    let mut count = 1;
    let mut last_type = 1;
    while offset < bytes.len() {
        last_type = u32_at(bytes, offset)?;
        let size = u32_at(bytes, offset + 4)? as usize;
        ensure!(
            size >= 8 && size.is_multiple_of(4) && size <= bytes.len() - offset,
            "Invalid EMF record size"
        );
        offset += size;
        count += 1;
    }
    ensure!(
        last_type == 14 && count == u32_at(bytes, 52)?,
        "Incomplete EMF records"
    );
    let original_frame = [
        i32_at(bytes, 24)?,
        i32_at(bytes, 28)?,
        i32_at(bytes, 32)?,
        i32_at(bytes, 36)?,
    ];
    let frame = cropped_bounds(original_frame, crop)?;
    let mut original_pixels = [0; 4];
    let mut pixels = [0; 4];
    for axis in 0..2 {
        let device = i32_at(bytes, 72 + axis * 4)?;
        let microns = if header_size >= 108 {
            i32_at(bytes, 100 + axis * 4)?
        } else {
            0
        };
        let millimeters = i32_at(bytes, 80 + axis * 4)?;
        ensure!(
            device > 0 && (microns > 0 || millimeters > 0),
            "Invalid EMF reference device"
        );
        let scale = if microns > 0 {
            f64::from(device) * 10.0 / f64::from(microns)
        } else {
            f64::from(device) / (f64::from(millimeters) * 100.0)
        };
        for index in [axis, axis + 2] {
            let value = (f64::from(frame[index]) * scale).round();
            let original = (f64::from(original_frame[index]) * scale).round();
            ensure!(
                value > i32::MIN as f64
                    && value < i32::MAX as f64
                    && original > i32::MIN as f64
                    && original < i32::MAX as f64,
                "EMF crop exceeds device coordinate range"
            );
            pixels[index] = value as i32;
            original_pixels[index] = original as i32;
        }
        // Physical EMF frames can be narrower than a reference-device pixel.
        pixels[axis + 2] = pixels[axis + 2].max(pixels[axis] + 1);
    }
    let clip = intersection(original_pixels, pixels);
    let mut result = bytes[..header_size].to_vec();
    result.extend(30u32.to_le_bytes()); // EMR_INTERSECTCLIPRECT, in the initial device coordinates.
    result.extend(24u32.to_le_bytes());
    for value in clip {
        result.extend(value.to_le_bytes());
    }
    // A metaregion survives later source clip resets without adding a SaveDC
    // level that would change authored absolute RestoreDC indices.
    result.extend(28u32.to_le_bytes()); // EMR_SETMETARGN.
    result.extend(8u32.to_le_bytes());
    result.extend_from_slice(&bytes[header_size..]);
    for (index, value) in pixels.into_iter().chain(frame).enumerate() {
        result[8 + index * 4..12 + index * 4].copy_from_slice(&value.to_le_bytes());
    }
    let length = u32::try_from(result.len())?;
    result[48..52].copy_from_slice(&length.to_le_bytes());
    result[52..56].copy_from_slice(
        &count
            .checked_add(2)
            .context("Too many EMF records")?
            .to_le_bytes(),
    );
    Ok(result)
}

/// Track logical windows separately from the rewritten crop windows and saved DC states.
struct WmfCrop {
    crop: [i32; 4],
    window: [i32; 4], // x origin, y origin, x extent, y extent (extents may be negative).
    saved: Vec<[i32; 4]>,
    output: Vec<u8>,
    max_words: u32,
}

impl WmfCrop {
    /// Rewrite placeable anisotropic WMFs, refusing mappings that cannot be preserved exactly.
    fn apply(bytes: &[u8], crop: [i32; 4]) -> Result<Vec<u8>> {
        ensure!(
            bytes.len() >= 40
                && u32_at(bytes, 0)? == 0x9ac6_cdd7
                && u16_at(bytes, 24)? == 9
                && u16_at(bytes, 14)? != 0,
            "WMF crop requires a placeable WMF header"
        );
        ensure!(
            usize::try_from(u32_at(bytes, 28)?)?.checked_mul(2) == Some(bytes.len() - 22),
            "Invalid WMF byte count"
        );
        let original = [
            i16_at(bytes, 6)? as i32,
            i16_at(bytes, 8)? as i32,
            i16_at(bytes, 10)? as i32,
            i16_at(bytes, 12)? as i32,
        ];
        let bounds = cropped_bounds(original, crop)?;
        let mut editor = Self {
            crop,
            window: [
                original[0],
                original[1],
                original[2] - original[0],
                original[3] - original[1],
            ],
            saved: Vec::new(),
            output: bytes[..40].to_vec(),
            max_words: 0,
        };
        for (index, value) in bounds.into_iter().enumerate() {
            let value =
                i16::try_from(value).context("WMF crop exceeds 16-bit canvas coordinates")?;
            editor.output[6 + index * 2..8 + index * 2].copy_from_slice(&value.to_le_bytes());
        }
        editor.record(0x0103, &[8])?; // MM_ANISOTROPIC makes both crop dimensions independent.
        editor.mapping()?;
        editor.clip()?;
        let mut offset = 40;
        let mut eof = false;
        while offset < bytes.len() {
            let words = u32_at(bytes, offset)?;
            let length = usize::try_from(words)?
                .checked_mul(2)
                .context("WMF record size overflow")?;
            ensure!(
                length >= 6 && length <= bytes.len() - offset && !eof,
                "Invalid WMF record size"
            );
            let record = &bytes[offset..offset + length];
            let kind = u16_at(record, 4)?;
            match kind {
                0x0103 => ensure!(
                    i16_at(record, 6)? == 8,
                    "WMF crop requires anisotropic mapping"
                ),
                0x020b | 0x020c | 0x020f => {
                    let x = i32::from(i16_at(record, 8)?);
                    let y = i32::from(i16_at(record, 6)?);
                    match kind {
                        0x020b => {
                            editor.window[0] = x;
                            editor.window[1] = y;
                        }
                        0x020c => {
                            editor.window[2] = x;
                            editor.window[3] = y;
                        }
                        _ => {
                            editor.window[0] = editor.window[0]
                                .checked_add(x)
                                .context("WMF window origin overflow")?;
                            editor.window[1] = editor.window[1]
                                .checked_add(y)
                                .context("WMF window origin overflow")?;
                        }
                    }
                    editor.mapping()?;
                }
                0x001e => {
                    editor.saved.push(editor.window);
                    editor.copy_record(record, words);
                }
                0x0127 => {
                    let level = i16_at(record, 6)?;
                    let index = if level < 0 {
                        editor.saved.len() as i64 + i64::from(level)
                    } else {
                        i64::from(level) - 1
                    };
                    ensure!(
                        index >= 0 && (index as usize) < editor.saved.len(),
                        "Invalid WMF RestoreDC level"
                    );
                    editor.window = editor.saved[index as usize];
                    editor.saved.truncate(index as usize);
                    editor.copy_record(record, words);
                    editor.clip()?;
                }
                0x012c => {
                    editor.copy_record(record, words);
                    editor.clip()?;
                } // SelectClipRegion can clear the crop.
                0x020d | 0x020e | 0x0211 | 0x0410 | 0x0412 | 0x0220 | 0x0149 => {
                    bail!(
                        "WMF crop does not support viewport scaling, scaled windows, moved clip regions, or RTL layouts; use --image-crop attributes"
                    );
                }
                0x0626 if record.windows(4).any(|value| value == b"WMFC") => {
                    bail!("WMF contains an overriding embedded EMF; use --image-crop attributes");
                }
                _ => {
                    editor.copy_record(record, words);
                }
            }
            eof = kind == 0;
            offset += length;
        }
        ensure!(eof, "WMF has no end record");
        let words = u32::try_from((editor.output.len() - 22) / 2)?;
        editor.output[28..32].copy_from_slice(&words.to_le_bytes());
        editor.output[34..38].copy_from_slice(&editor.max_words.to_le_bytes());
        let checksum = editor.output[..20]
            .as_chunks::<2>()
            .0
            .iter()
            .fold(0u16, |sum, pair| sum ^ u16::from_le_bytes(*pair));
        editor.output[20..22].copy_from_slice(&checksum.to_le_bytes());
        Ok(editor.output)
    }

    /// Convert the current logical window to cropped origin/extents without scaling its content.
    fn mapping(&mut self) -> Result<()> {
        let [x, y, width, height] = self.window;
        ensure!(width != 0 && height != 0, "WMF has an empty logical window");
        let [left, top, right, bottom] = self.crop.map(|side| f64::from(side) / 100_000.0);
        self.record(
            0x020b,
            &[
                (f64::from(y) + f64::from(height) * top).round() as i32,
                (f64::from(x) + f64::from(width) * left).round() as i32,
            ],
        )?;
        let new_width = (f64::from(width) * (1.0 - left - right)).round() as i32;
        let new_height = (f64::from(height) * (1.0 - top - bottom)).round() as i32;
        ensure!(
            new_width != 0 && new_height != 0,
            "Crop is smaller than WMF coordinate precision"
        );
        self.record(0x020c, &[new_height, new_width])
    }

    /// Restrict negative padding to the source canvas and recover after source clip resets.
    fn clip(&mut self) -> Result<()> {
        let [x, y, width, height] = self.window;
        let [left, top, right, bottom] = self.crop.map(|side| f64::from(side) / 100_000.0);
        let x1 = (f64::from(x) + f64::from(width) * left.clamp(0.0, 1.0)).round() as i32;
        let y1 = (f64::from(y) + f64::from(height) * top.clamp(0.0, 1.0)).round() as i32;
        let x2 = (f64::from(x) + f64::from(width) * (1.0 - right.clamp(0.0, 1.0))).round() as i32;
        let y2 = (f64::from(y) + f64::from(height) * (1.0 - bottom.clamp(0.0, 1.0))).round() as i32;
        self.record(0x0416, &[y1.max(y2), x1.max(x2), y1.min(y2), x1.min(x2)])
    }

    /// Encode a short WMF state record with checked 16-bit coordinates.
    fn record(&mut self, kind: u16, arguments: &[i32]) -> Result<()> {
        let words = 3 + arguments.len() as u32;
        self.output.extend(words.to_le_bytes());
        self.output.extend(kind.to_le_bytes());
        for &value in arguments {
            self.output.extend(
                i16::try_from(value)
                    .context("WMF crop exceeds 16-bit logical coordinates")?
                    .to_le_bytes(),
            );
        }
        self.max_words = self.max_words.max(words);
        Ok(())
    }

    /// Copy authored drawing/object records verbatim and maintain the metafile record limit.
    fn copy_record(&mut self, record: &[u8], words: u32) {
        self.output.extend_from_slice(record);
        self.max_words = self.max_words.max(words);
    }
}

/// Read a checked little-endian field from untrusted metafile bytes.
fn field<const SIZE: usize>(bytes: &[u8], offset: usize) -> Result<[u8; SIZE]> {
    bytes
        .get(
            offset
                ..offset
                    .checked_add(SIZE)
                    .context("Metafile offset overflow")?,
        )
        .context("Truncated metafile field")?
        .try_into()
        .context("Invalid metafile field")
}

/// Read an unsigned WMF word or header field.
fn u16_at(bytes: &[u8], offset: usize) -> Result<u16> {
    Ok(u16::from_le_bytes(field(bytes, offset)?))
}

/// Read a signed WMF coordinate or saved-DC index.
fn i16_at(bytes: &[u8], offset: usize) -> Result<i16> {
    Ok(i16::from_le_bytes(field(bytes, offset)?))
}

/// Read an unsigned metafile byte count or record identifier.
fn u32_at(bytes: &[u8], offset: usize) -> Result<u32> {
    Ok(u32::from_le_bytes(field(bytes, offset)?))
}

/// Read a signed EMF coordinate or reference-device dimension.
fn i32_at(bytes: &[u8], offset: usize) -> Result<i32> {
    Ok(i32::from_le_bytes(field(bytes, offset)?))
}
