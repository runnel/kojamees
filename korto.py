#!/usr/bin/env python3
"""korto-cli — unofficial command-line companion for Korto Pro.

Not affiliated with or endorsed by OÜ Tarkvaralabor. It uses your own Korto session to
read and fill in the same pages the web UI shows you, at human pace. Changes are dry runs
unless --apply is given. See README.md, "Terms, law and data", before using it.
"""
import argparse
import calendar
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser

__version__ = "0.3.0"
HOST = "https://pro.korto.ee/"
BASE = HOST + "index.php"
USER_AGENT = f"korto-cli/{__version__} (+https://github.com/runnel/korto-cli)"
MIN_INTERVAL = 1.0  # seconds between requests: human pace, never parallel
EXPIRED = "Korto did not return a logged-in page: the session has expired. Copy PHPSESSID from a logged-in browser again."
BUTTONS = ("submit", "button", "reset", "image")
REOPEN = "Taasava periood"
UNKNOWN = "Look in the web UI before running it again."
CENT = Decimal("0.01")
HEADER = ("tehing[nimi]", "tehing[arve_nr]", "tehing[kuupaev]", "tehing[maksetahtaeg]", "tehing[teenuse_kuu]")
MONTHS = ["jaanuar", "veebruar", "märts", "aprill", "mai", "juuni", "juuli", "august",
          "september", "oktoober", "november", "detsember"]


class SessionExpired(Exception):
    pass


class UsageError(Exception):
    pass


# --- transport -------------------------------------------------------------------------

def ensure_logged_in(html):
    """Every Korto billing page has the period selector; the login page does not."""
    if 'name="perioodi_id"' not in html:
        raise SessionExpired(EXPIRED)
    return html


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):  # Korto redirects an expired session to its login page
        return None                     # never follow: the cookie must not travel anywhere else


_opener = urllib.request.build_opener(_NoRedirect)


class Client:
    def __init__(self, session_id, union_id):
        self.session_id, self.union_id, self._last, self.stamp = session_id, union_id, 0.0, None

    def get(self, screen, **params):
        return self._send(f"{BASE}?{urllib.parse.urlencode({'yhistu_id': self.union_id, **params, 'screen': screen})}")

    def post(self, fields):
        """Submit form fields as the browser does. Korto answers a save with a redirect, which is not
        followed: the caller reads the result back instead."""
        return self._send(BASE, urllib.parse.urlencode(fields).encode())

    def _send(self, url, data=None):
        time.sleep(max(0.0, self._last + MIN_INTERVAL - time.monotonic()))
        headers = {"Cookie": f"PHPSESSID={self.session_id}", "User-Agent": USER_AGENT}
        if data is not None:
            headers["Origin"] = HOST.rstrip("/")
        try:
            with _opener.open(urllib.request.Request(url, data, headers), timeout=60) as response:
                html = ensure_logged_in(response.read().decode("utf-8", "replace"))
                stamp = re.search(r"javascript/[^\"'?]+\.js\?(\d{8})", html)  # Korto's release stamp on its own scripts
                self.stamp = stamp.group(1) if stamp else self.stamp
                return html
        except urllib.error.HTTPError as error:
            if not 300 <= error.code < 400:
                raise
            error.close()
            location = urllib.parse.urlparse((error.headers or {}).get("Location") or "").query
            screen = (urllib.parse.parse_qs(location).get("screen") or ["sisenemine"])[0]
            if "sisenemine" in screen:
                raise SessionExpired(EXPIRED) from None
            if data is None:
                raise UsageError(f"Korto sent the page on to {screen}: korto-cli does not understand it") from None
            return ""  # a save, answered with a redirect
        finally:
            self._last = time.monotonic()


# --- parsing ---------------------------------------------------------------------------

class _Tables(HTMLParser):
    """Every <table> as rows of cells. A cell keeps its text, titles, inputs and data-* attributes."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self._open, self._cell, self._script = {}, [], None, False  # _open: (rows, outer cell)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self._script = tag == "script"
        if tag == "table":
            self._open.append((self.tables.setdefault(a.get("id") or f"#{len(self.tables)}", []), self._cell))
            self._cell = None
        elif tag == "tr" and self._open:
            self._close_cell()
            self._open[-1][0].append([])
        elif tag in ("td", "th") and self._open and self._open[-1][0]:
            self._close_cell()
            self._cell = {"text": "", "header": tag == "th", "titles": [], "inputs": [], "data": {}}
            self._open[-1][0][-1].append(self._cell)
        if self._cell is not None:
            if tag == "br":
                self._cell["text"] += " "
            if a.get("title"):
                self._cell["titles"].append(a["title"])
            if tag == "input" and a.get("name"):
                self._cell["inputs"].append((a["name"], a.get("value", "")))

            self._cell["data"].update({k: v for k, v in a.items() if k.startswith("data-")})

    def handle_endtag(self, tag):
        self._script = False
        if tag in ("td", "th", "tr"):
            self._close_cell()
        elif tag == "table" and self._open:
            self._close_cell()
            self._cell = self._open.pop()[1]  # back to the cell that contained a nested table
            if self._cell is not None:
                self._cell["text"] += " "

    def handle_data(self, data):
        if self._cell is not None and not self._script:
            self._cell["text"] += data

    def _close_cell(self):
        if self._cell is not None:
            self._cell["text"] = " ".join(self._cell["text"].split())
            self._cell = None


def tables(html):
    parser = _Tables()
    parser.feed(html)
    return parser.tables


def periods(html):
    """Periods from the selector, newest first: [{id, name, state}]."""
    select = re.search(r'<select[^>]*name="perioodi_id"[^>]*>(.*?)</select>', html, re.S)
    found = []
    for value, label in re.findall(r'<option value="(\d+)"[^>]*>([^<]*)</option>', select.group(1) if select else ""):
        m = re.match(r"\s*(.*?)\s*\((.*?)\)\s*$", unescape(label))
        found.append({"id": value, "name": m.group(1) if m else label.strip(), "state": m.group(2) if m else ""})
    return found


def period_end(name):
    """'2026 oktoober' -> date(2026, 10, 31)."""
    try:
        year, month = int(name.split()[0]), MONTHS.index(name.split()[1].lower()) + 1
    except (ValueError, IndexError):
        raise UsageError(f"not a monthly period: {name}") from None
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def _number(text):
    try:
        return float(text.replace(" ", "")) if text else None
    except ValueError:
        return None


def _date(text):
    try:
        return dt.datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError:
        return None


ENTERED = re.compile(r"Näidu sisestas: (.*?)( lingi kaudu)? \((\d\d\.\d\d\.\d{4} \d\d:\d\d)\)")
METER_INPUT = "korterite_arvestid["


def readings(html):
    """Building meters and apartment meters of one period."""
    t = tables(html)
    building = [{"meter": r[0]["text"], "start": _number(r[2]["text"]), "end": _number(r[3]["text"]),
                 "date": r[4]["text"], "quantity": _number(r[5]["text"]), "unit": r[10]["text"]}
                for r in t.get("yldarvestid", []) if len(r) >= 11 and not r[0]["header"]]
    apartments, rows = [], [r for r in t.get("korterite_arvestid", []) if r and not r[0]["header"]]
    for row in rows:
        # the meter cell carries a hidden korterite_arvestid[...] input; other columns are relative to it
        k = next((i for i, c in enumerate(row) if any(n.startswith(METER_INPUT) for n, _ in c["inputs"])), None)
        if k is None or k < 2 or len(row) < k + 8:
            continue
        address, text = row[k - 1]["data"].get("data-korteri_aadress", ""), row[k]["text"]
        entered = next((m for m in map(ENTERED.match, row[k + 2]["titles"]) if m), None)
        name, meter_id = next((n, v) for n, v in row[k]["inputs"] if n.startswith(METER_INPUT))
        apartments.append({
            "apartment": row[k - 2]["text"], "apartment_id": name[len(METER_INPUT):].split("]")[0],
            "meter": text[len(address):].strip() if address and text.startswith(address) else text,
            "meter_id": meter_id,
            "start": _number(row[k + 1]["text"]), "end": _number(row[k + 2]["text"]), "date": row[k + 3]["text"],
            "quantity": _number(row[k + 4]["text"]), "forecast": "Prognoositud" in row[k + 6]["titles"],
            "unit": row[k + 7]["text"], "entered_by": entered.group(1) if entered else None,
            "via_link": bool(entered and entered.group(2)), "entered_at": entered.group(3) if entered else None})
    if not apartments:  # every association has meters: none parsed means the layout changed
        raise UsageError("Korto's readings page has a layout korto-cli does not understand; nothing was checked.")
    return {"building": [b for b in building if b["unit"]], "apartments": apartments}


def missing_invoices(html):
    """Cost types Korto itself reports as having no expense invoice yet."""
    text = unescape(re.sub(r"<[^>]+>", " ", html))
    m = re.search(r"Kululii(?:gi|kide) (.*?) kuluarved? on sisestamata", text, re.S)
    return re.findall(r'["“„]([^"“”]+)["“”]', m.group(1)) if m else []


def grid(html):
    """The largest table on a page as plain text rows (for summary pages)."""
    rows = max(tables(html).values(), key=len, default=[])
    return [[c["text"] for c in row] for row in rows if any(c["text"] for c in row)]


# --- forms -----------------------------------------------------------------------------

class Form(list):
    """A form's controls, its attributes, and its shape (see fingerprint) as loaded."""
    attrs, shape = {}, {}


