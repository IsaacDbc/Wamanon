#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Streamlit WhatsApp Viewer — Pour Manon 💚

- Drag & drop de plusieurs exports WhatsApp .zip
- Parsing robuste (crochets, 12/24h, —/–/-, espaces avant ":", NBSP, etc.)
- UI façon WhatsApp + badge “Pour Manon”
- Export PDF (WeasyPrint si dispo, sinon ReportLab)

Dépendances minimales :
    pip install streamlit jinja2 reportlab
Optionnel (PDF plus joli) :
    pip install weasyprint
"""
import base64
import datetime as dt
import io
import re
import zipfile
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import streamlit as st

# --- Page setup
st.set_page_config(page_title="WhatsApp Viewer — Pour Manon", layout="wide", page_icon="💚")

# --- Styles
BASE_CSS = """
<style>
* { box-sizing: border-box; }

/* Force le texte en noir, même en thème sombre */
:root, .stApp, body { color:#111 !important; }

/* Fond + en-tête */
body { background: linear-gradient(180deg,#e5ddd5 0%, #efeae2 100%); }
.header { background:#075e54; color:#fff !important; padding:14px 18px; font-weight:700; border-radius:12px; margin-bottom:8px; }
.badge { display:inline-block; padding:6px 10px; background:#25d366; color:#033 !important; border-radius:999px; font-weight:700; font-size:12px; margin-left:8px; }

/* Conteneur & bulles */
.container { background:#efeae2; border-radius:12px; padding:8px 8px 80px; min-height:60vh; border:1px solid #ded6cf; color:#111 !important; }
.bubbles { display:flex; flex-direction:column; gap:10px; }
.msg { max-width:72%; padding:8px 10px; border-radius:12px; position:relative; box-shadow: 0 1px 0 rgba(0,0,0,0.06); color:#111 !important; }
.msg div, .doc, .author, .meta { color:#111 !important; }

/* Couleurs des bulles */
.left  { background:#ffffff; align-self:flex-start; border-top-left-radius:0; }
.right { background:#d9fdd3; align-self:flex-end;   border-top-right-radius:0; }

.meta   { font-size:11px; color:#555 !important; margin-top:4px; text-align:right; }
.author { font-size:12px; font-weight:600; margin-bottom:4px; color:#075e54 !important; }

/* Médias */
img.media, video.media { max-width:100%; border-radius:10px; margin-top:6px; display:block; }
.audio { margin-top:6px; width:100%; }
.doc   { margin-top:6px; font-size:13px; }

/* Divers */
hr.sep { border:0; height:1px; background:#ddd; margin:8px 0; }
.sidebar-note { font-size:12px; color:#444 !important; }
a { color:#0b57d0 !important; }
</style>
"""

st.markdown(BASE_CSS, unsafe_allow_html=True)

# --- Helpers & parsing
MEDIA_EXTS = {
    "image": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"},
    "video": {".mp4", ".3gp", ".mov", ".avi", ".mkv", ".m4v"},
    "audio": {".opus", ".ogg", ".mp3", ".wav", ".m4a"},
    "doc": {".pdf", ".txt", ".vcf", ".csv", ".doc", ".docx", ".xls", ".xlsx", ".zip"}
}
ALL_MEDIA_EXTS = set().union(*MEDIA_EXTS.values())

# Formats de ligne reconnus (crochets / sans virgule / AM-PM / secondes optionnelles / espaces avant ':')
DATE_TIME_PATTERNS = [
    # [DD/MM/YYYY HH:MM(:SS)?] Name : msg
    (re.compile(r"^\[(\d{1,2}[\/\.\-]\d{1,2}[\/\.\-]\d{2,4})\s+(\d{1,2}:\d{2}(?::\d{2})?)\]\s+([^:]+?)\s*:\s(.*)$"), "%d/%m/%Y %H:%M:%S"),
    # [DD/MM/YYYY HH:MM(:SS)? AM/PM] Name : msg
    (re.compile(r"^\[(\d{1,2}[\/\.\-]\d{1,2}[\/\.\-]\d{2,4})\s+(\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM|am|pm))\]\s+([^:]+?)\s*:\s(.*)$"), "%d/%m/%Y %I:%M:%S %p"),
    # DD/MM/YYYY, HH:MM(:SS)? { -,–,— } Name : msg
    (re.compile(r"^(\d{1,2}[\/\.\-]\d{1,2}[\/\.\-]\d{2,4}),\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*[–—-]\s*([^:]+?)\s*:\s(.*)$"), "%d/%m/%Y %H:%M:%S"),
    # DD/MM/YY, HH:MM(:SS)? { -,–,— } Name : msg
    (re.compile(r"^(\d{1,2}[\/\.\-]\d{1,2}[\/\.\-]\d{2}),\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*[–—-]\s*([^:]+?)\s*:\s(.*)$"), "%d/%m/%y %H:%M:%S"),
    # iOS style sans virgule (sans crochets), "date time Name : msg"
    (re.compile(r"^(\d{1,2}[\/\.\-]\d{1,2}[\/\.\-]\d{2,4})\s+(\d{1,2}:\d{2}:\d{2})\s+([^:]+?)\s*:\s(.*)$"), "%d/%m/%Y %H:%M:%S"),
]

MEDIA_OMITTED_TOKENS = {"<Media omitted>", "<Média omis>", "<Média omise>", "image omitted", "video omitted", "image omise", "video omise"}

def classify_ext(path: Path) -> str:
    ext = path.suffix.lower()
    for kind, exts in MEDIA_EXTS.items():
        if ext in exts:
            return kind
    return "doc"

def parse_datetime(date_str: str, time_str: str) -> Optional[dt.datetime]:
    # Normalise séparateurs et espaces (NBSP, NNBSP)
    d_norm = re.sub(r"[.\-]", "/", date_str).replace("\u00a0", " ").replace("\u202f", " ").strip()
    t_norm = time_str.replace("\u00a0", " ").replace("\u202f", " ").strip().upper()

    fmts = [
        # 24h
        "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
        "%d/%m/%y %H:%M:%S", "%d/%m/%y %H:%M",
        "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M",
        "%m/%d/%y %H:%M:%S",  "%m/%d/%y %H:%M",
        # 12h AM/PM
        "%d/%m/%Y %I:%M:%S %p", "%d/%m/%Y %I:%M %p",
        "%d/%m/%y %I:%M:%S %p", "%d/%m/%y %I:%M %p",
        "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p",
        "%m/%d/%y %I:%M:%S %p",  "%m/%d/%y %I:%M %p",
    ]
    for f in fmts:
        try:
            return dt.datetime.strptime(f"{d_norm} {t_norm}", f)
        except Exception:
            continue
    return None

def detect_txt_file(extract_dir: Path) -> Optional[Path]:
    txts = list(extract_dir.glob("*.txt")) + list(extract_dir.glob("**/*.txt"))
    if not txts:
        return None
    txts.sort(key=lambda p: p.stat().st_size if p.exists() else 0, reverse=True)
    return txts[0]

def detect_title_from_txtname(txt_path: Path) -> str:
    name = txt_path.stem
    name = re.sub(r"^WhatsApp Chat with\s+", "", name, flags=re.IGNORECASE)
    name = re.sub(r"^Discussion WhatsApp avec\s+", "", name, flags=re.IGNORECASE)
    name = name.replace("_", " ")
    return name or "WhatsApp Chat"

class Attachment:
    def __init__(self, relpath: str, kind: str, filename: str):
        self.relpath = relpath
        self.kind = kind
        self.filename = filename

class Message:
    def __init__(self, ts: dt.datetime, author: str, text: str):
        self.timestamp = ts
        self.author = author
        self.text = text
        self.attachments: List[Attachment] = []

class Conversation:
    def __init__(self, chat_id: str, title: str, messages: List[Message], base_dir: Path):
        self.chat_id = chat_id
        self.title = title
        self.messages = messages
        self.base_dir = base_dir

def parse_chat_text(txt_path: Path) -> Tuple[str, List[Message]]:
    data = None
    for enc in ("utf-8-sig", "utf-16", "utf-8"):
        try:
            data = txt_path.read_text(encoding=enc)
            break
        except Exception:
            continue
    if data is None:
        raise RuntimeError(f"Impossible de lire {txt_path}")
    lines = data.splitlines()
    messages: List[Message] = []
    current: Optional[Message] = None
    title = detect_title_from_txtname(txt_path)

    for raw in lines:
        # Normalise LRM + NBSP + NNBSP
        line = raw.replace("\u200e", "").replace("\u00a0", " ").replace("\u202f", " ").strip()
        matched = False
        for pat, _fmt in DATE_TIME_PATTERNS:
            m = pat.match(line)
            if m:
                matched = True
                date_part, time_part, author, text = m.group(1), m.group(2), m.group(3).strip(), m.group(4)
                ts = parse_datetime(date_part, time_part) or dt.datetime.now()
                if current:
                    messages.append(current)
                current = Message(ts, author, text.strip())
                break
        if not matched:
            if current:
                current.text += "\n" + line
            else:
                continue
    if current:
        messages.append(current)
    messages.sort(key=lambda m: m.timestamp)
    return title, messages

def link_attachments(conv: Conversation) -> None:
    """Associer heuristiquement les fichiers médias aux messages."""
    media_files: Dict[str, Path] = {}
    for p in conv.base_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in ALL_MEDIA_EXTS:
            media_files[p.name] = p
    assigned = {k: False for k in media_files.keys()}

    # Regex sûres (pas de 'bad character range')
    filename_pat = re.compile(r"([A-Za-z0-9_-]+-\d{8}-WA\d+\.[A-Za-z0-9]{1,5})")
    generic_file_pat = re.compile(
        r"([\w.\-]+\.(?:jpg|jpeg|png|gif|mp4|3gp|mov|avi|mkv|m4v|opus|ogg|mp3|wav|m4a|pdf|webp|heic|docx?|xlsx?|zip))",
        re.IGNORECASE
    )
    date_from_name = re.compile(r".*-(\d{8})-WA\d+\.[A-Za-z0-9]{1,5}$")

    for msg in conv.messages:
        files_in_text = set()
        for rx in (filename_pat, generic_file_pat):
            for m in rx.finditer(msg.text):
                files_in_text.add(m.group(1))
        for fname in files_in_text:
            p = media_files.get(fname)
            if p and not assigned[fname]:
                msg.attachments.append(Attachment(str(p.relative_to(conv.base_dir)), classify_ext(p), fname))
                assigned[fname] = True

    # Heuristique par date si le nom ressemble à ...-YYYYMMDD-...
    for msg in conv.messages:
        if msg.attachments:
            continue
        text_l = msg.text.strip().lower()
        if not text_l or any(tok.lower() in text_l for tok in MEDIA_OMITTED_TOKENS):
            for fname, p in list(media_files.items()):
                if assigned.get(fname):
                    continue
                m = date_from_name.match(fname)
                if not m:
                    continue
                try:
                    d = dt.datetime.strptime(m.group(1), "%Y%m%d").date()
                except Exception:
                    continue
                if d == msg.timestamp.date():
                    msg.attachments.append(Attachment(str(p.relative_to(conv.base_dir)), classify_ext(p), fname))
                    assigned[fname] = True
                    break

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def safe_slug(s: str) -> str:
    import re as _re
    slug = _re.sub(r"[^a-zA-Z0-9_-]+", "_", s.strip())
    return slug[:80] if slug else "chat"

def b64_image(path: Path) -> Optional[str]:
    try:
        mime = {
            ".jpg":"image/jpeg",".jpeg":"image/jpeg",".png":"image/png",".gif":"image/gif",".webp":"image/webp",".heic":"image/heic"
        }.get(path.suffix.lower(), "application/octet-stream")
        data = path.read_bytes()
        return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
    except Exception:
        return None

def render_chat_html(conv: Conversation, me_names: List[str], show_author: bool) -> str:
    html_parts = []
    html_parts.append(f'<div class="header">{conv.title}<span class="badge">Pour Manon</span></div>')
    html_parts.append('<div class="container"><div class="bubbles">')
    for m in conv.messages:
        side = "right" if m.author in me_names else "left"
        bubble = [f'<div class="msg {side}">']
        if show_author:
            bubble.append(f'<div class="author">{m.author}</div>')
        for line in m.text.split("\\n"):
            bubble.append(f"<div>{line}</div>")
        for a in m.attachments:
            p = conv.base_dir / a.relpath
            if a.kind == "image":
                src = b64_image(p)
                if src:
                    bubble.append(f'<img class="media" src="{src}" alt="{a.filename}">')
                else:
                    bubble.append(f'<div class="doc">🖼 {a.filename}</div>')
            elif a.kind == "video":
                bubble.append(f'<div class="doc">🎞 {a.filename}</div>')
            elif a.kind == "audio":
                bubble.append(f'<div class="doc">🔊 {a.filename}</div>')
            else:
                bubble.append(f'<div class="doc">📎 {a.filename}</div>')
        bubble.append(f'<div class="meta">{m.timestamp.strftime("%d/%m/%Y %H:%M")}</div>')
        bubble.append("</div>")
        html_parts.append("".join(bubble))
    html_parts.append("</div></div>")
    return "".join(html_parts)

# --- Sidebar (upload + options)
st.sidebar.title("📦 Import")
uploaded = st.sidebar.file_uploader("Glisse-dépose un ou plusieurs exports WhatsApp (.zip)", type=["zip"], accept_multiple_files=True)
me_name = st.sidebar.text_input('Ton nom (pour aligner tes messages à droite)', value="")
st.sidebar.markdown('<span class="sidebar-note">Astuce: exporte la discussion avec les médias sur iPhone/Android.</span>', unsafe_allow_html=True)

# --- Work directory
root = Path(st.session_state.get("wa_root", str(Path.home() / ".wa_streamlit")))
ensure_dir(root)

def load_zip(upload_file) -> Optional["Conversation"]:
    stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S%f")
    extract_dir = root / f"upload_{stamp}"
    ensure_dir(extract_dir)
    data = upload_file.read()
    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as z:
            z.extractall(extract_dir)
    except zipfile.BadZipFile:
        st.warning(f"ZIP invalide: {upload_file.name}")
        return None
    txt_path = detect_txt_file(extract_dir)
    if not txt_path:
        st.warning(f"Aucun .txt trouvé dans {upload_file.name}")
        return None
    title, messages = parse_chat_text(txt_path)
    conv = Conversation(chat_id=safe_slug(title), title=title, messages=messages, base_dir=extract_dir)
    link_attachments(conv)
    return conv

# --- Header
st.markdown(f'<div class="header">Conversations WhatsApp <span class="badge">Pour Manon</span></div>', unsafe_allow_html=True)

# --- Load conversations
convs: Dict[str, Conversation] = {}
if uploaded:
    for uf in uploaded:
        conv = load_zip(uf)
        if conv:
            if conv.chat_id in convs:
                convs[conv.chat_id].messages.extend(conv.messages)
                convs[conv.chat_id].messages.sort(key=lambda m: m.timestamp)
            else:
                convs[conv.chat_id] = conv

if not convs:
    st.info("Dépose tes .zip ici pour commencer. Le viewer reconstruira la conversation avec un look WhatsApp ✨.")
    st.stop()

# --- Sidebar: list of convos
items = []
for cid, c in convs.items():
    if c.messages:
        first = c.messages[0].timestamp.strftime("%d/%m/%Y")
        last = c.messages[-1].timestamp.strftime("%d/%m/%Y")
        items.append((cid, f"{c.title} — {len(c.messages)} msgs — {first} → {last}"))

items.sort(key=lambda t: convs[t[0]].messages[-1].timestamp if convs[t[0]].messages else dt.datetime.min, reverse=True)

if not items:
    st.warning("Aucune discussion avec des messages exploitables n'a été trouvée dans tes .zip. Vérifie l'export (inclure les médias) et réessaie.")
    if convs:
        st.caption("Conversations détectées : " + ", ".join(sorted([c.title for c in convs.values()])))
    st.stop()

labels = [lbl for _, lbl in items]
choice = st.sidebar.selectbox("Choisis une discussion", options=list(range(len(items))), index=0,
                              format_func=lambda i: labels[i] if 0 <= i < len(labels) else "")
sel_cid = items[int(choice)][0]
conv = convs[sel_cid]

# --- Controls
c1, c2, c3 = st.columns([1,1,2])
with c1:
    show_author_default = (len({m.author for m in conv.messages}) > 2)
    show_author = st.toggle("Afficher l'auteur", value=show_author_default)
with c2:
    export_pdf_click = st.button("📄 Exporter en PDF")

# --- Render
me_names = [me_name.strip()] if me_name.strip() else []
me_names += ["You", "Vous", "Moi"]
html_chat = render_chat_html(conv, me_names=me_names, show_author=show_author)
st.markdown(html_chat, unsafe_allow_html=True)

# --- PDF export
def export_pdf(conv: Conversation, me_names: List[str]) -> Optional[Path]:
    try:
        from jinja2 import Template
        from weasyprint import HTML
        pdf_tpl = """
        <!doctype html><html><head><meta charset="utf-8">
        <style>{{ css }} body{background:white}.container{background:white}.msg{box-shadow:none}</style>
        </head><body>
        <div class="header">{{ conv.title }} <span class="badge">Pour Manon</span></div>
        <div class="container"><div class="bubbles">
        {% for m in conv.messages %}
          <div class="msg {{ 'right' if m.author in me_names else 'left' }}">
            {% if show_author %}<div class="author">{{ m.author }}</div>{% endif %}
            {% for line in m.text.split('\\n') %}<div>{{ line }}</div>{% endfor %}
            {% for a in m.attachments %}
              {% if a.kind == 'image' %}
                <img class="media" src="{{ base }}/{{ a.relpath }}" />
              {% elif a.kind == 'video' %}
                <div class="doc">🎞 {{ a.filename }}</div>
              {% elif a.kind == 'audio' %}
                <div class="doc">🔊 {{ a.filename }}</div>
              {% else %}
                <div class="doc">📎 {{ a.filename }}</div>
              {% endif %}
            {% endfor %}
            <div class="meta">{{ m.timestamp.strftime("%d/%m/%Y %H:%M") }}</div>
          </div>
        {% endfor %}
        </div></div></body></html>
        """
        tpl = Template(pdf_tpl)
        html_str = tpl.render(conv=conv, css=BASE_CSS, me_names=me_names,
                              show_author=(len({m.author for m in conv.messages})>2), base=str(conv.base_dir))
        out_dir = Path(root) / "pdf_exports"; ensure_dir(out_dir)
        out_pdf = out_dir / f"{safe_slug(conv.title)}.pdf"
        HTML(string=html_str, base_url=str(conv.base_dir)).write_pdf(str(out_pdf))
        return out_pdf
    except Exception:
        pass
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.lib.utils import ImageReader
        width, height = A4
        out_dir = Path(root) / "pdf_exports"; ensure_dir(out_dir)
        out_pdf = out_dir / f"{safe_slug(conv.title)}.pdf"
        c = rl_canvas.Canvas(str(out_pdf), pagesize=A4)
        margin = 15 * mm
        max_w = width - 2*margin
        y = height - margin
        me_set = set(me_names)
        def draw_text(text, right=False):
            nonlocal y
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.platypus import Paragraph, Frame
            from reportlab.lib.enums import TA_LEFT, TA_RIGHT
            from reportlab.lib.styles import ParagraphStyle
            style = ParagraphStyle('bubble', parent=getSampleStyleSheet()['Normal'],
                                   alignment=TA_RIGHT if right else TA_LEFT, fontSize=9, leading=11)
            p = Paragraph(text.replace("\\n","<br/>"), style)
            w, h = p.wrap(max_w, 10000)
            if y - h < margin: c.showPage(); y = height - margin
            x = width - margin - w if right else margin
            f = Frame(x, y - h, w, h, showBoundary=0)
            f.addFromList([p], c)
            y -= h + 6
        def draw_img(img_path, right=False):
            nonlocal y
            try:
                img = ImageReader(str(img_path))
                iw, ih = img.getSize()
                scale = min(1.0, max_w/iw)
                w, h = iw*scale, ih*scale
                if y - h < margin: c.showPage(); y = height - margin
                x = width - margin - w if right else margin
                c.drawImage(img, x, y - h, width=w, height=h, preserveAspectRatio=True, mask='auto')
                y -= h + 6
            except Exception:
                pass
        for m in conv.messages:
            right = (m.author in me_set)
            draw_text(f"{m.author} — {m.timestamp.strftime('%d/%m/%Y %H:%M')}", right)
            if m.text.strip():
                draw_text(m.text.strip(), right)
            for a in m.attachments:
                p = conv.base_dir / a.relpath
                if p.suffix.lower() in {'.jpg','.jpeg','.png','.gif'}:
                    draw_img(p, right)
                else:
                    draw_text(f"[{a.kind.upper()}] {a.filename}", right)
        c.save()
        return out_pdf
    except Exception:
        return None

if export_pdf_click:
    out = export_pdf(conv, me_names)
    if out and out.exists():
        st.success(f"PDF prêt : {out.name}")
        st.markdown(f"[Télécharger le PDF]({out.as_posix()})")
    else:
        st.error("Impossible de générer le PDF. Installe WeasyPrint ou ReportLab (voir la doc).")
