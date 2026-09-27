
"""
Pandoc filter to move all images, bibliography files, CSL files and other resource files to the target directory.

This filter processes:
- Image paths in markdown images
- Bibliography file paths in metadata
- CSL file paths in metadata
- Any other file references

Usage:
    pandoc input.md --filter pandoc/filters/latex/resource_move.py -o output.pdf
"""

import shutil
import os
from typing import Any, Union
import panflute as pf

TARGET_DIR = "output/latex/"  # Generic output directory for LaTeX resources


def move_resource(path: str):
    """Copy a referenced resource while keeping absolute paths out of output names."""
    if not os.path.exists(path):
        print(f"Warning: File '{path}' does not exist.")
        return None
    relative_path = os.path.basename(path) if os.path.isabs(path) else path
    target_path = os.path.join(TARGET_DIR, relative_path)
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    shutil.copy(path, target_path)
    return None


def process_metadata_value(value: Any) -> Any:
    """
    Process panflute metadata values that might contain file paths.
    
    Args:
        value: The metadata value to process (panflute MetaValue)
        prefix: The prefix to add to paths
    
    Returns:
        Processed metadata value (panflute MetaValue)
    """
    # Handle MetaString
    if isinstance(value, pf.MetaString):
        move_resource(value.text)
    
    # Handle MetaInlines (inline text)
    elif isinstance(value, pf.MetaInlines):
        # Extract text from inlines
        text = pf.stringify(value)
        move_resource(text)
    
    # Handle MetaList (list of values)
    elif isinstance(value, pf.MetaList):
        [process_metadata_value(item) for item in value.content]
    
    # Handle MetaMap (dictionary)
    elif isinstance(value, pf.MetaMap):
        {process_metadata_value(v) for k, v in value.content.items()}
    
    # Return unchanged for other types
    else:
        None
    
def action(elem: pf.Element, doc: pf.Doc) -> Union[pf.Element, None]:
    """
    Panflute action function to process each element.
    
    Args:
        elem: The element to process
        doc: The document
    
    Returns:
        Modified element or None
    """
    # Process images
    if isinstance(elem, pf.Image):
        move_resource(elem.url)
    
    # Process links that might reference local files
    if isinstance(elem, pf.Link):
        # Only modify if it looks like a file path (has extension)
        if '.' in elem.url and not elem.url.startswith('http'):
            move_resource(elem.url)
    
    return None


def prepare(doc: pf.Doc) -> None:
    """
    Prepare function to process document metadata before elements.
    
    Args:
        doc: The document
    """
    # Process bibliography paths
    if 'bibliography' in doc.metadata:
        process_metadata_value(doc.metadata['bibliography'])
    
    # Process CSL path
    if 'csl' in doc.metadata:
        process_metadata_value(doc.metadata['csl'])
    
    # Process other common metadata paths
    path_metadata_keys = [
        'reference-doc',
        'reference-docx', 
        'template',
        'include-before-body',
        'include-after-body',
        'include-in-header',
        'css',
        'data-dir',
        'extract-media',
        'resource-path'
    ]
    
    for key in path_metadata_keys:
        if key in doc.metadata:
            process_metadata_value(doc.metadata[key])


def main(doc: pf.Doc | None = None):
    """
    Main function for the filter.
    
    Args:
        doc: The document (provided by panflute)
    
    Returns:
        Processed document
    """
    return pf.run_filter(
        action,
        prepare=prepare,
        doc=doc
    )


if __name__ == '__main__':
    main()