class _Forms(HTMLParser):
    """Each <form> as its controls in document order, each with what a browser would send ("on")."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms, self._dialogs, self._inside, self._text = [], [], False, None

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if tag == "div":  # Korto's dialogs are <div class="dialog"> inside the form
            self._dialogs.append(a.get("id", "") if "dialog" in a.get("class", "").split() else "")
        elif tag == "form":
            self._inside = True
            self.forms.append(Form())
            self.forms[-1].attrs = {k: a.get(k, "").lower() if k == "method" else a.get(k, "")
                                    for k in ("method", "action", "enctype", "onsubmit")}
        elif self._inside and tag in ("input", "select", "textarea", "button"):
            kind = a.get("type", "submit" if tag == "button" else "text" if tag == "input" else tag).lower()
            control = {"name": a.get("name", ""), "type": kind, "value": a.get("value", ""), "options": [],
                       "label": a.get("value", "") if tag == "input" and kind in BUTTONS else "",
                       "onclick": a.get("onclick", ""), "action": a.get("data-action", ""),
                       "dialog": next((d for d in reversed(self._dialogs) if d), ""), "disabled": "disabled" in a,
                       "on": "disabled" not in a and (kind not in BUTTONS + ("checkbox", "radio", "file") or "checked" in a)}
            self.forms[-1].append(control)
            self._text = control if tag in ("button", "textarea") else None
        elif tag == "option" and self._inside and self.forms[-1] and self.forms[-1][-1]["type"] == "select":
            select = self.forms[-1][-1]
            select["options"].append([a.get("value", ""), ""])
            if len(select["options"]) == 1 or "selected" in a:
                select["value"] = a.get("value", "")
            self._text = select["options"][-1]

    def handle_endtag(self, tag):
        if tag == "div" and self._dialogs:
            self._dialogs.pop()
        elif tag == "form":
            self._inside = False
        elif tag in ("button", "textarea", "option", "select"):
            self._text = None

    def handle_data(self, data):
        if isinstance(self._text, list):
            self._text[1] += data
        elif self._text is not None:
            self._text["label" if self._text["type"] in BUTTONS else "value"] += data


def forms(html):
    parser = _Forms()
    parser.feed(html)
    for form in parser.forms:
        form.shape = fingerprint(form)
    return parser.forms


def fingerprint(form):
    """A form's shape without its data: what Korto expects to receive. Names have numbers and ids as N;
    the order of control types per name matters, because PHP keeps the last of repeated names."""
    names, buttons = {}, {}
    for c in form:
        if c["type"] in BUTTONS:
            buttons[f"{_label(c)}|{c['dialog']}"] = [re.sub(r"\d+", "N", c["onclick"]), "confirm(" in c["onclick"], c["action"]]
        elif c["name"]:
            kind = c["type"] + ("!" if c["disabled"] else "")
            if c["type"] == "radio" or (c["type"] == "hidden" and c["name"] in ("screen", "action")):
                kind += f"={c['value']}"
            names.setdefault(c["name"], []).append(kind)
    fields = {}
    for name, kinds in names.items():
        fields.setdefault(re.sub(r"\d+", "N", name), set()).add(" ".join(kinds))
    return {"form": dict(form.attrs), "fields": {k: sorted(v) for k, v in sorted(fields.items())},
            "buttons": dict(sorted(buttons.items()))}


def differences(old, new):
    lines = []
    for part in ("form", "fields", "buttons"):
        a, b = old.get(part, {}), new.get(part, {})
        lines += [f"+ {k}" for k in b if k not in a] + [f"- {k}" for k in a if k not in b]
        lines += [f"~ {k}: {a[k]} -> {b[k]}" for k in a if k in b and a[k] != b[k]]
    return lines


def _label(control):
    return " ".join(control["label"].split())


def find_form(html, has):
    """The first form with a field named `has` or a button labelled `has`."""
    for form in forms(html):
        if any(has in (c["name"], _label(c)) for c in form):
            return form
    raise UsageError(f"Korto's page has no '{has}': the period may be in another state, or the page changed")


def payload(form):
    return [(c["name"], c["value"]) for c in form if c["on"] and c["name"]]


def value(form, name):
    """What Korto receives for a field: PHP keeps the last of repeated names."""
    sent = [v for n, v in payload(form) if n == name]
    return sent[-1] if sent else None


def fill(form, name, new, dialog=None):
    """Type into a field (str) or tick a checkbox (bool), as a user would; `dialog`: only inside that dialog."""
    matches = [c for c in form if c["name"] == name and c["type"] not in BUTTONS and dialog in (None, c["dialog"])]
    if not matches:
        raise UsageError(f"Korto's form has no field {name}")
    toggles = [c for c in matches if c["type"] in ("checkbox", "radio")]
    if isinstance(new, bool) and not toggles:
        raise UsageError(f"Korto's form has no checkbox {name}")
    if isinstance(new, bool):
        for c in toggles:
            c["on"] = new
    elif toggles:  # pick by value; a ticked-off checkbox leaves its hidden default, e.g. 'f'
        if new not in [c["value"] for c in matches]:
            raise UsageError(f"{name} has no choice {new}")
        for c in toggles:
            c["on"] = c["value"] == new
    else:
        target = matches[-1]  # PHP keeps the last of repeated names
        if target["options"] and new not in [v for v, _ in target["options"]]:
            raise UsageError(f"{name} has no option {new}")
        target["value"] = new


def press(form, label):
    """The fields sent when a button is clicked. Korto's buttons only set hidden fields before submitting."""
    buttons = [c for c in form if c["type"] == "submit" and _label(c) == label]
    buttons = [c for c in buttons if not c["dialog"]] or buttons  # the page's own button before a dialog's
    if len(buttons) != 1:
        raise UsageError(f"Korto's form has no '{label}' button: korto-cli does not understand this page")
    button = buttons[0]
    if form.attrs.get("method") != "post":
        raise UsageError(f"the '{label}' form is not posted: korto-cli writes only through POST forms")
    pairs = re.findall(r"elements\['([^']+)'\]\.value = '([^']*)'", button["onclick"])
    for inner in re.findall(r"setFormValues\(this\.form, \{(.*?)\}\)", button["onclick"]):
        pairs += re.findall(r"(\w+): '([^']*)'", inner)
    if button["disabled"] or not (pairs or button["action"]):
        raise UsageError(f"Korto's '{label}' button is disabled, or does what korto-cli does not understand")
    if button["action"]:  # a dialog button: Korto disables the other dialogs and posts its data-action
        for c in form:
            c["on"] = c["on"] and c["dialog"] in ("", button["dialog"])
        fill(form, "action", button["action"])
    for name, new in pairs:
        fill(form, name, new)
    return payload(form)


