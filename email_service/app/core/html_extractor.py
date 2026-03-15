"""Convert HTML email bodies to plain text suitable for regex matching."""
import re
from bs4 import BeautifulSoup


# Block-level elements that should produce line breaks
BLOCK_TAGS = {'p', 'div', 'tr', 'li', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote'}


def html_to_text(html: str) -> str:
    """
    Convert HTML to plain text, preserving meaningful whitespace at block boundaries.

    Strategy:
    1. Insert newlines before each block-level element.
    2. Strip all tags.
    3. Collapse horizontal whitespace within lines.
    4. Collapse runs of blank lines to single blank lines.
    """
    soup = BeautifulSoup(html, 'html.parser')

    # Insert newlines before block-level elements
    for tag in soup.find_all(BLOCK_TAGS):
        tag.insert_before('\n')
        tag.insert_after('\n')

    text = soup.get_text(separator=' ')

    # Collapse whitespace within lines (but keep newlines)
    lines = text.split('\n')
    lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in lines]

    # Remove runs of blank lines
    result_lines = []
    prev_blank = False
    for line in lines:
        is_blank = (line == '')
        if is_blank and prev_blank:
            continue
        result_lines.append(line)
        prev_blank = is_blank

    return '\n'.join(result_lines).strip()
