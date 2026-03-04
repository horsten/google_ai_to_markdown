#!/usr/bin/env python3
"""
mhtml2md.py — Convert Google AI Mode .mhtml files to clean Markdown.

Google AI Mode conversations saved via Chrome's "Save as single file"
(.mhtml) contain quoted-printable encoded HTML wrapped in Google's
heavy JavaScript framework markup. This script extracts the readable
conversation content and produces clean Markdown.

Usage:
    python3 mhtml2md.py input.mhtml                  # outputs input.md
    python3 mhtml2md.py input.mhtml -o output.md     # explicit output path
    python3 mhtml2md.py *.mhtml                      # batch convert
    python3 mhtml2md.py input.mhtml --include-sources # include citation URLs

Author: Thomas (Copenhagen) & Claude
License: MIT
"""

import argparse
import email
import quopri
import base64
import re
import sys
import os
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup, NavigableString, Tag


# ═══════════════════════════════════════════════════════════════════
# MHTML Parsing
# ═══════════════════════════════════════════════════════════════════

def extract_html_from_mhtml(mhtml_path: str) -> tuple[str, dict]:
    """Parse an .mhtml file and extract the main HTML content + metadata."""
    with open(mhtml_path, 'rb') as f:
        raw = f.read()

    msg = email.message_from_bytes(raw)

    metadata = {
        'subject': msg.get('Subject', ''),
        'date': msg.get('Date', ''),
        'url': msg.get('Snapshot-Content-Location', ''),
    }

    # Extract query from subject
    subject = metadata['subject']
    if subject.endswith(' - Google Search'):
        metadata['query'] = subject.replace(' - Google Search', '')
    else:
        metadata['query'] = subject

    html_parts = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() != 'text/html':
                continue
            encoding = part.get('Content-Transfer-Encoding', '').lower()
            payload = part.get_payload(decode=False)
            if isinstance(payload, str):
                payload = payload.encode('utf-8', errors='replace')
            elif not isinstance(payload, bytes):
                continue

            if encoding == 'quoted-printable':
                payload = quopri.decodestring(payload)
            elif encoding == 'base64':
                payload = base64.b64decode(payload)

            html_parts.append(payload.decode('utf-8', errors='replace'))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            html_parts.append(payload.decode('utf-8', errors='replace'))

    if not html_parts:
        raise ValueError(f"No HTML content found in {mhtml_path}")

    return max(html_parts, key=len), metadata


# ═══════════════════════════════════════════════════════════════════
# Text Cleaning Utilities
# ═══════════════════════════════════════════════════════════════════