def rows_of(form, prefix):
    """prefix[i][field] fields grouped by row i, as Korto receives them."""
    rows = {}
    for name, v in payload(form):
        m = re.fullmatch(re.escape(prefix) + r"\[(\d+)\]\[(\w+)\]", name)
        if m:
            rows.setdefault(m.group(1), {})[m.group(2)] = v
    return rows


# --- checks ----------------------------------------------------------------------------

def check(current, previous, end_date, missing=(), ignore=()):
    """Findings for one period. 'block' must be fixed before distributing costs; 'warn' needs a look."""
    findings = []

    def add(level, apartment, meter, message):
        findings.append({"level": level, "apartment": apartment, "meter": meter, "message": message})

    heat = max((b["quantity"] for b in current["building"] if b["unit"] == "MWh" and b["quantity"] is not None),
               default=None)
    if heat is None and any(r["unit"] == "MWh" for r in current["apartments"]):
        add("warn", "", "building heat", "no building heat reading: the whole-building comparison was skipped")
    before = {r["meter_id"]: r["quantity"] for r in previous["apartments"]}
    for r in current["apartments"]:
        apt, meter, q = r["apartment"], r["meter"], r["quantity"]
        if f"{apt}:{meter}" in ignore:
            continue
        if r["end"] is None:
            add("block", apt, meter, "missing reading")
            continue
        when = _date(r["date"])
        if when and when > end_date:
            add("block", apt, meter, f"dated {r['date']}, after the period end {end_date:%d.%m.%Y}")
        if q is None:
            continue
        if heat and heat > 0 and r["unit"] == "MWh" and q > heat:
            add("block", apt, meter, f"{q:.3f} MWh is more than the whole building ({heat:.3f} MWh)")
        p = before.get(r["meter_id"])
        if p and p > 0 and q > 5 * p and q - p >= 0.5:  # absolute floor: tiny previous quantities are noise
            add("warn", apt, meter, f"{q:.3f} is over 5x the previous period ({p:.3f})")
        if q < 0:
            add("warn", apt, meter, f"negative consumption {q:.3f} (normal after a forecast)")
        if r["unit"] == "MWh" and q > 0 and q == int(q) and not r["forecast"]:
            add("warn", apt, meter, f"round number {q:.3f} MWh: an estimate?")
    for name in missing:
        add("block", "", name, "expense invoice missing")
    return findings


# --- command line ----------------------------------------------------------------------

def load_env(path):
    """Process environment, plus KEY=VALUE lines from an optional file (environment wins)."""
    env = dict(os.environ)
    if path:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    env.setdefault(key.strip(), value.split(" #", 1)[0].strip().strip("'\""))
    return env


def resolve(client, wanted, newest=False):
    """Period for an id or YYYY-MM; default: the open period (unless newest), else the newest."""
    available = periods(client.get("yyriarvestus.naidud"))
    if not wanted:
        match = next((p for p in available if p["state"] == "avatud" and not newest), available[0] if available else None)
    elif re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", wanted):
        match = next((p for p in available if p["name"] == f"{wanted[:4]} {MONTHS[int(wanted[5:]) - 1]}"), None)
    else:
        match = next((p for p in available if p["id"] == wanted), None)
    if not match:
        raise UsageError(f"period not found: {wanted}")
    return match, available


def show(rows, columns, as_json):
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    cells = [[("" if r.get(c) is None else str(r.get(c))) for c in columns] for r in rows]
    widths = [max([len(c)] + [len(row[i]) for row in cells]) for i, c in enumerate(columns)]
    for row in [columns] + cells:
        print("  ".join(v.ljust(w) for v, w in zip(row, widths)).rstrip())


def cmd_periods(client, args):
    show(periods(client.get("yyriarvestus.naidud")), ["id", "name", "state"], args.json)


def cmd_readings(client, args):
    period, _ = resolve(client, args.period)
    data = readings(client.get("yyriarvestus.naidud", perioodi_id=period["id"]))
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    show(data["building"], ["meter", "start", "end", "date", "quantity", "unit"], False)
    print()
    show(data["apartments"], ["apartment", "meter", "start", "end", "date", "quantity", "unit", "forecast",
                              "entered_by", "via_link"], False)


def cmd_check(client, args):
    period, available = resolve(client, args.period)
    index = available.index(period)
    current = readings(client.get("yyriarvestus.naidud", perioodi_id=period["id"]))
    previous = (readings(client.get("yyriarvestus.naidud", perioodi_id=available[index + 1]["id"]))
                if index + 1 < len(available) else {"building": [], "apartments": []})
    missing = missing_invoices(client.get("yyriarvestus.tehingud", perioodi_id=period["id"]))
    findings = check(current, previous, period_end(period["name"]), missing, set(args.ignore))
    show(findings, ["level", "apartment", "meter", "message"], args.json)
    if not args.json:
        print(f"\n{period['name']}: {sum(f['level'] == 'block' for f in findings)} blocking, "
              f"{sum(f['level'] == 'warn' for f in findings)} warnings")
    return 1 if any(f["level"] == "block" for f in findings) else 0


def cmd_summary(client, args):
    period, _ = resolve(client, args.period)
    pages = {name: grid(client.get(screen, perioodi_id=period["id"]))
             for name, screen in (("distribution", "yyriarvestus.maaramised"), ("invoices", "yyriarvestus.arved"))}
    if args.json:
        print(json.dumps(pages, ensure_ascii=False, indent=2))
        return
    for name, rows in pages.items():
        print(f"== {name}")
        for row in rows:
            print(" | ".join(row))
        print()


TABLES = {"costs": ("yyriarvestus.kululiigid", "Kululiik"), "payments": ("yyriarvestus.laekumised", "Maksja"),
          "balances": ("yyriarvestus.saldod", "Algsaldo"), "expenses": ("yyriarvestus.tehingud", "Partner"),
          "distribution": ("yyriarvestus.maaramised", "Korter"), "invoices": ("yyriarvestus.arved", "Arve nr.")}


