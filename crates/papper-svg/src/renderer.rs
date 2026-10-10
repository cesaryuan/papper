//! Render normalized static SVGs on Skia CPU surfaces, including embedded panels.
//!
//! usvg resolves CSS, DPI-relative units, font outlines and adjacent resources.
//! Its canonical SVG is then rendered by Skia alone. The resource provider
//! rasterizes nested SVG images because Skia's raster codecs cannot decode SVG;
//! bounded recursion and shared errors prevent silent missing panel output.

use anyhow::{Context, Result, bail, ensure};
use skia_safe::{
    AlphaType, Color, ColorType, Data, EncodedImageFormat, FontMgr, ImageInfo, Typeface, images,
    resources::{ImageAsset, LocalResourceProvider, ResourceProvider},
    surfaces,
    svg::Dom,
};
use std::{cell::RefCell, io::Cursor, path::Path, rc::Rc, sync::Arc};

/// Decode canonical inline images while retaining errors across native callbacks.
struct Images {
    local: LocalResourceProvider,
    depth: usize,
    errors: Rc<RefCell<Option<String>>>,
}

impl ResourceProvider for Images {
    /// Canonical usvg output embeds local image bytes as data URIs.
    fn load(&self, path: &str, name: &str) -> Option<Data> {
        self.local.load(path, name)
    }

    /// Render SVG child panels before handing their pixels to Skia's image codec.
    fn load_image_asset(&self, path: &str, name: &str, _id: &str) -> Option<ImageAsset> {
        let data = self.load(path, name)?;
        let bytes = data.as_bytes();
        let image = if std::str::from_utf8(bytes).is_ok_and(|svg| svg.trim_start().starts_with('<'))
        {
            match normalize_and_render(
                Path::new("embedded.svg"),
                bytes,
                96.0,
                1.0,
                None,
                self.depth + 1,
                self.errors.clone(),
                None,
            ) {
                Ok(png) => Data::new_copy(&png),
                Err(error) => {
                    *self.errors.borrow_mut() =
                        Some(format!("Cannot render SVG child image: {error:#}"));
                    return None;
                }
            }
        } else if bytes.starts_with(b"RIFF") && bytes.get(8..12) == Some(b"WEBP") {
            match webp_png(bytes) {
                Ok(png) => png,
                Err(error) => {
                    *self.errors.borrow_mut() =
                        Some(format!("Cannot decode WebP child image: {error:#}"));
                    return None;
                }
            }
        } else {
            data
        };
        let asset = ImageAsset::from_data(image, None);
        if asset.is_none() {
            *self.errors.borrow_mut() = Some("Cannot decode embedded SVG image resource".into());
        }
        asset
    }

    /// Text has already been converted to outlines using the fingerprinted fonts.
    fn load_typeface(&self, _name: &str, _url: &str) -> Option<Typeface> {
        None
    }

    /// Avoid a second, differently ordered system-font scan inside Skia.
    fn font_mgr(&self) -> FontMgr {
        FontMgr::empty()
    }
}

/// Keep WebP child images readable when the upstream Skia binary omits its codec.
fn webp_png(bytes: &[u8]) -> Result<Data> {
    let mut decoder = image_webp::WebPDecoder::new(Cursor::new(bytes))?;
    let (width, height) = decoder.dimensions();
    ensure!(
        u64::from(width) * u64::from(height) <= 200_000_000,
        "WebP child image exceeds 200 million pixels"
    );
    let alpha = decoder.has_alpha();
    let mut pixels = vec![
        0;
        decoder
            .output_buffer_size()
            .context("WebP child size is invalid")?
    ];
    decoder.read_image(&mut pixels)?;
    let rgba = if alpha {
        pixels
    } else {
        pixels
            .as_chunks::<3>()
            .0
            .iter()
            .flat_map(|rgb| [rgb[0], rgb[1], rgb[2], 255])
            .collect()
    };
    let info = ImageInfo::new(
        (i32::try_from(width)?, i32::try_from(height)?),
        ColorType::RGBA8888,
        AlphaType::Unpremul,
        None,
    );
    images::raster_from_data(&info, Data::new_copy(&rgba), width as usize * 4)
        .context("Cannot allocate WebP child image")?
        .encode(None, EncodedImageFormat::PNG, None)
        .context("Cannot encode WebP child image")
}

