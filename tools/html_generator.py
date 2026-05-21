"""HTML report generation tools for Tony."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from server.paths import REPORTS_DIR


def generate_html_report(
    file_name: str,
    title: str,
    body_content: str,
    css_styles: Optional[str] = None,
    include_timestamp: bool = True,
) -> str:
    """
    Generate a styled HTML report and save to output/reports/.

    Args:
        file_name: Filename with .html extension (e.g., "analysis.html")
        title: Page title and header
        body_content: HTML body content (can include HTML tags)
        css_styles: Optional custom CSS (default styling provided)
        include_timestamp: Add generation timestamp to footer

    Returns:
        JSON string with status and file path
    """
    # Ensure .html extension
    if not file_name.endswith(".html"):
        file_name += ".html"

    # Default professional styling
    default_css = """
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               max-width: 1200px; margin: 0 auto; padding: 20px; line-height: 1.6; }
        h1 { color: #1a1a1a; border-bottom: 2px solid #333; padding-bottom: 10px; }
        h2 { color: #2a2a2a; margin-top: 30px; }
        h3 { color: #3a3a3a; margin-top: 25px; }
        .timestamp { color: #666; font-size: 0.9em; margin-top: 40px; border-top: 1px solid #ddd;
                     padding-top: 10px; }
        table { border-collapse: collapse; width: 100%; margin: 20px 0; }
        th, td { border: 1px solid #ddd; padding: 12px; text-align: left; }
        th { background-color: #f5f5f5; font-weight: 600; }
        tr:nth-child(even) { background-color: #fafafa; }
        .metric { display: inline-block; background: #f0f0f0; padding: 15px 20px;
                  margin: 10px 10px 10px 0; border-radius: 8px; }
        .metric-value { font-size: 1.5em; font-weight: bold; color: #333; }
        .metric-label { font-size: 0.9em; color: #666; }
        code { background: #f4f4f4; padding: 2px 6px; border-radius: 3px; font-family: monospace; }
        pre { background: #f4f4f4; padding: 15px; border-radius: 5px; overflow-x: auto; }
        ul, ol { margin: 15px 0; }
        li { margin: 5px 0; }
    </style>
    """

    css = css_styles or default_css

    timestamp_html = ""
    if include_timestamp:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M %Z")
        timestamp_html = f'<div class="timestamp">Generated: {timestamp}</div>'

    html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    {css}
</head>
<body>
    <h1>{title}</h1>
    {body_content}
    {timestamp_html}
</body>
</html>"""

    # Write to output/reports/
    output_path = REPORTS_DIR / file_name

    try:
        output_path.write_text(html_template, encoding="utf-8")
        return json.dumps({
            "status": "success",
            "path": str(output_path),
            "filename": file_name,
            "size_bytes": len(html_template.encode("utf-8"))
        })
    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": str(e)
        })


def generate_html_from_markdown(
    file_name: str,
    title: str,
    markdown_content: str,
) -> str:
    """
    Convert markdown content to styled HTML report.
    Basic markdown parsing for headers, lists, bold, italic.

    Args:
        file_name: Filename with .html extension
        title: Page title
        markdown_content: Markdown formatted text

    Returns:
        JSON string with status and file path
    """
    import re

    html_body = markdown_content

    # Headers (process from largest to smallest)
    html_body = re.sub(r'^#### (.+)$', r'<h4>\1</h4>', html_body, flags=re.MULTILINE)
    html_body = re.sub(r'^### (.+)$', r'<h3>\1</h3>', html_body, flags=re.MULTILINE)
    html_body = re.sub(r'^## (.+)$', r'<h2>\1</h2>', html_body, flags=re.MULTILINE)
    html_body = re.sub(r'^# (.+)$', r'<h1>\1</h1>', html_body, flags=re.MULTILINE)

    # Bold and italic (process iteratively to handle nested)
    # Bold: **text** → <strong>text</strong>
    html_body = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html_body)
    # Italic: *text* → <em>text</em> (but not already processed bold)
    html_body = re.sub(r'\*(.+?)\*', r'<em>\1</em>', html_body)

    # Lists
    lines = html_body.split('\n')
    in_list = False
    new_lines = []

    for line in lines:
        stripped = line.strip()
        # Bullet lists (• or - or *)
        if stripped.startswith('• ') or stripped.startswith('- ') or (stripped.startswith('* ') and not stripped.startswith('**')):
            if not in_list:
                new_lines.append('<ul>')
                in_list = True
            content = stripped[2:]
            new_lines.append(f'<li>{content}</li>')
        # Numbered lists
        elif re.match(r'^\d+\. ', stripped):
            if not in_list:
                new_lines.append('<ol>')
                in_list = True
            content = re.sub(r'^\d+\. ', '', stripped)
            new_lines.append(f'<li>{content}</li>')
        else:
            if in_list:
                # Check if previous list was ordered or unordered
                if '<ol>' in new_lines[-10:] and '<ul>' not in new_lines[-10:]:  # Simple heuristic
                    new_lines.append('</ol>')
                else:
                    new_lines.append('</ul>')
                in_list = False
            new_lines.append(line)

    if in_list:
        new_lines.append('</ul>')

    html_body = '\n'.join(new_lines)

    # Code blocks (```)
    html_body = re.sub(r'```(\w+)?\n(.+?)```', r'<pre><code>\2</code></pre>', html_body, flags=re.DOTALL)

    # Inline code (`text`)
    html_body = re.sub(r'`(.+?)`', r'<code>\1</code>', html_body)

    # Paragraphs (wrap text blocks not already in tags)
    paragraphs = html_body.split('\n\n')
    wrapped = []
    for p in paragraphs:
        p = p.strip()
        if p and not p.startswith('<') and not p.endswith('>'):
            wrapped.append(f'<p>{p}</p>')
        else:
            wrapped.append(p)
    html_body = '\n\n'.join(wrapped)

    return generate_html_report(
        file_name=file_name,
        title=title,
        body_content=html_body
    )