def table(client, name, period):
    """One of Korto's tables, recognised by a column it must have."""
    screen, column = TABLES[name]
    candidates = [[[c["text"] for c in row] for row in t if any(c["text"] for c in row)]
                  for t in sorted(tables(client.get(screen, perioodi_id=period["id"])).values(), key=len, reverse=True)]
    rows = next((t for t in candidates if t and column in t[0]), None)
    if rows is None:
        raise UsageError(f"no {name} table with a '{column}' column on Korto's page: none yet, or a layout "
                         "korto-cli does not know")
    return rows


def cmd_table(client, args):
    period, _ = resolve(client, args.period)
    rows = table(client, args.name, period)
    print(json.dumps(rows, ensure_ascii=False, indent=2) if args.json else "\n".join(" | ".join(row) for row in rows))


def cmd_history(client, args):
    period, _ = resolve(client, args.period)
    _, rows, i = locate(client, period, args.apartment, args.meter)[1:4]
    page = client.get("yyriarvestus.naidud_ajalugu", perioodi_id=period["id"], arvesti_id=rows[i]["arvesti_id"])
    found = [dict(zip(("date", "reading", "quantity", "forecast", "unit", "period"), row))
             for row in grid(page) if _date(row[0])]
    print(rows[i]["arvesti"])
    show(found, ["date", "reading", "quantity", "forecast", "unit", "period"], args.json)


# --- write commands: dry runs unless --apply ---------------------------------------------

def writable(client, wanted):
    period, _ = resolve(client, wanted)
    if period["state"] != "avatud":
        raise UsageError(f"{period['name']} is {period['state']}: Korto takes changes only in an open period")
    return period


def in_period(text, period):
    """A dd.mm.yyyy date inside the period's month; default: the period end."""
    end = period_end(period["name"])
    day = _date(text) if text else end
    if not day or (day.year, day.month) != (end.year, end.month):
        raise UsageError(f"--date must be dd.mm.yyyy in {period['name']}")
    return f"{day:%d.%m.%Y}"


def state_of(client, period):
    return next((p["state"] for p in periods(client.get("yyriarvestus.naidud")) if p["id"] == period["id"]), "")


def submit(client, args, key, form, title, changes, fields, verify, problems=()):
    """Show the change; send it only with --apply and a matching baseline, then read it back from Korto."""
    last, mismatch = dict(fields), unmatched(client, key, form)  # PHP keeps the last of repeated names
    print(title)
    show([{"field": f, "now": now, "after": after} for f, now, after in changes], ["field", "now", "after"], False)
    print(f"\nKorto form: {last.get('screen')}, action {last.get('action') or '-'}")
    print(f"baseline: {mismatch or 'matches'}")
    for problem in problems:
        print(f"problem: {problem}")
    if problems:
        return 2
    if not args.apply:
        print("dry run: nothing was saved in Korto. Add --apply to save it.")
        return 0
    if mismatch:
        raise UsageError(f"not sent: {mismatch}. Run `korto selftest`, read the differences, then `korto selftest --accept`.")
    try:
        answer = client.post(fields)
        problem = verify(answer)
    except (SessionExpired, UsageError, OSError):
        print(f"The change may or may not be in Korto. {UNKNOWN}", file=sys.stderr)
        raise
    if problem:
        print(f"Korto does not show the change as sent: {problem}. {UNKNOWN}", file=sys.stderr)
        return 1
    print("done, and read back from Korto.")
    return 0


def readings_key(args):
    return "readings:building" if args.apartment == "building" else "readings:apartment"


def baseline_path():
    return os.environ.get("KORTO_BASELINE") or os.path.expanduser("~/.korto-baseline.json")


def load_baseline(client, everything=False):
    try:
        with open(baseline_path(), encoding="utf-8") as f:
            stored = json.load(f)
    except (OSError, ValueError):
        stored = {}
    stored = stored if isinstance(stored, dict) else {}
    mine = stored.get(str(client.union_id))
    return stored if everything else mine if isinstance(mine, dict) else {}


def unmatched(client, key, form):
    """Why the form about to be sent is not the one in the baseline, or None."""
    entry = load_baseline(client).get(key)
    if not client.stamp:
        return "Korto's pages carry no version stamp"
    if not isinstance(entry, dict) or not {"stamp", "shape"} <= set(entry):
        return f"no baseline for the {key} form"
    if entry["stamp"] != client.stamp:
        return f"Korto changed ({entry['stamp']} -> {client.stamp})"
    if entry["shape"] != form.shape:
        return f"the {key} form changed: " + "; ".join(differences(entry["shape"], form.shape))


KEYS = ("readings:apartment", "readings:building", "forecast", "distribute", "expense", "new-period",
        "close", "period:jaotatud")  # and cost-type:CODE for each cost type with a fixed price


def cost_types(listing):
    """(code, id) for each link on Korto's cost type list, duplicates kept."""
    return [(code, cost_id) for cost_id, code in re.findall(r"kululiigi_id=(\d+)[^>]*>\s*(\d+)\s", listing)]


def fixed_prices(listing):
    """The cost types whose row on the list names a fixed price, by a code used once: each has a form of its own shape."""
    found = [pair for row in re.findall(r"<tr.*?</tr>", listing, re.S) if "fikseeritud hind" in row for pair in cost_types(row)]
    codes = [code for code, _ in cost_types(listing)]
    return {code: cost_id for code, cost_id in found if codes.count(code) == 1}


def cmd_selftest(client, args):
    """Read the forms the current period states allow and compare them with the baseline."""
    available, loaders = periods(client.get("yyriarvestus.naidud")), {}
    current = next((p for p in available if p["state"] == "avatud"), None)
    if current:
        pid = current["id"]
        page = client.get("yyriarvestus.naidud", perioodi_id=pid)
        loaders["forecast"] = lambda: find_form(page, "kuude_arv")
        for key, apartment in (("readings:apartment", readings(page)["apartments"][0]["apartment_id"]), ("readings:building", "0")):
            loaders[key] = lambda apartment=apartment: find_form(client.get(
                "yyriarvestus.naidud_muuda", perioodi_id=pid, korteri_id=apartment, arvesti_id="0"), "naidud[0][arvesti_id]")
        loaders["distribute"] = lambda: find_form(client.get("yyriarvestus.perioodid_lukusta", perioodi_id=pid), "jaotamise_kuupaev")
        for code, cost_id in fixed_prices(client.get("yyriarvestus.kululiigid", perioodi_id=pid)).items():
            loaders[f"cost-type:{code}"] = lambda cost_id=cost_id: find_form(client.get(
                "yyriarvestus.kululiigid_form", perioodi_id=pid, kululiigi_id=cost_id, **{"return": ""}), "kululiik[fikseeritud_hind]")
        loaders["expense"] = lambda: prefilled(client, pid, None)[0]
    else:
        loaders["new-period"] = lambda: find_form(client.get("yyriarvestus.perioodid_form", perioodi_id="0"), "periood[aasta]")
    confirmed = next((p for p in available if p["state"] == "kinnitatud"), None)
    if confirmed:
        loaders["close"] = lambda: closing_form(closing_page(client, confirmed), confirmed)
    distributed = next((p for p in available if p["state"] == "jaotatud"), None)
    if distributed:
        loaders["period:jaotatud"] = lambda: find_form(client.get("yyriarvestus.perioodid_vaata", perioodi_id=distributed["id"]), REOPEN)
    base, found, changed = load_baseline(client), {}, False
    for key, load in loaders.items():
        try:
            found[key] = form = load()
        except UsageError as error:  # one form korto-cli cannot read must not stop the others
            print(f"{key}: not readable: {error}")
            changed = True
            continue
        entry = base.get(key) or {}
        same = entry.get("shape") == form.shape and entry.get("stamp") == client.stamp
        changed |= not same
        print(f"{key}: {'same' if same else 'changed' if entry else 'new'}"
              + (f" (stamp {entry.get('stamp')} -> {client.stamp})" if entry and entry.get("stamp") != client.stamp else ""))
        for line in differences(entry.get("shape", {}), form.shape) if entry else []:
            print(f"  {line}")
    print(f"not loadable in the current period states: {', '.join(k for k in KEYS if k not in loaders) or 'none'}")
    if not args.accept:
        return 1 if changed else 0
    if not client.stamp:
        raise UsageError("Korto's pages carry no version stamp: nothing accepted")
    stored = load_baseline(client, everything=True)
    stored.setdefault(str(client.union_id), {}).update({k: {"stamp": client.stamp, "shape": f.shape} for k, f in found.items()})
    try:
        with open(baseline_path() + ".tmp", "w", encoding="utf-8") as f:
            json.dump(stored, f, ensure_ascii=False, indent=1)
        os.replace(baseline_path() + ".tmp", baseline_path())
    except OSError as error:
        raise UsageError(f"cannot write the baseline {baseline_path()}: {error.strerror}") from None
    print(f"accepted {len(found)} forms into {baseline_path()}")
    return 0