def clean_text(text: str) -> str:
    """Remove Google framework artifacts from text."""
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)

    # ── General Google framework marker removal ──
    # Google's JS framework injects identifier tokens (e.g. TgQPHd, Sv6Kpe,
    # qkimaf, cqw1tb, BipLCb, BVUQsc) as text nodes. These follow patterns:
    #   - "Token[...]" — data markers with bracketed content
    #   - "Token|..." — pipe-delimited data markers
    #   - "Token XXX/YYY" — path-style markers
    # The token is typically 5-10 alphanumeric chars starting with a letter,
    # containing mixed case or digits (not a normal English word).

    # Helper pattern for framework token identifiers:
    # 5-10 chars, starts with letter, contains at least one uppercase and one
    # lowercase, often has digits. Examples: Sv6Kpe, TgQPHd, BipLCb, BVUQsc
    _TOK = r'[A-Za-z][A-Za-z0-9]{4,9}'

    # Pattern 1: Token[...] — greedy match from [ to the last ] on the line
    text = re.sub(_TOK + r'\[.*?\](?:\])*', '', text)
    # Pattern 1b: Token[... with unclosed bracket (truncated data)
    text = re.sub(_TOK + r'\[\[?[^\]]*$', '', text, flags=re.MULTILINE)
    # Pattern 1c: Simple Token[] that might remain
    text = re.sub(_TOK + r'\[\]', '', text)

    # Pattern 2: Token|data (TgQPHd|[...])
    text = re.sub(_TOK + r'\|[^\s]*', '', text)

    # Pattern 3: Token path/data — "BVUQsc crI50d_g/fmcmS" style
    text = re.sub(_TOK + r'\s+[A-Za-z0-9_]+/[A-Za-z0-9_]+', '', text)

    # Pattern 4: Paired path markers: "qkimaf X/Ycqw1tb X/Y"
    text = re.sub(r'[a-z]{4,10}\s+\S+?/\S+(?:[a-z]{4,10}\s+\S+/\S+)?', '', text)

    # Pattern 5: base64 image data blobs that leaked into text
    text = re.sub(r'data:image/[^"\s]+', '', text)

    # ── HTML entity and citation cleanup ──
    text = text.replace('&quot;', '"')
    text = text.replace('&amp;', '&')
    text = text.replace('&lt;', '<')
    text = text.replace('&gt;', '>')
    # Remove citation chip remnants: "SourceName +N ..."
    text = re.sub(r'(?:GitHub Docs|Medium|Markdown Guide|Reddit|Angular\.love|'
                  r'CommonMark|Meta Stack Exchange|Wikimedia Foundation|'
                  r'Wikipedia|Forbes|ResearchGate|SpringerLink)\s*\+\d+(?:\s+\S*"[^]]*\]\])?', '', text)
    # Catch remaining "Word",N]]" patterns from partially stripped citation chips
    text = re.sub(r'\w*",?\d*\]*\]\]', '', text)
    # Unicode escapes
    text = text.replace('\\u003d', '=').replace('\\u0026', '&')
    text = text.replace('\\u003c', '<').replace('\\u003e', '>')
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def is_ui_noise(text: str) -> bool:
    """Check if text is Google UI chrome rather than conversation content."""
    noise_patterns = [
        r'^Creating a public link',
        r'^Good response',
        r'^Bad response',
        r'^Thank you',
        r'^Your feedback helps Google',
        r'^Share more feedback',
        r'^Report a problem',
        r'^Close$',
        r'^Searching$',
        r'^Use code with caution',
        r'^See our\s*Privacy Policy',
        r'^Ask anything$',
        r'^Show all$',
        r'^Learn more$',
        r'^Here are top web results',
        r'^\d+\s+sites?$',
    ]
    for pattern in noise_patterns:
        if re.match(pattern, text, re.I):
            return True
    return False


# ═══════════════════════════════════════════════════════════════════
# Content Block Extraction
# ═══════════════════════════════════════════════════════════════════

def extract_inline_text(element: Tag, include_sources: bool = False) -> str:
    """
    Extract text from an element, preserving bold, inline code, and links.
    Returns a Markdown-formatted string.
    """
    parts = []

    for child in element.children:
        if isinstance(child, NavigableString):
            text = clean_text(str(child))
            if text:
                parts.append(text)

        elif isinstance(child, Tag):
            child_classes = ' '.join(child.get('class', []))

            # Skip source citation chips (Fsg96 class)
            if 'Fsg96' in child_classes and not include_sources:
                continue

            # Skip feedback / UI elements
            if any(c in child_classes for c in ['qacuz', 'YsAfEc', 'hpw4G']):
                continue

            # Bold text
            if child.name in ('strong', 'b') or 'Yjhzub' in child_classes:
                bold_text = clean_text(child.get_text())
                if bold_text:
                    parts.append(f'**{bold_text}**')

            # Links
            elif child.name == 'a':
                link_text = clean_text(child.get_text())
                href = child.get('href', '')
                if link_text and href:
                    parts.append(f'[{link_text}]({href})')
                elif link_text:
                    parts.append(link_text)

            # Inline code
            elif child.name == 'code':
                code_text = child.get_text()
                if code_text.strip():
                    parts.append(f'`{code_text.strip()}`')

            # Nested span/div with content — recurse
            elif child.name in ('span', 'div'):
                link = child.find('a')
                if link:
                    link_text = clean_text(link.get_text())
                    href = link.get('href', '')
                    if link_text and href:
                        parts.append(f'[{link_text}]({href})')
                    elif link_text:
                        parts.append(link_text)
                else:
                    nested = extract_inline_text(child, include_sources)
                    if nested.strip():
                        parts.append(nested)
            else:
                text = clean_text(child.get_text())
                if text and not is_ui_noise(text):
                    parts.append(text)

    # Join with space to prevent word fusion, then normalize
    result = ' '.join(p.strip() for p in parts if p.strip())
    # Remove spaces before punctuation (artifact of space-joining)
    result = re.sub(r'\s+([,.\?!:;])', r'\1', result)
    # Apply full cleanup to assembled text  
    result = clean_text(result)
    return result


