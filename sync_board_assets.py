"""Embed the maintained board assets into the existing self-contained dashboard."""
from pathlib import Path
import re

root = Path(__file__).resolve().parent
page = root / 'liveintel_v2.html'
html = page.read_text()
for asset, tag, element in [('board.css','style','board-styles'),('board.js','script','board-script')]:
    pattern = r'<' + tag + r' id="' + element + r'">.*?</' + tag + '>'
    replacement = '<'+tag+' id="'+element+'">\n'+(root/asset).read_text()+'\n</'+tag+'>'
    html, count = re.subn(pattern,lambda match:replacement,html,flags=re.S)
    if count != 1:
        raise SystemExit('Expected exactly one embedded '+element)
math = '<script id="ticket-math-script">\n'+(root/'ticket_math.js').read_text()+'\n</script>\n'
html = re.sub(r'<script id="ticket-math-script">.*?</script>\s*','',html,flags=re.S)
html = html.replace('<script id="board-script">',math+'<script id="board-script">')
page.write_text(html)
