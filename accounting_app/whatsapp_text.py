"""The chat's HTML answers, rewritten for WhatsApp.

The web chat answers in HTML: bold figures, tables, download links, follow-up
buttons. WhatsApp shows plain text with *bold* and _italic_, caps a message at
4096 characters, and a wide table is unreadable on a phone. So:

  * <b> becomes *bold*, <i> becomes _italic_, <br> and blocks become new lines;
  * a table becomes one line per row - the first cell, then "Header: value"
    for the rest - cut short after MAX_ROWS with a pointer to the PDF;
  * the download links go (the reply offers PDF / EXCEL instead), and the
    follow-up buttons become a short "You can also ask" line.
"""
import html as html_lib
import re
from html.parser import HTMLParser

MAX_ROWS = 12
MAX_VALUE_COLUMNS = 4     # a wider table keeps its first two and last two
MAX_CHARS = 3900          # WhatsApp's limit is 4096; leave room for the footer
MAX_FOLLOWUPS = 3

TOKEN_RE = re.compile(r"export_chat_result\?token=([0-9a-f]{32})")


class _Converter(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.skip = 0              # depth inside something that is not shown
        self.skip_tag = None
        self.table = None          # {"head": [...], "rows": [[...]], "cell": [...]}
        self.row = None
        self.in_head = False
        self.followups = []
        self.in_followups = 0
        self.follow_text = None
        self.lead = []             # the Agent's own words, moved to the top
        self.in_say = 0

    # -- helpers
    def _emit(self, text):
        if self.in_say:
            self.lead.append(text)
        elif self.table is not None and self.row is not None and self.table.get("cell") is not None:
            self.table["cell"].append(text)
        elif self.in_followups:
            if self.follow_text is not None:
                self.follow_text.append(text)
        else:
            self.out.append(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if self.skip:
            if tag == self.skip_tag:
                self.skip += 1
            return
        # Not shown on WhatsApp: download links (the reply offers PDF / EXCEL),
        # the "computed from" note, and the web chat's thumbs-up/down buttons.
        if (tag in ("script", "style", "svg", "canvas")
                or (tag == "a" and "export_chat_result" in (a.get("href") or ""))
                or (tag == "small" and ("rv-alt" in cls or "rv-src" in cls))
                or (tag == "div" and "rv-feedback" in cls)):
            self.skip, self.skip_tag = 1, tag
            return
        if tag == "div" and "rv-followups" in cls:
            self.in_followups += 1
            return
        if self.in_say:
            if tag == "div":
                self.in_say += 1
            elif tag == "br":
                self._emit("\n")
            return
        if tag == "div" and "rv-agent-say" in cls:
            self.in_say = 1
            return
        # A choice button ("did you mean ...") cannot be tapped in a message,
        # so each becomes its own line; the channel numbers them.
        if tag == "button" and "rv-pick" in cls:
            self._emit("\n• ")
            return
        if self.in_followups:
            if tag == "div":
                self.in_followups += 1
            if tag == "button":
                self.follow_text = []
            return
        if tag == "table":
            self.table = {"head": [], "rows": [], "cell": None}
        elif tag == "thead":
            self.in_head = True
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in ("td", "th") and self.table is not None:
            self.table["cell"] = []
        elif tag in ("b", "strong"):
            self._emit("*")
        elif tag in ("i", "em"):
            self._emit("_")
        elif tag == "br":
            self._emit("\n")
        elif tag == "li":
            self._emit("\n• ")
        elif tag in ("p", "div", "ul", "ol"):
            self._emit("\n")

    def handle_endtag(self, tag):
        if self.skip:
            if tag == self.skip_tag:
                self.skip -= 1
            return
        if self.in_say:
            if tag == "div":
                self.in_say -= 1
            return
        if self.in_followups:
            if tag == "button" and self.follow_text is not None:
                text = " ".join("".join(self.follow_text).split())
                if text:
                    self.followups.append(text)
                self.follow_text = None
            elif tag == "div":
                self.in_followups -= 1
            return
        if tag in ("td", "th") and self.table is not None and self.row is not None:
            cell = " ".join("".join(self.table["cell"] or []).split())
            self.row.append(cell)
            self.table["cell"] = None
        elif tag == "tr" and self.table is not None and self.row is not None:
            if self.in_head and not self.table["head"]:
                self.table["head"] = self.row
            elif self.row:
                self.table["rows"].append(self.row)
            self.row = None
        elif tag == "thead":
            self.in_head = False
        elif tag == "table" and self.table is not None:
            self.out.append("\n" + _render_table(self.table) + "\n")
            self.table = None
        elif tag in ("b", "strong"):
            self._emit("*")
        elif tag in ("i", "em"):
            self._emit("_")
        elif tag in ("p", "div", "ul", "ol"):
            self._emit("\n")

    def handle_data(self, data):
        if self.skip:
            return
        self._emit(data)


def _render_table(table):
    head, rows = table["head"], table["rows"]
    if not head and rows and all(len(r) for r in rows):
        head = [""] * max(len(r) for r in rows)
    lines = []
    for row in rows[:MAX_ROWS]:
        label = row[0] if row else ""
        rest = []
        for i, value in enumerate(row[1:], start=1):
            if value == "":
                continue
            name = head[i] if i < len(head) else ""
            rest.append(f"{name}: {value}" if name else value)
        # A wide report (ageing has fourteen columns) is unreadable on a
        # phone: keep the first two and the last two - usually the name and
        # the total - and point at the file for the rest.
        if len(rest) > MAX_VALUE_COLUMNS:
            rest = rest[:2] + ["…"] + rest[-2:]
        lines.append(f"• *{label}*" + (" — " + " · ".join(rest) if rest else ""))
    if len(rows) > MAX_ROWS:
        lines.append(f"_…and {len(rows) - MAX_ROWS} more. Reply *PDF* for the full list._")
    if not rows:
        lines.append("_No rows._")
    return "\n".join(lines)


def to_whatsapp(html):
    """(text, export_token or None) for a chat answer."""
    html = html or ""
    match = TOKEN_RE.search(html)
    token = match.group(1) if match else None

    parser = _Converter()
    parser.feed(html)
    parser.close()
    text = html_lib.unescape("".join(parser.out))
    lead = html_lib.unescape("".join(parser.lead)).strip()
    if lead:
        # The Agent's summary first - it is the answer; the tables back it up.
        text = lead + "\n_Written by AI from the figures below._\n\n" + text

    # Tidy: no stray spaces at line ends, at most one blank line, and no
    # "* *" left by a bold tag around nothing.
    text = text.replace("\xa0", " ")
    text = text.replace("**", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if parser.followups:
        text += "\n\n_You can also ask:_ " + " · ".join(parser.followups[:MAX_FOLLOWUPS])
    if token:
        text += "\n\nReply *PDF* or *EXCEL* for this as a file."
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rsplit("\n", 1)[0] + "\n_…cut short. Reply *PDF* for all of it._"
    return text, token
