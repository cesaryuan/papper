"""DOCX post-processing entry points and pipeline steps used by pmt."""

from .orchestrator import main, postprocess_docx

__all__ = ["main", "postprocess_docx"]
