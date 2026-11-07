"""Assemble the three local design studies into one conversation preview."""
from pathlib import Path
import base64
import re

base = Path(__file__).resolve().parent
parts = base / "parts"
font_base = base.parent / "node_modules" / "@fontsource" / "ibm-plex-sans"
styles = []
sections = []
for name in ("console", "protocol", "studio"):
    source = (parts / f"{name}.html").read_text(encoding="utf-8-sig")
    styles.extend(re.findall(r"<style>(.*?)</style>", source, flags=re.S))
    sections.append(re.sub(r"<style>.*?</style>", "", source, flags=re.S).strip())

for weight in (400, 500, 600):
    for subset in ("cyrillic", "latin"):
        font = font_base / "files" / f"ibm-plex-sans-{subset}-{weight}-normal.woff2"
        font_css = (font_base / f"{weight}.css").read_text(encoding="utf-8")
        font_block = next(block for block in font_css.split("}") if font.name in block)
        unicode_range = re.search(r"unicode-range:\s*([^;]+);", font_block).group(1)
        encoded = base64.b64encode(font.read_bytes()).decode("ascii")
        styles.append("@font-face{font-family:'EVA Plex';font-style:normal;font-weight:" + str(weight)
                      + ";font-display:swap;src:url(data:font/woff2;base64," + encoded
                      + ") format('woff2');unicode-range:" + unicode_range + ";}")

styles.append("""
#eva-root .ev-app{min-width:0;min-height:1040px;--eva-reading-size:14px}
#eva-root .ev-app [data-notice]{overflow-wrap:anywhere}
#eva-root .ev-app button:disabled{cursor:default;opacity:.65}
#eva-root .ev-app [data-appeal-reason]{max-width:100%}
#eva-root .ev-studio{display:flex;flex-direction:column;border-radius:16px;border:1px solid var(--st-line);overflow:clip}
#eva-root .ev-studio .st-main{flex:1;display:flex;flex-direction:column}
#eva-root .ev-studio .st-actions{margin-top:auto}
#eva-root .ev-studio .st-user{margin-right:72px}
@media(max-width:800px){#eva-root .ev-app{min-height:1444px}#eva-root .ev-studio .st-user{margin-right:0}}
@media(max-width:600px){#eva-root .ev-app{min-height:1800px}}
@media(max-width:360px){#eva-root .ev-app{min-height:1880px}}
#eva-root .ev-studio .st-lane-name{white-space:nowrap}
@media(max-width:640px){#eva-root .ev-studio .st-lanes{grid-template-columns:64px minmax(0,1fr)}#eva-root .ev-studio .st-ruler{margin-left:64px}}
""")
credit = "<!-- Visual direction uses unslop by Damir Sokolovsky, https://github.com/heydamir/unslop, CC BY 4.0. Custom EVA interface studies. IBM Plex Sans: see FONT-LICENSE.txt. -->"
output = (credit + "\n<style>\n" + "\n".join(styles) + "\n</style>\n"
          + '<div id="eva-root">\n<div class="viz-carousel" aria-label="Варианты интерфейса EVA" data-previous-label="Предыдущий вариант" data-next-label="Следующий вариант">\n'
          + "\n".join(sections) + "\n</div>\n</div>\n<script>\n"
          + (parts / "interactions.js").read_text(encoding="utf-8") + "\n</script>\n")
assert len(output.encode("utf-8")) < 1_000_000
assert '<!doctype' not in output.lower()
(base / "eva-visual-directions.html").write_text(output, encoding="utf-8", newline="\n")
(base / "FONT-LICENSE.txt").write_text((font_base / "LICENSE").read_text(encoding="utf-8"), encoding="utf-8")
print(f"Assembled {len(sections)} designs; {len(output.encode('utf-8')):,} bytes")