def warnings(html):
    """Korto's own warnings on a page."""
    box = re.search(r'id="warning".*?</ul>', html, re.S)
    return [unescape(re.sub(r"<[^>]+>", "", item)).strip() for item in re.findall(r"<li>(.*?)</li>", box.group(0) if box else "", re.S)]


def describe(html):
    """What a page Korto answered with says and offers, for a human to finish in the web UI."""
    if not html:
        return "a redirect"
    title = re.search(r"<h1>(.*?)</h1>", html, re.S)
    parts = [unescape(re.sub(r"<[^>]+>", "", title.group(1))).strip()] if title else []
    parts += [f"warns: {w}" for w in warnings(html)]
    for form in forms(html):
        if any(c["name"] == "_csrf_token" for c in form):
            parts += [f"field {c['name']}={c['value']}" for c in form if c["name"] and c["type"] not in ("hidden",) + BUTTONS]
            parts += [f"button {_label(c)}" for c in form if c["type"] == "submit" and _label(c)]
    return "; ".join(parts)


def cmd_close(client, args):
    if not args.period:
        raise UsageError("name the period to close: --period YYYY-MM")
    period, available = resolve(client, args.period)
    if period["state"] != "kinnitatud":
        raise UsageError(f"{period['name']} is {period['state']}: only a confirmed period is closed")
    index = available.index(period)  # newest first
    if any(p["state"] != "suletud" for p in available[index + 1:]):
        raise UsageError(f"close {next(p['name'] for p in available[index + 1:] if p['state'] != 'suletud')} first")
    if index and available[index - 1]["state"] in ("kinnitatud", "suletud"):
        raise UsageError(f"{available[index - 1]['name']} is {available[index - 1]['state']}: closing {period['name']} "
                         "now is too late, its balances would not reach that period")
    try:
        dates = [next(d for d in map(_date, row) if d) for row in table(client, "payments", period)[1:] if any(map(_date, row))]
    except UsageError:  # no payments table: no payments
        dates = []
    page = closing_page(client, period)
    form = closing_form(page, period)
    for warning in warnings(page):
        print("Korto warns:", warning)

    def verify(answer):
        state = state_of(client, period)
        if state != "suletud":
            return f"the period is {state}; Korto answered: {describe(answer)}"
    changes = [("payments", "", f"{len(dates)}, newest {max(dates):%d.%m.%Y}" if dates else "none"),
               ("state", period["state"], "suletud")]
    return submit(client, args, "close", form, f"{period['name']}: close", changes, press(form, "Sulge"), verify)


def closing_page(client, period):
    """Korto's "close the period?" page. Sulge periood only opens it, as Jaota kulud opens its own:
    loading it left a confirmed period confirmed (checked live on 25.11.2026)."""
    form = find_form(client.get("yyriarvestus.perioodid_vaata", perioodi_id=period["id"]), "Sulge periood")
    button = next(c for c in form if c["type"] == "submit" and _label(c) == "Sulge periood")
    sent = dict(press(form, "Sulge periood"))
    if sent.get("screen") != "yyriarvestus.perioodid_sulge" or sent.get("action") or "confirm(" in button["onclick"]:
        raise UsageError("Korto's Sulge periood button no longer only opens the closing page: close it in the web UI")
    return client.post(sent.items())


def closing_form(page, period):
    """The closing page as korto-cli knows it: nothing to fill in, Sulge and Tagasi, for this period."""
    form = next((f for f in forms(page) if any(_label(c) == "Sulge" for c in f)), None)
    if form is None or value(form, "screen") != "yyriarvestus.perioodid_sulge" or value(form, "perioodi_id") != period["id"] \
            or sorted(_label(c) for c in form if c["type"] == "submit") != ["Sulge", "Tagasi"] \
            or " ".join(next(c["onclick"] for c in form if _label(c) == "Sulge").split()) != "this.form.elements['action'].value = 'sulge';" \
            or any(c["name"] and c["type"] not in ("hidden",) + BUTTONS for c in form):
        raise UsageError(f"Korto's closing page is not the one korto-cli knows: {describe(page)}. Close it in the web UI.")
    return form


def reading(client, args):
    """The open period, then as locate()."""
    return locate(client, writable(client, args.period), args.apartment, args.meter)[:6]


def locate(client, period, apartment, meter):
    """One apartment's readings form ('building': the building meters), its rows, the row of the meter,
    the rows that hold a reading, a function that reads the rows back by meter id, and the form loader."""
    apartment_id = "0"
    if apartment != "building":
        found = {}
        for r in readings(client.get("yyriarvestus.naidud", perioodi_id=period["id"]))["apartments"]:
            found.setdefault(r["apartment"], set()).add(r["apartment_id"])
        if len(found.get(apartment, ())) != 1:
            raise UsageError(f"no apartment {apartment}, or more than one with that number; "
                             f"there are {', '.join(found)} and 'building'")
        apartment_id = found[apartment].pop()

    def load():
        form = find_form(client.get("yyriarvestus.naidud_muuda", perioodi_id=period["id"], korteri_id=apartment_id,
                                    arvesti_id="0"), "naidud[0][arvesti_id]")
        return form, rows_of(form, "naidud")
    form, rows = load()
    hits = [i for i, r in rows.items() if meter is not None
            and (meter in (r.get("arvesti_id"), r.get("arvesti")) or r.get("arvesti", "").endswith(" " + meter))]
    if meter is not None and len(hits) != 1:
        raise UsageError(f"'{meter}' matches {len(hits)} of: " + ", ".join(r.get("arvesti", "?") for r in rows.values()))
    entered = {m.group(1) for n, v in payload(form) if v and (m := re.fullmatch(r"naidud\[(\d+)\]\[nait\]", n))}
    return period, form, rows, hits[0] if hits else None, entered, lambda: by_meter(load()[1]), load


def by_meter(rows):
    return {r.get("arvesti_id"): r for r in rows.values()}


def changed_others(rows, after, meter_id):
    """Meters other than `meter_id` whose reading changed: a loud failure instead of silent harm."""
    return [r.get("arvesti") for r in rows.values() if r.get("arvesti_id") != meter_id
            and (r.get("nait"), r.get("kuupaev")) != (after.get(r.get("arvesti_id"), {}).get("nait"),
                                                     after.get(r.get("arvesti_id"), {}).get("kuupaev"))]