/// Keep the public renderer's original input, DPI, width and scale contracts.
pub(super) fn render(
    source: &Path,
    svg: &str,
    dpi: f64,
    scale: f64,
    width: Option<u32>,
) -> Result<Vec<u8>> {
    let trace = super::RenderTrace::new(source);
    normalize_and_render(
        source,
        svg.as_bytes(),
        dpi,
        scale,
        width,
        0,
        Rc::new(RefCell::new(None)),
        Some(&trace),
    )
}

/// Normalize input once, then render through Skia without a second raster backend.
#[allow(clippy::too_many_arguments)]
fn normalize_and_render(
    source: &Path,
    input: &[u8],
    dpi: f64,
    scale: f64,
    width: Option<u32>,
    depth: usize,
    errors: Rc<RefCell<Option<String>>>,
    trace: Option<&super::RenderTrace>,
) -> Result<Vec<u8>> {
    ensure!(depth <= 16, "Nested SVG images exceed 16 levels");
    let fonts = if std::str::from_utf8(input).map_or(true, super::requires_fonts) {
        super::font_database()
    } else {
        Arc::new(usvg::fontdb::Database::new())
    };
    let options = usvg::Options {
        resources_dir: source.parent().map(Path::to_path_buf),
        dpi: dpi as f32,
        font_size: 16.0,
        font_family: if cfg!(target_os = "linux") {
            "Liberation Serif"
        } else {
            "Times New Roman"
        }
        .into(),
        default_size: usvg::Size::from_wh(width.unwrap_or(100) as f32, 100.0)
            .context("SVG default viewport is invalid")?,
        fontdb: fonts,
        ..Default::default()
    };
    if let Some(trace) = trace {
        trace.stage("Fonts ready; parsing SVG");
    }
    let tree = usvg::Tree::from_data(input, &options)
        .with_context(|| format!("Cannot render SVG: {}", source.display()))?;
    // Preserve the previous native-size rounding before applying width or scale.
    let original = tree.size().to_int_size();
    let size = if let Some(width) = width {
        original.scale_to_width(width)
    } else {
        original.scale_by(scale as f32)
    }
    .context("SVG render size is invalid")?;
    ensure!(
        u64::from(size.width()) * u64::from(size.height()) <= 200_000_000,
        "SVG rasterization exceeds 200 million pixels"
    );
    let canonical = tree.to_string(&usvg::WriteOptions::default());
    let provider = Images {
        local: LocalResourceProvider::new(FontMgr::empty()),
        depth,
        errors: errors.clone(),
    };
    let mut dom =
        Dom::from_bytes(canonical.as_bytes(), provider).context("Cannot load SVG into Skia")?;
    dom.set_container_size((tree.size().width(), tree.size().height()));
    if let Some(trace) = trace {
        trace.stage("SVG parsed; rasterizing with Skia CPU");
    }
    let mut surface =
        surfaces::raster_n32_premul((i32::try_from(size.width())?, i32::try_from(size.height())?))
            .context("Could not allocate SVG image buffer")?;
    let canvas = surface.canvas();
    canvas.clear(Color::TRANSPARENT);
    canvas.scale((
        size.width() as f32 / original.width() as f32,
        size.height() as f32 / original.height() as f32,
    ));
    dom.render(canvas);
    if let Some(error) = errors.borrow_mut().take() {
        bail!("{error}");
    }
    if let Some(trace) = trace {
        trace.stage("Pixels rendered; encoding PNG");
    }
    let png = surface
        .image_snapshot()
        .encode(None, EncodedImageFormat::PNG, None)
        .context("Cannot encode SVG pixels as PNG")?;
    if let Some(trace) = trace {
        trace.stage("PNG ready");
    }
    Ok(png.as_bytes().to_vec())
}
