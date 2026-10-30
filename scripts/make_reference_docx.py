"""Create templates/reference.docx (Word styles for the DOCX downloads) from pandoc's default.

Usage: uv run scripts/make_reference_docx.py

Only fonts and colours are changed: Arial (available on every computer, unlike Fira Sans) and the
violet of the LYMEC logo for headings and the title. Run again after changing the values below.
"""

from __future__ import annotations

import re
import subprocess
import zipfile
from io import BytesIO

import pbook

FONT = "Arial"
HEADING_COLOUR = "5A5FB5"  # lymecviolet in templates/lymec.latex
LINK_COLOUR = "1F8BC0"  # a darker lymecblue, readable on white


def patch(name: str, xml: str) -> str:
    if name == "word/theme/theme1.xml":
        return re.sub(r'<a:latin typeface="[^"]*"', f'<a:latin typeface="{FONT}"', xml)
    if name == "word/styles.xml":
        xml = re.sub(r'<w:color w:val="[0-9A-F]{6}" w:themeColor="accent1"[^/]*/>', f'<w:color w:val="{HEADING_COLOUR}" />', xml)
        return re.sub(
            r'(<w:style [^>]*w:styleId="Hyperlink".*?<w:color w:val=")[0-9A-F]{6}("[^/]*/>)', rf"\g<1>{LINK_COLOUR}\2", xml, flags=re.S
        )
    return xml


def main() -> None:
    default = subprocess.run(["pandoc", "--print-default-data-file", "reference.docx"], capture_output=True, check=True).stdout
    out = BytesIO()
    with zipfile.ZipFile(BytesIO(default)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename.endswith(".xml"):
                data = patch(info.filename, data.decode("utf-8")).encode("utf-8")
            dst.writestr(info, data)  # keeps names, order and timestamps: same input, same file
    target = pbook.ROOT / "templates" / "reference.docx"
    target.write_bytes(out.getvalue())
    print(f"wrote {target.relative_to(pbook.ROOT)}")


if __name__ == "__main__":
    main()