def extract_code_block(element: Tag) -> Optional[str]:
    """Extract a fenced code block from a code container element."""
    pre = element.find('pre')
    if pre:
        code = pre.find('code')
        code_text = (code or pre).get_text()
    else:
        code = element.find('code')
        if not code:
            return None
        code_text = code.get_text()

    if not code_text or len(code_text.strip()) < 3:
        return None

    # Try to detect language
    lang = ''
    code_el = element.find('code') or element.find('pre')
    if code_el:
        for cls in code_el.get('class', []):
            if cls.startswith('language-'):
                lang = cls.replace('language-', '')
                break

    # If no language class, check if a sibling/prior block mentioned the language
    # (Google often puts "python" as inline code before the actual code block)

    return f'```{lang}\n{code_text.rstrip().replace(chr(13), "")}\n```'


def extract_table(element: Tag) -> Optional[str]:
    """Extract a Markdown table from an HTML table element."""
    table = element.find('table') if element.name != 'table' else element
    if not table:
        return None

    rows = table.find_all('tr')
    if not rows:
        return None

    md_rows = []
    max_cols = 0

    for row in rows:
        cells = row.find_all(['th', 'td'])
        cell_texts = []
        for cell in cells:
            # Preserve inline code in cells
            codes = cell.find_all('code')
            if codes:
                parts = []
                for child in cell.children:
                    if isinstance(child, NavigableString):
                        t = clean_text(str(child))
                        if t:
                            parts.append(t)
                    elif isinstance(child, Tag) and child.name == 'code':
                        ct = child.get_text().strip()
                        if ct:
                            parts.append(f'`{ct}`')
                    elif isinstance(child, Tag):
                        code_in = child.find('code')
                        if code_in:
                            ct = code_in.get_text().strip()
                            if ct:
                                parts.append(f'`{ct}`')
                        else:
                            t = clean_text(child.get_text())
                            if t:
                                parts.append(t)
                text = ' '.join(p for p in parts if p)
            else:
                text = clean_text(cell.get_text())
            cell_texts.append(text)

        max_cols = max(max_cols, len(cell_texts))
        md_rows.append(cell_texts)

    if not md_rows or max_cols == 0:
        return None

    # Pad rows
    for row in md_rows:
        while len(row) < max_cols:
            row.append('')

    lines = []
    lines.append('| ' + ' | '.join(md_rows[0]) + ' |')
    lines.append('| ' + ' | '.join(['---'] * max_cols) + ' |')
    for row in md_rows[1:]:
        lines.append('| ' + ' | '.join(row) + ' |')

    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════════
# List & Blockquote Extraction
# ═══════════════════════════════════════════════════════════════════

def extract_list(element: Tag, include_sources: bool = False, depth: int = 0) -> str:
    """
    Recursively extract a Markdown list (ordered or unordered) from
    a <ul> or <ol> element, preserving nesting.
    """
    is_ordered = element.name == 'ol'
    lines = []
    indent = '    ' * depth  # 4-space indent per level

    for idx, li in enumerate(element.find_all('li', recursive=False), 1):
        # Extract the direct text of this list item (not from sublists)
        text_parts = []
        for child in li.children:
            if isinstance(child, NavigableString):
                t = clean_text(str(child))
                if t:
                    text_parts.append(t)
            elif isinstance(child, Tag):
                # Skip sublists — we'll recurse into them separately
                if child.name in ('ul', 'ol'):
                    continue
                # Skip source chips
                child_cls = ' '.join(child.get('class', []))
                if 'Fsg96' in child_cls or 'uJ19be' in child_cls:
                    continue
                # Bold
                if child.name in ('strong', 'b') or 'Yjhzub' in child_cls:
                    bold_text = clean_text(child.get_text())
                    if bold_text:
                        text_parts.append(f'**{bold_text}**')
                # Italic
                elif child.name in ('em', 'i'):
                    it_text = clean_text(child.get_text())
                    if it_text:
                        text_parts.append(f'*{it_text}*')
                # Inline code
                elif child.name == 'code':
                    code_text = child.get_text().strip()
                    if code_text:
                        text_parts.append(f'`{code_text}`')
                # Links
                elif child.name == 'a':
                    link_text = clean_text(child.get_text())
                    href = child.get('href', '')
                    if link_text and href:
                        text_parts.append(f'[{link_text}]({href})')
                    elif link_text:
                        text_parts.append(link_text)
                else:
                    t = clean_text(child.get_text())
                    if t and not is_ui_noise(t):
                        text_parts.append(t)

        item_text = ' '.join(text_parts)
        item_text = re.sub(r'\s+', ' ', item_text).strip()
        # Remove spaces before punctuation
        item_text = re.sub(r'\s+([,.\?!:;])', r'\1', item_text)

        marker = f'{idx}.' if is_ordered else '-'
        if item_text:
            lines.append(f'{indent}{marker} {item_text}')

        # Recurse into nested sublists
        for sub in li.find_all(['ul', 'ol'], recursive=False):
            sub_md = extract_list(sub, include_sources, depth + 1)
            if sub_md:
                lines.append(sub_md)

    return '\n'.join(lines)


