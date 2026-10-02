
"""
Pandoc filter to replace .emf with .pdf in image paths.
"""

import re
from typing import  Union
import panflute as pf

def action(elem: pf.Element, doc: pf.Doc) -> Union[pf.Element, None]:
    if isinstance(elem, pf.Image):
        if elem.url.endswith('.emf'):
            elem.url = re.sub(r'\.emf$', '.pdf', elem.url)
        return elem
    
    return None

def main(doc: pf.Doc | None = None):
    return pf.run_filter(
        action,
        doc=doc
    )

if __name__ == '__main__':
    main()