def cmd_set_reading(client, args):
    period, form, rows, i, entered, read_back = reading(client, args)
    row, date = rows[i], in_period(args.date, period)
    if i in entered and row.get("kuupaev") != date:  # Korto keeps one reading per date and uses the latest
        raise UsageError(f"{row['arvesti']} already has {row.get('nait')} dated {row.get('kuupaev')}: pass --date "
                         f"{row.get('kuupaev')}, or delete it first with delete-reading")
    digits, new = int(row.get("tapsus") or 3), _number(args.value.replace(",", "."))
    if new is None:
        raise UsageError(f"not a number: {args.value}")
    start = _number(row.get("algnait", ""))
    end, quantity = f"{new:.{digits}f}", "" if start is None else f"{new - start:.{digits}f}"
    for j in rows:  # an unticked row loses its reading in Korto: only rows without one are left out
        fill(form, f"naidud[{j}][salvestada]", j == i or j in entered)
    for field, v in (("nait", end), ("kuupaev", date), ("kogus", quantity), ("muudetud", "t")):
        fill(form, f"naidud[{i}][{field}]", v)
    if args.forecast or row.get("prognoositud") == "t":  # tick, or untick an earlier estimate
        fill(form, f"naidud[{i}][prognoositud]", args.forecast)

    def verify(answer):
        after = read_back()
        now = after.get(row["arvesti_id"], {})
        if (_number(now.get("nait", "")), now.get("kuupaev")) != (float(end), date):
            return f"it shows {now.get('nait') or 'no reading'} dated {now.get('kuupaev') or '-'}"
        if changed_others(rows, after, row["arvesti_id"]):
            return "it also changed " + ", ".join(changed_others(rows, after, row["arvesti_id"]))
    changes = [("end", row.get("nait"), end), ("date", row.get("kuupaev"), date), ("quantity", row.get("kogus"), quantity),
               ("forecast", row.get("prognoositud"), "t" if args.forecast else "f")]
    return submit(client, args, readings_key(args), form, f"{period['name']}: {row['arvesti']}", changes, press(form, "Salvesta"), verify)


def cmd_delete_reading(client, args):
    period, form, rows, i, entered, read_back = reading(client, args)
    row = rows[i]
    if i not in entered:
        raise UsageError(f"{row['arvesti']} has no reading to delete")
    for j in rows:
        fill(form, f"naidud[{j}][salvestada]", j == i)

    def verify(answer):
        after = read_back()
        now = after.get(row["arvesti_id"], {})
        if (now.get("nait"), now.get("kuupaev")) == (row.get("nait"), row.get("kuupaev")):
            return "the reading is still there"
        if changed_others(rows, after, row["arvesti_id"]):
            return "it also changed " + ", ".join(changed_others(rows, after, row["arvesti_id"]))
        if now.get("nait"):
            print(f"Korto now uses an earlier entry: {now['nait']} dated {now['kuupaev']}")
    changes = [("end", row.get("nait"), ""), ("date", row.get("kuupaev"), "")]
    return submit(client, args, readings_key(args), form, f"{period['name']}: {row['arvesti']}", changes, press(form, "Kustuta märgitud"), verify)


def cmd_forecast(client, args):
    period = writable(client, args.period)
    html = client.get("yyriarvestus.naidud", perioodi_id=period["id"])
    current = readings(html)["apartments"]
    missing, before = [r for r in current if r["end"] is None], {r["meter_id"]: r for r in current if r["end"] is not None}
    if not missing:
        print(f"{period['name']}: no missing readings")
        return 0
    form, date = find_form(html, "kuude_arv"), in_period(args.date, period)
    fill(form, "kuude_arv", str(args.months), dialog="prognoosi")
    fill(form, "kuupaev", date, dialog="prognoosi")

    def verify(answer):
        after = readings(client.get("yyriarvestus.naidud", perioodi_id=period["id"]))["apartments"]
        changed = [f"{r['apartment']} {r['meter']}" for r in after if r["meter_id"] in before
                   and (r["end"], r["date"]) != (before[r["meter_id"]]["end"], before[r["meter_id"]]["date"])]
        if changed:
            return "it also changed " + ", ".join(changed)
        left = [r for r in after if r["end"] is None]
        if len(left) == len(missing):
            return "no reading was forecast"
        for r in left:
            print(f"no history to forecast from: {r['apartment']} {r['meter']}")
    changes = [(f"{r['apartment']} {r['meter']}", "", f"forecast, {date}") for r in missing]
    return submit(client, args, "forecast", form, f"{period['name']}: forecast from the last {args.months} months", changes,
                  press(form, "Prognoosi"), verify)


def cmd_distribute(client, args):
    period = writable(client, args.period)
    html = client.get("yyriarvestus.perioodid_lukusta", perioodi_id=period["id"])
    form, date = find_form(html, "jaotamise_kuupaev"), in_period(args.date, period)
    before = value(form, "jaotamise_kuupaev")
    fill(form, "jaotamise_kuupaev", date)
    for warning in warnings(html):
        print("Korto warns:", warning)

    def verify(answer):
        if state_of(client, period) == "avatud":
            return "the period is still open"
    return submit(client, args, "distribute", form, f"{period['name']}: distribute costs", [("distribution date", before, date)],
                  press(form, "Jaota kulud"), verify)


def cmd_reopen(client, args):
    period, _ = resolve(client, args.period, newest=True)
    if period["state"] in ("", "avatud", "kinnitatud", "suletud"):  # unknown, open, invoices confirmed, closed
        raise UsageError(f"{period['name']} is {period['state']}: only a distributed period is reopened here")
    # never the reopen screen itself in a dry run: it may act as soon as it is loaded
    form = find_form(client.get("yyriarvestus.perioodid_vaata", perioodi_id=period["id"]), REOPEN)

    def verify(answer):
        if state_of(client, period) != "avatud":
            return f"the period is {state_of(client, period)}"
    return submit(client, args, f"period:{period['state']}", form, f"{period['name']}: reopen", [("state", period["state"], "avatud")],
                  press(form, REOPEN), verify)


def cmd_set_residents(client, args):
    period, form, rows, _, entered, read_back, load = locate(client, writable(client, args.period), args.apartment, None)
    if not re.fullmatch(r"[0-9]{1,3}", args.count):
        raise UsageError(f"not a number of residents: {args.count}")
    args.count = str(int(args.count))
    before = value(form, "elanike_arv")
    for j in rows:  # the residents travel with the readings form: every reading in it is kept as it is
        fill(form, f"naidud[{j}][salvestada]", j in entered)
    fill(form, "elanike_arv", args.count)
    fill(form, "korter_muudetud", "t")

    def verify(answer):
        now = value(load()[0], "elanike_arv")
        if now != args.count:
            return f"it shows {now} residents"
        if changed_others(rows, read_back(), None):
            return "it also changed " + ", ".join(changed_others(rows, read_back(), None))
    return submit(client, args, "readings:apartment", form, f"{period['name']}: apartment {args.apartment}", [("residents", before, args.count)],
                  press(form, "Salvesta"), verify)