def extract_blockquote(element: Tag, include_sources: bool = False, depth: int = 0) -> str:
    """
    Recursively extract a Markdown blockquote from a <blockquote> element,
    preserving nested quotes, lists, and text.
    """
    prefix = '> ' * (depth + 1)
    lines = []

    for child in element.children:
        if not hasattr(child, 'name') or not child.name:
            continue

        child_cls = ' '.join(child.get('class', []))

        # Skip source chips and UI noise
        if 'Fsg96' in child_cls or 'uJ19be' in child_cls:
            continue

        # Nested blockquote
        if child.name == 'blockquote':
            nested = extract_blockquote(child, include_sources, depth + 1)
            if nested:
                lines.append(nested)
            continue

        # List inside blockquote
        if child.name in ('ul', 'ol'):
            list_md = extract_list(child, include_sources, depth=0)
            if list_md:
                # Prefix each line with the blockquote marker
                for line in list_md.split('\n'):
                    lines.append(f'{prefix}{line}')
            continue

        # Text content (div, p, span)
        if child.name in ('div', 'p', 'span'):
            text = extract_inline_text(child, include_sources)
            if text and not is_ui_noise(text):
                lines.append(f'{prefix}{text}')
            continue

        # Horizontal rule inside blockquote
        if child.name == 'hr':
            lines.append(f'{prefix}---')
            continue

    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════════
# Conversation Turn Extraction
# ═══════════════════════════════════════════════════════════════════

def extract_conversation(html: str, include_sources: bool = False) -> list[dict]:
    """
    Extract conversation turns from Google AI Mode HTML.

    Structure (as of early 2026):
    - div.tonYlb = conversation turn container (one per user→AI exchange)
      - span.VndcI = user message text
      - div.CKgc1d = AI response wrapper
        - div.mZJni.Dn7Fzd = AI response content area
          - div.Y3BBE = text paragraph (bold, links, inline code)
          - div.otQkpb = section heading
          - div.r1PmQe = code block container (pre > code)
          - div.Fv6NCb = table container
          - div.Fsg96 = source citation chip
    """
    soup = BeautifulSoup(html, 'html.parser')
    conversation = []

    # Find conversation turn containers
    turn_containers = soup.find_all('div', class_=re.compile(r'\btonYlb\b'))

    if not turn_containers:
        # Fallback: try CKgc1d directly
        ck_divs = soup.find_all('div', class_=re.compile(r'\bCKgc1d\b'))
        if ck_divs:
            for ck in ck_divs:
                ai_md = _extract_ai_response(ck, include_sources)
                if ai_md:
                    conversation.append({'role': 'assistant', 'content': ai_md})
            return conversation if conversation else _fallback_extraction(soup)
        return _fallback_extraction(soup)

    for turn in turn_containers:
        # ── User message ──
        user_span = turn.find('span', class_=re.compile(r'\bVndcI\b'))
        if user_span:
            user_text = clean_text(user_span.get_text())
            if user_text:
                conversation.append({'role': 'user', 'content': user_text})

        # ── AI response ──
        ai_container = turn.find('div', class_=re.compile(r'\bCKgc1d\b'))
        if ai_container:
            ai_md = _extract_ai_response(ai_container, include_sources)
            if ai_md:
                conversation.append({'role': 'assistant', 'content': ai_md})

    if not conversation:
        return _fallback_extraction(soup)

    return conversation


