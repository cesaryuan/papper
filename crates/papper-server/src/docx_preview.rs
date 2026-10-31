//! Serve editor DOCX previews as PDF without installing or spawning Docxide CLI.
//!
//! The CLI supplies its retained DOCX builder as a callback. This service owns a
//! loopback listener and renders the resulting bytes with the embedded Docxide
//! library. Requests are serialized to retain native worker caches; closing the
//! owner's stdin ends the service. No request can select a PDF output filename.

use anyhow::{Context, Result};
use serde_json::{Value, json};
use std::io::{Read, Write};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};
use tiny_http::{Header, Method, Response, Server, StatusCode};

/// Bound unsaved manuscript snapshots independently of the generated PDF size.
const MAX_REQUEST_BYTES: u64 = 32 * 1024 * 1024;

/// Run one preview-owned service with an authenticated, persistent DOCX builder.
pub fn run(token: &str, mut build: impl FnMut(Value) -> Result<(Vec<u8>, Value)>) -> Result<()> {
    anyhow::ensure!(
        token.len() >= 32 && token.bytes().all(|c| c.is_ascii_hexdigit()),
        "Preview service token must contain at least 32 hexadecimal characters"
    );
    let server = Server::http("127.0.0.1:0").map_err(|error| anyhow::anyhow!("{error}"))?;
    let address = server
        .server_addr()
        .to_ip()
        .context("Missing preview listener address")?;
    let stopped = Arc::new(AtomicBool::new(false));
    let owner_closed = stopped.clone();
    // The extension owns stdin. EOF also cleans up the server if its host dies
    // without sending an explicit HTTP shutdown request.
    std::thread::spawn(move || {
        let mut buffer = [0; 256];
        while std::io::stdin()
            .read(&mut buffer)
            .is_ok_and(|count| count != 0)
        {}
        owner_closed.store(true, Ordering::Release);
    });
    {
        let mut output = std::io::stdout().lock();
        serde_json::to_writer(
            &mut output,
            &json!({
                "type":"ready", "protocol":"papper-docx-preview-v1",
                "url":format!("http://{address}"), "pid":std::process::id(),
                "renderer":"docxide-pdf/0.20.1"
            }),
        )?;
        writeln!(output)?;
        output.flush()?;
    }
    eprintln!("[DOCX preview] Embedded PDF service ready at {address}");
    while !stopped.load(Ordering::Acquire) {
        let Some(mut request) = server.recv_timeout(Duration::from_millis(250))? else {
            continue;
        };
        let authorized = request.headers().iter().any(|header| {
            header.field.equiv("Authorization")
                && header.value.as_str() == format!("Bearer {token}")
        });
        if !authorized {
            let _ = request.respond(Response::empty(StatusCode(401)));
            continue;
        }
        if request.method() != &Method::Post || request.url() != "/preview" {
            let _ = request.respond(Response::empty(StatusCode(404)));
            continue;
        }
        let result = (|| -> Result<(Vec<u8>, Value, f64)> {
            anyhow::ensure!(
                request
                    .body_length()
                    .is_none_or(|size| size as u64 <= MAX_REQUEST_BYTES),
                "Preview snapshot exceeds 32 MiB"
            );
            let mut bytes = Vec::new();
            request
                .as_reader()
                .take(MAX_REQUEST_BYTES + 1)
                .read_to_end(&mut bytes)?;
            anyhow::ensure!(
                bytes.len() as u64 <= MAX_REQUEST_BYTES,
                "Preview snapshot exceeds 32 MiB"
            );
            let (docx, metrics) = build(serde_json::from_slice(&bytes)?)?;
            let started = Instant::now();
            // Docxide receives bytes directly; its CLI's output-renaming behavior
            // and external executable installation are absent from this path.
            let pdf =
                std::panic::catch_unwind(|| docxide_pdf::convert_docx_bytes_to_pdf_bytes(&docx))
                    .map_err(|_| anyhow::anyhow!("Embedded PDF renderer panicked"))??;
            anyhow::ensure!(
                pdf.starts_with(b"%PDF-"),
                "Renderer returned an invalid PDF"
            );
            Ok((pdf, metrics, started.elapsed().as_secs_f64() * 1000.0))
        })();
        match result {
            Ok((pdf, metrics, conversion_ms)) => {
                let build_ms = metrics["timings_ms"]["total"].as_f64().unwrap_or_default();
                eprintln!(
                    "[DOCX preview] DOCX={build_ms:.0} ms; PDF={conversion_ms:.0} ms; bytes={}",
                    pdf.len()
                );
                let response = Response::from_data(pdf)
                    .with_header(header("Content-Type", "application/pdf"))
                    .with_header(header("Cache-Control", "no-store"))
                    .with_header(header("X-Papper-Build-Ms", &build_ms.to_string()))
                    .with_header(header("X-Papper-Pdf-Ms", &conversion_ms.to_string()))
                    .with_header(header(
                        "X-Papper-Worker-Pid",
                        &metrics["worker_pid"].to_string(),
                    ));
                let _ = request.respond(response);
            }
            Err(error) => {
                eprintln!("[DOCX preview] Build failed: {error:#}");
                let _ = request.respond(
                    Response::from_data(serde_json::to_vec(
                        &json!({"error":format!("{error:#}")}),
                    )?)
                    .with_status_code(StatusCode(422))
                    .with_header(header("Content-Type", "application/json")),
                );
            }
        }
    }
    eprintln!("[DOCX preview] Service stopped");
    Ok(())
}

/// Construct only internal response headers with known ASCII field names.
fn header(name: &str, value: &str) -> Header {
    Header::from_bytes(name, value).expect("Valid internal preview header")
}
