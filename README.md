# mhtml2md — Google AI Mode Conversation Archiver

Convert Google AI Mode conversations saved as `.mhtml` files into clean, readable Markdown.

Google AI Mode doesn't offer a conversation export feature. This tool parses the Chrome "Save as single file" (`.mhtml`) output — decoding the MIME container, stripping Google's JavaScript framework markup, and extracting the conversation structure into well-formatted Markdown.

## Features

- Extracts all conversation turns (user prompts and AI responses)
- Preserves formatting: **bold**, *italic*, `inline code`, [links](https://example.com)
- Fenced code blocks with language hints
- Tables (converted to GFM Markdown tables)
- Nested ordered and unordered lists with correct indentation
- Blockquotes, including nested blockquotes and mixed content
- Section headings
- Strips Google UI noise (feedback buttons, citation chips, framework data markers)
- Batch conversion (`*.mhtml`)
- Optional `--include-sources` flag to preserve citation URLs

## Installation

Requires Python 3.10+ and two dependencies:

```bash
pip install beautifulsoup4 markdownify
```

No other setup needed — it's a single standalone script.

## Usage

```bash
# Convert a single file (outputs conversation.md alongside the original)
python3 mhtml2md.py conversation.mhtml

# Explicit output path
python3 mhtml2md.py conversation.mhtml -o archive/my-chat.md

# Batch convert all saved conversations
python3 mhtml2md.py *.mhtml

# Include source citation URLs in the output
python3 mhtml2md.py conversation.mhtml --include-sources

# Verbose mode (shows extraction progress)
python3 mhtml2md.py conversation.mhtml -v
```

## How to save a Google AI Mode conversation

1. Open the conversation in Chrome
2. Make sure all responses are fully expanded (click any collapsed sections)
3. `Ctrl+S` (or `Cmd+S` on Mac) → choose **"Webpage, Single File (.mhtml)"**
4. Run `mhtml2md.py` on the saved file

## Example output

A conversation that starts with a query like "how did wikipedia help ai" produces:

```markdown
# how did wikipedia help ai

*Saved: Wed, 4 Mar 2026 03:53:07 +0100*

*Source: Google AI Mode*

---

## Prompt 1

how did wikipedia help ai

## Response 1

Wikipedia has played a pivotal role in the advancement of AI by serving as
the primary high-quality training dataset for Large Language Models (LLMs)...

### 1. The "Backbone" of AI Training

Wikipedia is considered one of the highest-quality datasets in the world
for training AI.

---

## Prompt 2

Are open source models trained on Wikipedia allowed?

## Response 2

Yes, open-source models trained on Wikipedia are fully allowed...
```

## Limitations

- **Google's markup changes over time.** The script relies on CSS class names (`tonYlb`, `CKgc1d`, `VndcI`, `Y3BBE`, etc.) that Google may update. If extraction breaks after a Google frontend update, the class names in `extract_conversation` and `_extract_ai_response` will need updating. A fallback text extractor kicks in if the class-based approach fails entirely.
- **Collapsed content is not captured.** If parts of the AI response were collapsed/truncated in the browser when you saved the file, they won't appear in the MHTML. Expand everything before saving.
- **LaTeX and advanced formatting** are preserved as plain text rather than being converted to Markdown math syntax.
- **Source citation chips** (the small "Medium +1", "GitHub Docs +2" badges) are stripped by default. Use `--include-sources` to keep them.

## How it works

1. **MIME parsing** — `.mhtml` files use the same MIME multipart format as email. The script extracts the main HTML part and decodes the quoted-printable encoding.
2. **Turn detection** — Google AI Mode wraps each user→AI exchange in a `div.tonYlb` container. User messages are in `span.VndcI`, AI responses in `div.CKgc1d`.
3. **Content block extraction** — Within each AI response, content blocks are classified by type (paragraph, heading, code block, table, list, blockquote) and converted to the appropriate Markdown syntax.
4. **Noise removal** — Google's framework markers (`TgQPHd`, `qkimaf`, `cqw1tb`), UI chrome (feedback buttons, "Searching" indicators), and citation chip remnants are stripped.

## License

MIT

## Credits

Built by [Thomas Horsten](https://github.com/horsten) & [Claude Opus 4.6](https://claude.ai) (Anthropic) in a collaborative session, March 2026.