def _extract_ai_response(container: Tag, include_sources: bool = False) -> str:
    """Extract Markdown content from an AI response container (CKgc1d)."""
    content_area = container.find('div', class_=re.compile(r'\bmZJni\b'))
    if not content_area:
        text = clean_text(container.get_text())
        return text if text and not is_ui_noise(text) else ''

    md_blocks = []

    # Find the div that holds the content blocks
    # Structure: mZJni > (wrapper div) > [content blocks as direct children]
    content_parent = None
    for child in content_area.find_all('div', recursive=False):
        inner_divs = child.find_all('div', recursive=False)
        if inner_divs:
            content_parent = child
            break

    if not content_parent:
        content_parent = content_area

    # Iterate ALL element children, not just divs — lists, blockquotes,
    # and horizontal rules appear as direct children alongside divs.
    for block in content_parent.children:
        if not hasattr(block, 'name') or not block.name:
            continue

        block_classes = ' '.join(block.get('class', []))
        tag = block.name

        # ── Skip UI chrome ──
        if any(c in block_classes for c in ['alk4p', 'SGF5Lb', 'hpw4G', 'ofHStc']):
            continue
        if tag == 'div' and block.find('div', class_=re.compile(r'qacuz|YsAfEc')):
            continue

        block_text = clean_text(block.get_text())
        if not block_text or is_ui_noise(block_text):
            continue

        # ── Source citation (Fsg96) ──
        if 'Fsg96' in block_classes:
            if include_sources:
                links = block.find_all('a')
                for link in links:
                    href = link.get('href', '')
                    text = clean_text(link.get_text())
                    if href and text:
                        md_blocks.append(f'> Source: [{text}]({href})')
            continue

        # ── Section heading (otQkpb) ──
        if 'otQkpb' in block_classes:
            heading = clean_text(block.get_text())
            if heading:
                md_blocks.append(f'### {heading}')
            continue

        # ── Blockquote ──
        if tag == 'blockquote':
            bq_md = extract_blockquote(block, include_sources)
            if bq_md:
                md_blocks.append(bq_md)
            continue

        # ── Unordered list ──
        if tag == 'ul':
            list_md = extract_list(block, include_sources)
            if list_md:
                md_blocks.append(list_md)
            continue

        # ── Ordered list ──
        if tag == 'ol':
            list_md = extract_list(block, include_sources)
            if list_md:
                md_blocks.append(list_md)
            continue

        # ── Horizontal rule ──
        if tag == 'hr':
            md_blocks.append('---')
            continue

        # ── Code block (r1PmQe or has pre>code) ──
        if 'r1PmQe' in block_classes or (block.find('pre') and block.find('code')):
            code_md = extract_code_block(block)
            if code_md:
                md_blocks.append(code_md)
            continue

        # ── Table (Fv6NCb or has <table>) ──
        if 'Fv6NCb' in block_classes or block.find('table'):
            table_md = extract_table(block)
            if table_md:
                md_blocks.append(table_md)
            continue

        # ── Text paragraph (Y3BBE or other div) ──
        if tag == 'div':
            para = extract_inline_text(block, include_sources)
            if para and not is_ui_noise(para):
                md_blocks.append(para)
            continue

    return '\n\n'.join(md_blocks)


def _fallback_extraction(soup: BeautifulSoup) -> list[dict]:
    """Last-resort: extract all visible text."""
    for tag in soup.find_all(['script', 'style', 'noscript', 'svg', 'link', 'meta']):
        tag.decompose()

    text = soup.get_text(separator='\n')
    lines = []
    for line in text.split('\n'):
        line = line.strip()
        line = re.sub(r'<!--.*?-->', '', line, flags=re.DOTALL)
        line = re.sub(r'TgQPHd\|[^\s]*', '', line)
        if line and len(line) > 2 and not is_ui_noise(line):
            lines.append(line)

    deduped = []
    for line in lines:
        if not deduped or line != deduped[-1]:
            deduped.append(line)

    return [{'role': 'assistant', 'content': '\n'.join(deduped)}]