def cmd_set_price(client, args):
    period = writable(client, args.period)
    listing = client.get("yyriarvestus.kululiigid", perioodi_id=period["id"])
    hits = [cost_id for code, cost_id in cost_types(listing) if code == args.code]
    if len(hits) != 1:
        raise UsageError(f"{len(hits)} cost types {args.code}; there are {', '.join(code for code, _ in cost_types(listing))}")

    def load():
        return find_form(client.get("yyriarvestus.kululiigid_form", perioodi_id=period["id"], kululiigi_id=hits[0],
                                    **{"return": ""}), "kululiik[fikseeritud_hind]")
    form = load()
    if value(form, "kululiik[hinna_reegel]") != "H":
        raise UsageError(f"{args.code} is not priced by a fixed unit price")
    new = _money(args.price.replace(",", ".")) if re.fullmatch(r"\d+([.,]\d+)?", args.price) else None
    if new is None or new <= 0:
        raise UsageError(f"not a price: {args.price}")
    before, digits = value(form, "kululiik[fikseeritud_hind]"), int(value(form, "kululiik[hinna_tapsus]") or 3)
    if new != new.quantize(Decimal(1).scaleb(-digits)):
        raise UsageError(f"{args.price} has more decimals than {args.code} keeps ({digits})")
    price, kept = f"{new:.{digits}f}", {n: v for n, v in payload(form) if n not in ("_csrf_token", "kululiik[fikseeritud_hind]")}
    fill(form, "kululiik[fikseeritud_hind]", price)

    def verify(answer):
        again = load()
        now = value(again, "kululiik[fikseeritud_hind]")
        if now != price:
            return f"it shows {now}"
        moved = [n for n, v in {n: v for n, v in payload(again) if n in kept}.items() if kept[n] != v]
        if moved or set(kept) - {n for n, _ in payload(again)}:
            return "it also changed " + ", ".join(moved or sorted(set(kept) - {n for n, _ in payload(again)}))
    return submit(client, args, f"cost-type:{args.code}", form, f"{period['name']}: {args.code} {value(form, 'kululiik[nimetus]')}",
                  [("price without VAT", before, price)], press(form, "Salvesta"), verify)


