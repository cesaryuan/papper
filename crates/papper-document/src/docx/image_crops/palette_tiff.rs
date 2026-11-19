//! Expand the 8-bit indexed TIFFs commonly stored inside Word picture grids.

use anyhow::{Context, Result, ensure};
use image::{DynamicImage, ImageBuffer};
use std::io::Cursor;
use tiff::decoder::{Decoder, DecodingResult};
use tiff::tags::{PhotometricInterpretation, Tag};

/// Decode TIFF palette entries without rewriting compressed strips or resampling pixels.
pub(super) fn decode(bytes: &[u8]) -> Result<Option<DynamicImage>> {
    let mut decoder = Decoder::new(Cursor::new(bytes))?;
    if decoder.find_tag_unsigned::<u16>(Tag::PhotometricInterpretation)?
        != Some(PhotometricInterpretation::RGBPalette.to_u16())
        || decoder.get_tag_u16_vec(Tag::BitsPerSample)? != [8]
    {
        return Ok(None);
    }
    let (width, height) = decoder.dimensions()?;
    let palette = decoder.get_tag_u16_vec(Tag::ColorMap)?;
    ensure!(palette.len() == 3 * 256, "Invalid TIFF color map");
    let ifd = decoder
        .ifd_pointer()
        .context("Missing TIFF image directory")?
        .0;
    let gray = as_gray_indices(bytes, usize::try_from(ifd)?)?;
    let DecodingResult::U8(indices) = Decoder::new(Cursor::new(gray))?.read_image()? else {
        anyhow::bail!("Expected one 8-bit TIFF index per pixel");
    };
    if palette.iter().all(|value| value >> 8 == value & 255) {
        let rgb = indices.into_iter().flat_map(|i| {
            let i = usize::from(i);
            [palette[i], palette[256 + i], palette[512 + i]].map(|value| (value >> 8) as u8)
        });
        let image = ImageBuffer::from_raw(width, height, rgb.collect())
            .context("Invalid TIFF pixel buffer")?;
        Ok(Some(DynamicImage::ImageRgb8(image)))
    } else {
        // A TIFF palette can carry genuine 16-bit channel values. Keep them in the PNG.
        let rgb = indices.into_iter().flat_map(|i| {
            let i = usize::from(i);
            [palette[i], palette[256 + i], palette[512 + i]]
        });
        let image = ImageBuffer::from_raw(width, height, rgb.collect())
            .context("Invalid TIFF pixel buffer")?;
        Ok(Some(DynamicImage::ImageRgb16(image)))
    }
}

/// Let the TIFF library decode the same bytes as gray samples instead of rejecting the palette tag.
fn as_gray_indices(bytes: &[u8], ifd: usize) -> Result<Vec<u8>> {
    let (little, count_size, entry_size, value_offset) = match bytes.get(..4) {
        Some(b"II\x2a\x00") => (true, 2, 12, 8),
        Some(b"MM\x00\x2a") => (false, 2, 12, 8),
        Some(b"II\x2b\x00") => (true, 8, 20, 12),
        Some(b"MM\x00\x2b") => (false, 8, 20, 12),
        _ => anyhow::bail!("Unrecognized TIFF header"),
    };
    let short = if little {
        u16::from_le_bytes
    } else {
        u16::from_be_bytes
    };
    let long = if little {
        u64::from_le_bytes
    } else {
        u64::from_be_bytes
    };
    let (count, entries) = bytes
        .get(ifd..)
        .context("Invalid TIFF directory offset")?
        .split_at_checked(count_size)
        .context("Truncated TIFF directory")?;
    let count = if count_size == 2 {
        usize::from(short(count.try_into()?))
    } else {
        usize::try_from(long(count.try_into()?))?
    };
    let mut entries = entries.chunks_exact(entry_size);
    ensure!(count <= entries.len(), "Truncated TIFF directory entries");
    let index = entries
        .by_ref()
        .take(count)
        .position(|entry| short([entry[0], entry[1]]) == Tag::PhotometricInterpretation.to_u16())
        .context("Missing TIFF photometric interpretation")?;
    let at = ifd + count_size + index * entry_size + value_offset;
    let mut gray = bytes.to_vec();
    let value = PhotometricInterpretation::BlackIsZero.to_u16();
    gray[at..at + 2].copy_from_slice(&if little {
        value.to_le_bytes()
    } else {
        value.to_be_bytes()
    });
    Ok(gray)
}