# ═══════════════════════════════════════════════════════════════════
# Markdown Output
# ═══════════════════════════════════════════════════════════════════

def conversation_to_markdown(turns: list[dict], metadata: dict) -> str:
    """Convert conversation turns to a clean Markdown document."""
    lines = []

    query = metadata.get('query', 'Untitled Conversation')
    date = metadata.get('date', '')

    lines.append(f'# {query}')
    lines.append('')
    if date:
        lines.append(f'*Saved: {date}*')
        lines.append('')
    lines.append('*Source: Google AI Mode*')
    lines.append('')
    lines.append('---')
    lines.append('')

    turn_number = 0
    for turn in turns:
        role = turn['role']
        content = turn['content']

        if role == 'user':
            turn_number += 1
            lines.append(f'## Prompt {turn_number}')
            lines.append('')
            lines.append(content)
            lines.append('')

        elif role == 'assistant':
            lines.append(f'## Response {turn_number}')
            lines.append('')
            lines.append(content)
            lines.append('')
            lines.append('---')
            lines.append('')

    result = '\n'.join(lines)
    result = re.sub(r'\n{4,}', '\n\n\n', result)
    result = re.sub(r'<!--.*?-->', '', result, flags=re.DOTALL)
    return result


# ═══════════════════════════════════════════════════════════════════
# Main Pipeline
# ═══════════════════════════════════════════════════════════════════

def convert_mhtml_to_markdown(mhtml_path: str, output_path: Optional[str] = None,
                               include_sources: bool = False,
                               verbose: bool = False) -> str:
    mhtml_path = os.path.abspath(mhtml_path)
    if not output_path:
        output_path = str(Path(mhtml_path).with_suffix('.md'))

    if verbose:
        print(f"Reading: {mhtml_path}")

    html, metadata = extract_html_from_mhtml(mhtml_path)

    if verbose:
        print(f"  Subject: {metadata.get('subject', 'N/A')}")
        print(f"  Date: {metadata.get('date', 'N/A')}")
        print(f"  HTML size: {len(html):,} chars")

    turns = extract_conversation(html, include_sources)

    if verbose:
        user_turns = sum(1 for t in turns if t['role'] == 'user')
        ai_turns = sum(1 for t in turns if t['role'] == 'assistant')
        print(f"  Turns: {user_turns} user, {ai_turns} assistant")

    markdown = conversation_to_markdown(turns, metadata)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(markdown)

    if verbose:
        print(f"  Output: {output_path} ({len(markdown):,} chars)")

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description='Convert Google AI Mode .mhtml files to clean Markdown.',
        epilog='Examples:\n'
               '  %(prog)s conversation.mhtml\n'
               '  %(prog)s *.mhtml --include-sources\n'
               '  %(prog)s input.mhtml -o output.md -v\n',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument('files', nargs='+', help='Input .mhtml file(s)')
    parser.add_argument('-o', '--output', help='Output file path (single file mode only)')
    parser.add_argument('--include-sources', action='store_true',
                        help='Include source/citation URLs in output')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Print progress information')

    args = parser.parse_args()

    if args.output and len(args.files) > 1:
        print("Error: --output can only be used with a single input file", file=sys.stderr)
        sys.exit(1)

    success = 0
    errors = 0

    for mhtml_file in args.files:
        try:
            out = convert_mhtml_to_markdown(
                mhtml_file,
                output_path=args.output,
                include_sources=args.include_sources,
                verbose=args.verbose,
            )
            print(f"✓ {os.path.basename(mhtml_file)} → {os.path.basename(out)}")
            success += 1
        except Exception as e:
            print(f"✗ {os.path.basename(mhtml_file)}: {e}", file=sys.stderr)
            if args.verbose:
                import traceback
                traceback.print_exc()
            errors += 1

    if len(args.files) > 1:
        print(f"\nDone: {success} converted, {errors} failed")

    sys.exit(1 if errors > 0 and success == 0 else 0)


if __name__ == '__main__':
    main()