def cmd_new_period(client, args):
    available = periods(client.get("yyriarvestus.naidud"))
    if not available:
        raise UsageError("Korto lists no periods: make the first one in the web UI")
    if any(p["state"] == "avatud" for p in available):
        raise UsageError("a period is open: distribute it before opening the next one")
    form = find_form(client.get("yyriarvestus.perioodid_form", perioodi_id="0"), "periood[aasta]")
    year, month = int(value(form, "periood[aasta]")), int(value(form, "periood[kuu]"))
    name, end = f"{year} {MONTHS[month - 1]}", dt.date(year, month, calendar.monthrange(year, month)[1])
    if any(p["name"] == name for p in available):
        raise UsageError(f"{name} exists already")
    after = period_end(available[0]["name"]) + dt.timedelta(days=1)
    due = dt.date(year + month // 12, month % 12 + 1, 14)
    if (year, month) != (after.year, after.month) or value(form, "periood[arvepaev]") != f"{end:%d.%m.%Y}" \
            or value(form, "periood[maksepaev]") != f"{due:%d.%m.%Y}":
        raise UsageError(f"Korto suggests {name}, invoices dated {value(form, 'periood[arvepaev]')} and due "
                         f"{value(form, 'periood[maksepaev]')}: not the month after {available[0]['name']}, "
                         f"dated {end:%d.%m.%Y} and due {due:%d.%m.%Y}. Make it in the web UI.")
    for p in available:
        if p["state"] == "kinnitatud":
            print(f"note: {p['name']} is confirmed but not closed: close it before {name}'s invoices are confirmed")

    def verify(answer):
        if not any(p["name"] == name and p["state"] == "avatud" for p in periods(client.get("yyriarvestus.naidud"))):
            return f"no open period {name}"
    changes = [(n, "", value(form, n)) for n in dict.fromkeys(n for n, _ in payload(form) if n != "_csrf_token")]
    return submit(client, args, "new-period", form, f"new period: {name}", changes, press(form, "Salvesta"), verify)


def _money(text):
    try:
        return Decimal(text.replace(" ", "")) if text else None
    except InvalidOperation:
        return None


def _cents(amount):
    return amount.quantize(CENT, ROUND_HALF_UP)


def recompute(form, given):
    """Redo the sums Korto's page script redoes as you type: for the rows you changed, the fields you did not set."""
    touched = {n[5:].split("]")[0] for n in given if n.startswith("read[")}
    for i, r in rows_of(form, "read").items():
        if i not in touched:
            continue
        q, price, net, vat = (_money(r.get(k, "")) for k in ("kogus", "hind", "summa_kmta", "km_protsent"))
        if f"read[{i}][summa_kmta]" not in given and q is not None and price is not None:
            net = _cents(q * price)
            fill(form, f"read[{i}][summa_kmta]", str(net))
        elif f"read[{i}][hind]" not in given and q and net is not None:  # a new net amount: the unit price follows
            fill(form, f"read[{i}][hind]", str((net / q).quantize(Decimal(1).scaleb(-int(r.get("hinna_tapsus") or 3)), ROUND_HALF_UP)))
        if f"read[{i}][summa_kmga]" not in given and net is not None:
            fill(form, f"read[{i}][summa_kmga]", str(_cents(net * (1 + (vat or 0) / 100))))
    if touched and "tehing[tasuda]" not in given:
        fill(form, "tehing[tasuda]", str(sum((_money(r.get("summa_kmga", "")) or 0 for r in rows_of(form, "read").values()
                                              if r.get("salvestada") == "t"), Decimal(0))))


def expense_lines(form, given):
    """Each saved row's arithmetic, and what does not add up: a stale total is how wrong invoices go out."""
    lines, problems, total = [], [], Decimal(0)
    for i, r in rows_of(form, "read").items():
        if r.get("salvestada") != "t":
            continue
        q, price, net, vat, gross = (_money(r.get(k, "")) for k in ("kogus", "hind", "summa_kmta", "km_protsent", "summa_kmga"))
        lines.append((f"read[{i}]", "", f"{r.get('teenus', '')}: {r.get('kogus')} x {r.get('hind')} = {r.get('summa_kmta')}"
                      f" + {r.get('km_protsent')}% = {r.get('summa_kmga')}"))
        if net is None or gross is None:
            problems.append(f"read[{i}]: summa_kmta and summa_kmga are required")
            continue
        typed = {k for k in ("kogus", "hind", "summa_kmta") if f"read[{i}][{k}]" in given}
        if {"summa_kmta", "hind"} <= typed and q is not None and price is not None and _cents(q * price) != net:
            problems.append(f"read[{i}]: kogus x hind is {_cents(q * price)}, summa_kmta is {net}")
        if any(n.startswith(f"read[{i}][") for n in given) and abs(net * (1 + (vat or 0) / 100) - gross) > CENT:
            problems.append(f"read[{i}]: summa_kmta with VAT is {_cents(net * (1 + (vat or 0) / 100))}, summa_kmga is {gross}")
        total += gross
    due = _money(value(form, "tehing[tasuda]") or "")
    if due is None or abs(due - total) > 5 * CENT:  # invoices round their total; more than that is a stale field
        problems.append(f"tehing[tasuda] is {due}, the rows add up to {total}")
    return lines, problems


def prefilled(client, period_id, like):
    """The new-expense form filled by Korto from an earlier invoice (`like`: id or partner text; None: the first),
    as picking it in the web UI does: Korto fills the form and saves nothing. Returns the form and the invoice's text."""
    form = find_form(client.get("yyriarvestus.tehingud_form", perioodi_id=period_id, tehingu_id="0"), "vana_tehingu_id")
    options = [(v, " ".join(text.split())) for v, text in next(c for c in form if c["name"] == "vana_tehingu_id")["options"] if v]
    chosen = (options[:1] if like is None else [o for o in options if o[0] == like]
              or [o for o in options if like.lower() in o[1].lower()])
    if len(chosen) != 1:
        raise UsageError("--like must match one earlier invoice: " + "; ".join(f"{v} {text}" for v, text in options))
    fill(form, "vana_tehingu_id", chosen[0][0])
    fill(form, "action", "vali_tehing")
    filled = find_form(client.post(payload(form)), "vana_tehingu_id")
    if not value(filled, "tehing[nimi]") or not chosen[0][1].startswith(value(filled, "tehing[nimi]")):
        raise UsageError(f"Korto did not fill the form from {chosen[0][1]}")
    return filled, chosen[0][1]


def cmd_add_expense(client, args):
    period = writable(client, args.period)
    (form, template), changes = prefilled(client, period["id"], args.like), []
    for item in args.fields:
        name, sep, new = item.partition("=")
        if not sep:
            raise UsageError(f"not FIELD=VALUE: {item}")
        changes.append((name, value(form, name), new))
        fill(form, name, new)
    given = [name for name, _, _ in changes]
    recompute(form, given)
    lines, problems = expense_lines(form, given)
    lines = [(n, "", value(form, n)) for n in HEADER if n not in given] + lines
    lines += [] if "tehing[tasuda]" in given else [("tehing[tasuda]", "", value(form, "tehing[tasuda]"))]
    due, number = _cents(_money(value(form, "tehing[tasuda]") or "") or Decimal(0)), value(form, "tehing[arve_nr]")
    partner = value(form, "tehing[nimi]")
    if any(partner in row and (number in row if number else str(due) in row)
           for row in grid(client.get("yyriarvestus.tehingud", perioodi_id=period["id"]))):
        problems.append(f"{partner} {number or str(due)} is already in {period['name']}")

    def verify(answer):
        listed = grid(client.get("yyriarvestus.tehingud", perioodi_id=period["id"]))
        if not any(str(due) in row and (not number or number in row) for row in listed):
            return f"no expense {number or ''} of {due} in the list"
    return submit(client, args, "expense", form, f"{period['name']}: new expense like {template}", changes + lines,
                  press(form, "Salvesta"), verify, problems)


COMMANDS = {"periods": cmd_periods, "readings": cmd_readings, "check": cmd_check, "summary": cmd_summary,
            "table": cmd_table, "history": cmd_history,
            "set-reading": cmd_set_reading, "delete-reading": cmd_delete_reading, "forecast": cmd_forecast,
            "distribute": cmd_distribute, "reopen": cmd_reopen, "add-expense": cmd_add_expense,
            "set-residents": cmd_set_residents, "set-price": cmd_set_price, "new-period": cmd_new_period,
            "close": cmd_close, "selftest": cmd_selftest}
WRITES = {"set-reading": "enter or correct a reading", "delete-reading": "delete a reading",
          "forecast": "forecast the missing readings", "distribute": "distribute costs and make the invoices",
          "reopen": "reopen a distributed period", "add-expense": "enter an expense invoice like an earlier one",
          "set-residents": "set an apartment's number of residents", "set-price": "set a cost type's fixed unit price",
          "new-period": "open the next period with Korto's defaults", "close": "close a confirmed period"}


def main(argv=None, client=None):
    parser = argparse.ArgumentParser(prog="korto", description=__doc__.splitlines()[0])
    parser.add_argument("--env", default=os.environ.get("KORTO_ENV"),
                        help="file with KORTO_PHPSESSID and KORTO_UNION_ID (default: $KORTO_ENV)")
    parser.add_argument("--version", action="version", version=f"korto-cli {__version__} — unofficial, "
                        "not affiliated with or endorsed by OÜ Tarkvaralabor")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (("periods", "period ids and states"), ("readings", "meter readings of a period"),
                       ("check", "find mistakes before distributing costs"), ("summary", "distribution and invoices"),
                       ("table", "one of Korto's tables"), ("history", "every reading of one meter")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--json", action="store_true", help="machine-readable output")
        if name != "periods":
            p.add_argument("--period", help="period id or YYYY-MM (default: the open period)")
        if name == "table":
            p.add_argument("name", choices=TABLES)
        if name == "history":
            p.add_argument("apartment", help="apartment number, or 'building'")
            p.add_argument("meter", help="meter name or id, as `korto readings` shows it")
        if name == "check":
            p.add_argument("--ignore", nargs="*", default=[], metavar="APT:METER",
                           help="meters to skip entirely, e.g. those known to have no reading")
    p = sub.add_parser("selftest", help="compare Korto's forms with your baseline (read only)")
    p.add_argument("--accept", action="store_true", help="store the forms read now as the new baseline")
    for name, text in WRITES.items():
        p = sub.add_parser(name, help=f"{text} (a dry run unless --apply)")
        p.add_argument("--period", required=name == "close",
                       help="period id or YYYY-MM" + ("" if name == "close" else " (default: the open period)"))
        p.add_argument("--apply", action="store_true", help="send it to Korto; without it, only show what would be sent")
        if name.endswith("-reading") or name == "set-residents":
            p.add_argument("apartment", help="apartment number" + ("" if name == "set-residents" else ", or 'building'"))
        if name.endswith("-reading"):
            p.add_argument("meter", help="meter name or id, as `korto readings` shows it")
        if name == "set-reading":
            p.add_argument("value", help="the meter reading")
            p.add_argument("--forecast", action="store_true", help="mark it as an estimate, not a reading")
        if name in ("set-reading", "forecast", "distribute"):
            p.add_argument("--date", help="dd.mm.yyyy in the period (default: the period end)")
        if name == "forecast":
            p.add_argument("--months", type=int, default=6, choices=range(1, 13), metavar="1-12",
                           help="average of how many earlier months (default: 6)")
        if name == "set-residents":
            p.add_argument("count", help="number of residents")
        if name == "set-price":
            p.add_argument("code", help="cost type code, e.g. 041")
            p.add_argument("price", help="unit price without VAT")
        if name == "add-expense":
            p.add_argument("--like", required=True, help="the earlier invoice to start from: its id or partner name")
            p.add_argument("fields", nargs="*", metavar="FIELD=VALUE", help="Korto's form fields to change")
    args = parser.parse_args(argv)
    if client is None:
        try:
            env = load_env(args.env)
        except OSError as error:
            parser.error(f"cannot read the --env file: {error.strerror}")
        session, union = env.get("KORTO_PHPSESSID", "").strip(), env.get("KORTO_UNION_ID", "").strip()
        if not re.fullmatch(r"[A-Za-z0-9,-]+", session) or not union.isdigit():
            parser.error("set KORTO_PHPSESSID (the session cookie) and KORTO_UNION_ID (a number), "
                         "in the environment or an --env file")
        if env.get("KORTO_BASELINE"):
            os.environ.setdefault("KORTO_BASELINE", env["KORTO_BASELINE"])
        client = Client(session, union)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # names and JSON stay intact on any console
    try:
        return COMMANDS[args.command](client, args) or 0
    except SessionExpired as error:
        print(error, file=sys.stderr)
        return 3
    except UsageError as error:
        print(error, file=sys.stderr)
        return 2
    except (urllib.error.URLError, OSError) as error:
        print(f"Korto request failed: {error}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
