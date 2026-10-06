//! Word style values and effective inheritance differences.

use std::collections::HashMap;

/// Word style types keep identically named paragraph and character styles distinct.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub(in crate::html) enum StyleKind {
    Paragraph,
    Character,
    Table,
}

/// Keep ancestry alongside effective values so CSS can share parent declarations.
pub(super) struct ReferenceStyle {
    pub(super) id: String,
    pub(super) parent: Option<String>,
    pub(super) effective: StyleSpec,
    pub(super) configured_by: Option<String>,
}

/// Index named styles and their ids without conflating paragraph and run styles.
pub(super) struct ReferenceStyles {
    pub(super) named: HashMap<(StyleKind, String), ReferenceStyle>,
    pub(super) ids: HashMap<String, (StyleKind, String)>,
    pub(super) defaults: StyleSpec,
}

/// Effective formatting declared by a Word paragraph, character, or table style.
#[derive(Clone, Default)]
pub(super) struct StyleSpec {
    pub(super) font_size: Option<f64>,
    pub(super) western_font: Option<String>,
    pub(super) chinese_font: Option<String>,
    pub(super) bold: Option<bool>,
    pub(super) italic: Option<bool>,
    pub(super) color: Option<[u8; 3]>,
    pub(super) line_height: Option<String>,
    pub(super) before: Option<f64>,
    pub(super) after: Option<f64>,
    pub(super) alignment: Option<String>,
    pub(super) first_line: Option<String>,
    pub(super) left: Option<f64>,
    pub(super) right: Option<f64>,
    pub(super) cell_top: Option<f64>,
    pub(super) cell_right: Option<f64>,
    pub(super) cell_bottom: Option<f64>,
    pub(super) cell_left: Option<f64>,
    pub(super) borders: [Option<String>; 6],
    pub(super) header_borders: [Option<String>; 4],
}

impl StyleSpec {
    /// Reset browser paragraph spacing when Word supplies none or the caption owns it.
    pub(super) fn zero_paragraph_spacing() -> Self {
        Self {
            before: Some(0.0),
            after: Some(0.0),
            left: Some(0.0),
            right: Some(0.0),
            first_line: Some("0pt".into()),
            ..Self::default()
        }
    }

    /// Emit changes from the parent, keeping composed font families complete.
    pub(super) fn difference(&self, parent: &Self) -> Self {
        let mut result = Self::default();
        macro_rules! copy_changed {
            ($($field:ident),*) => {$(if self.$field != parent.$field {
                result.$field = self.$field.clone();
            })*};
        }
        copy_changed!(
            font_size,
            bold,
            italic,
            color,
            line_height,
            before,
            after,
            alignment,
            first_line,
            left,
            right,
            cell_top,
            cell_right,
            cell_bottom,
            cell_left
        );
        // Word can change just the East Asian font; CSS font-family replaces
        // the entire list, so retain the inherited Western font in that case.
        if self.western_font != parent.western_font || self.chinese_font != parent.chinese_font {
            result.western_font = self.western_font.clone();
            result.chinese_font = self.chinese_font.clone();
        }
        for index in 0..self.borders.len() {
            if self.borders[index] != parent.borders[index] {
                result.borders[index] = self.borders[index].clone();
            }
        }
        for index in 0..self.header_borders.len() {
            if self.header_borders[index] != parent.header_borders[index] {
                result.header_borders[index] = self.header_borders[index].clone();
            }
        }
        result
    }

    /// Apply only explicitly declared child properties, including false and zero.
    pub(super) fn overlay(&mut self, child: Self) {
        macro_rules! copy {
            ($($field:ident),*) => {$(if child.$field.is_some() {self.$field = child.$field;})*};
        }
        copy!(
            font_size,
            western_font,
            chinese_font,
            bold,
            italic,
            color,
            line_height,
            before,
            after,
            alignment,
            first_line,
            left,
            right,
            cell_top,
            cell_right,
            cell_bottom,
            cell_left
        );
        for (parent, child) in self.borders.iter_mut().zip(child.borders) {
            if child.is_some() {
                *parent = child;
            }
        }
        for (parent, child) in self.header_borders.iter_mut().zip(child.header_borders) {
            if child.is_some() {
                *parent = child;
            }
        }
    }
}

/// Render six significant digits, matching Python's general number formatting.
pub(in crate::html) fn general(value: f64) -> String {
    if !value.is_finite() {
        return value.to_string().to_lowercase();
    }
    if value == 0.0 {
        return if value.is_sign_negative() { "-0" } else { "0" }.into();
    }
    // Decide notation after rounding, since 999999.9 rounds into exponent six.
    let scientific = format!("{value:.5e}");
    let (mantissa, exponent) = scientific.split_once('e').expect("scientific float");
    let exponent = exponent.parse::<i32>().expect("scientific exponent");
    if !(-4..6).contains(&exponent) {
        let part = mantissa
            .trim_end_matches('0')
            .trim_end_matches('.')
            .to_owned();
        return format!(
            "{part}e{}{:02}",
            if exponent < 0 { "-" } else { "+" },
            exponent.abs()
        );
    }
    let precision = (5 - exponent).max(0) as usize;
    let rendered = format!("{value:.precision$}");
    if rendered.contains('.') {
        rendered.trim_end_matches('0').trim_end_matches('.').into()
    } else {
        rendered
    }
}
