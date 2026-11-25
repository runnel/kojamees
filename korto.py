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
from html import unescape
from html.parser import HTMLParser

__version__ = "0.2.0"
BASE = "https://pro.korto.ee/index.php"
USER_AGENT = f"korto-cli/{__version__} (+https://github.com/runnel/korto-cli)"
MIN_INTERVAL = 1.0  # seconds between requests: human pace, never parallel
EXPIRED = "Korto did not return a logged-in page: the session has expired. Copy PHPSESSID from a logged-in browser again."
BUTTONS = ("submit", "button", "reset", "image")
REOPEN = "Taasava periood"
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
        self.session_id, self.union_id, self._last = session_id, union_id, 0.0

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
            headers["Origin"] = "https://pro.korto.ee"
        try:
            with _opener.open(urllib.request.Request(url, data, headers), timeout=30) as response:
                return ensure_logged_in(response.read().decode("utf-8", "replace"))
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
        self.tables, self._open, self._cell = {}, [], None  # _open: (rows, outer cell) per table

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
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
        if tag in ("td", "th", "tr"):
            self._close_cell()
        elif tag == "table" and self._open:
            self._close_cell()
            self._cell = self._open.pop()[1]  # back to the cell that contained a nested table
            if self._cell is not None:
                self._cell["text"] += " "

    def handle_data(self, data):
        if self._cell is not None:
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
            self.forms.append([])
        elif self._inside and tag in ("input", "select", "textarea", "button"):
            kind = a.get("type", "submit" if tag == "button" else "text" if tag == "input" else tag).lower()
            control = {"name": a.get("name", ""), "type": kind, "value": a.get("value", ""), "options": [],
                       "label": a.get("value", "") if tag == "input" and kind in BUTTONS else "",
                       "onclick": a.get("onclick", ""), "action": a.get("data-action", ""),
                       "dialog": next((d for d in reversed(self._dialogs) if d), ""),
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
    return parser.forms


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


def fill(form, name, new):
    """Type into a field (str) or tick a checkbox (bool), as a user would."""
    matches = [c for c in form if c["name"] == name and c["type"] not in BUTTONS]
    if not matches:
        raise UsageError(f"Korto's form has no field {name}")
    toggles = [c for c in matches if c["type"] in ("checkbox", "radio")]
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
    if button["action"]:  # a dialog button: Korto disables the other dialogs and posts its data-action
        for c in form:
            c["on"] = c["on"] and c["dialog"] in ("", button["dialog"])
        fill(form, "action", button["action"])
    pairs = re.findall(r"elements\['([^']+)'\]\.value = '([^']*)'", button["onclick"])
    for inner in re.findall(r"setFormValues\(this\.form, \{(.*?)\}\)", button["onclick"]):
        pairs += re.findall(r"(\w+): '([^']*)'", inner)
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


def submit(client, args, title, changes, fields, verify, problems=()):
    """Show the change; send it only with --apply, then read it back from Korto."""
    print(title)
    show([{"field": f, "now": now, "after": after} for f, now, after in changes], ["field", "now", "after"], False)
    for problem in problems:
        print(f"problem: {problem}")
    if problems:
        return 2
    if not args.apply:
        print("\ndry run: nothing was sent to Korto. Add --apply to send it.")
        return 0
    client.post(fields)
    problem = verify()
    if problem:
        print(f"Korto did not take the change: {problem}", file=sys.stderr)
        return 1
    print("\ndone, and read back from Korto.")
    return 0


def reading(client, args):
    """The open period, the readings form of one apartment ('building': the building meters), its rows,
    the row of the meter, and a function that reads that row back."""
    period, apartment_id = writable(client, args.period), "0"
    if args.apartment != "building":
        found = {r["apartment"]: r["apartment_id"]
                 for r in readings(client.get("yyriarvestus.naidud", perioodi_id=period["id"]))["apartments"]}
        if args.apartment not in found:
            raise UsageError(f"no apartment {args.apartment}; there are {', '.join(found)} and 'building'")
        apartment_id = found[args.apartment]

    def load():
        form = find_form(client.get("yyriarvestus.naidud_muuda", perioodi_id=period["id"], korteri_id=apartment_id,
                                    arvesti_id="0"), "naidud[0][arvesti_id]")
        return form, rows_of(form, "naidud")
    form, rows = load()
    hits = [i for i, r in rows.items()
            if args.meter in (r.get("arvesti_id"), r.get("arvesti")) or r.get("arvesti", "").endswith(" " + args.meter)]
    if len(hits) != 1:
        raise UsageError(f"'{args.meter}' matches {len(hits)} of: " + ", ".join(r.get("arvesti", "?") for r in rows.values()))
    meter_id = rows[hits[0]].get("arvesti_id")
    return period, form, rows, hits[0], lambda: next((r for r in load()[1].values() if r.get("arvesti_id") == meter_id), {})


def cmd_set_reading(client, args):
    period, form, rows, i, read_back = reading(client, args)
    row, date = rows[i], in_period(args.date, period)
    if row.get("nait") and row.get("kuupaev") != date:  # Korto keeps one reading per date and uses the latest
        raise UsageError(f"{row['arvesti']} already has {row['nait']} dated {row['kuupaev']}: pass --date "
                         f"{row['kuupaev']}, or delete it first with delete-reading")
    digits, new = int(row.get("tapsus") or 3), _number(args.value.replace(",", "."))
    if new is None:
        raise UsageError(f"not a number: {args.value}")
    start = _number(row.get("algnait", ""))
    end, quantity = f"{new:.{digits}f}", "" if start is None else f"{new - start:.{digits}f}"
    for j, r in rows.items():  # an unticked row loses its reading in Korto: only rows without one are left out
        fill(form, f"naidud[{j}][salvestada]", j == i or bool(r.get("nait")))
    for field, v in (("nait", end), ("kuupaev", date), ("kogus", quantity), ("muudetud", "t")):
        fill(form, f"naidud[{i}][{field}]", v)
    fill(form, f"naidud[{i}][prognoositud]", args.forecast)

    def verify():
        now = read_back()
        if (_number(now.get("nait", "")), now.get("kuupaev")) != (float(end), date):
            return f"it shows {now.get('nait') or 'no reading'} dated {now.get('kuupaev') or '-'}"
    changes = [("end", row.get("nait"), end), ("date", row.get("kuupaev"), date), ("quantity", row.get("kogus"), quantity),
               ("forecast", row.get("prognoositud"), "t" if args.forecast else "f")]
    return submit(client, args, f"{period['name']}: {row['arvesti']}", changes, press(form, "Salvesta"), verify)


def cmd_delete_reading(client, args):
    period, form, rows, i, read_back = reading(client, args)
    row = rows[i]
    if not row.get("nait"):
        raise UsageError(f"{row['arvesti']} has no reading to delete")
    for j in rows:
        fill(form, f"naidud[{j}][salvestada]", j == i)

    def verify():
        now = read_back()
        if (now.get("nait"), now.get("kuupaev")) == (row["nait"], row["kuupaev"]):
            return "the reading is still there"
        if now.get("nait"):
            print(f"Korto now uses an earlier entry: {now['nait']} dated {now['kuupaev']}")
    changes = [("end", row["nait"], ""), ("date", row["kuupaev"], "")]
    return submit(client, args, f"{period['name']}: {row['arvesti']}", changes, press(form, "Kustuta märgitud"), verify)


def cmd_forecast(client, args):
    period = writable(client, args.period)
    html = client.get("yyriarvestus.naidud", perioodi_id=period["id"])
    missing = [r for r in readings(html)["apartments"] if r["end"] is None]
    if not missing:
        print(f"{period['name']}: no missing readings")
        return 0
    form, date = find_form(html, "kuude_arv"), in_period(args.date, period)
    fill(form, "kuude_arv", str(args.months))
    fill(form, "kuupaev", date)

    def verify():
        left = [r for r in readings(client.get("yyriarvestus.naidud", perioodi_id=period["id"]))["apartments"]
                if r["end"] is None]
        if len(left) == len(missing):
            return "no reading was forecast"
        for r in left:
            print(f"no history to forecast from: {r['apartment']} {r['meter']}")
    changes = [(f"{r['apartment']} {r['meter']}", "", f"forecast, {date}") for r in missing]
    return submit(client, args, f"{period['name']}: forecast from the last {args.months} months", changes,
                  press(form, "Prognoosi"), verify)


def cmd_distribute(client, args):
    period = writable(client, args.period)
    html = client.get("yyriarvestus.perioodid_lukusta", perioodi_id=period["id"])
    form, date = find_form(html, "jaotamise_kuupaev"), in_period(args.date, period)
    before = value(form, "jaotamise_kuupaev")
    fill(form, "jaotamise_kuupaev", date)
    warning = re.search(r'id="warning".*?</ul>', html, re.S)
    for item in re.findall(r"<li>(.*?)</li>", warning.group(0) if warning else "", re.S):
        print("Korto warns:", unescape(re.sub(r"<[^>]+>", "", item)).strip())

    def verify():
        if state_of(client, period) == "avatud":
            return "the period is still open"
    return submit(client, args, f"{period['name']}: distribute costs", [("distribution date", before, date)],
                  press(form, "Jaota kulud"), verify)


def cmd_reopen(client, args):
    period, _ = resolve(client, args.period, newest=True)
    if period["state"] == "avatud":
        raise UsageError(f"{period['name']} is already open")
    # never the reopen screen itself in a dry run: it may act as soon as it is loaded
    form = find_form(client.get("yyriarvestus.perioodid_vaata", perioodi_id=period["id"]), REOPEN)

    def verify():
        if state_of(client, period) != "avatud":
            return f"the period is {state_of(client, period)}"
    return submit(client, args, f"{period['name']}: reopen", [("state", period["state"], "avatud")],
                  press(form, REOPEN), verify)


def recompute(form, given):
    """Redo the sums Korto's page script redoes as you type: for the rows you changed, the fields you did not set."""
    touched = {n[5:].split("]")[0] for n in given if n.startswith("read[")}
    for i, r in rows_of(form, "read").items():
        q, price, vat = (_number(r.get(k, "")) for k in ("kogus", "hind", "km_protsent"))
        if i in touched and f"read[{i}][summa_kmta]" not in given and q is not None and price is not None:
            fill(form, f"read[{i}][summa_kmta]", f"{q * price:.2f}")
        net = _number(value(form, f"read[{i}][summa_kmta]") or "")
        if i in touched and f"read[{i}][summa_kmga]" not in given and net is not None:
            fill(form, f"read[{i}][summa_kmga]", f"{net * (1 + (vat or 0) / 100):.2f}")
    if touched and "tehing[tasuda]" not in given:
        fill(form, "tehing[tasuda]", f"{sum(_number(r.get('summa_kmga', '')) or 0 for r in rows_of(form, 'read').values() if r.get('salvestada') == 't'):.2f}")


def expense_lines(form):
    """Each saved row's arithmetic, and what does not add up: a stale total is how wrong invoices go out."""
    lines, problems, total = [], [], 0.0
    for i, r in rows_of(form, "read").items():
        if r.get("salvestada") != "t":
            continue
        q, price, net, vat, gross = (_number(r.get(k, "")) for k in ("kogus", "hind", "summa_kmta", "km_protsent", "summa_kmga"))
        lines.append((f"read[{i}]", "", f"{r.get('teenus', '')}: {r.get('kogus')} x {r.get('hind')} = {r.get('summa_kmta')}"
                      f" + {r.get('km_protsent')}% = {r.get('summa_kmga')}"))
        if net is None or gross is None:
            problems.append(f"read[{i}]: summa_kmta and summa_kmga are required")
            continue
        if q is not None and price is not None and abs(q * price - net) > 0.01:
            problems.append(f"read[{i}]: kogus x hind is {q * price:.2f}, summa_kmta is {net:.2f}")
        if abs(net * (1 + (vat or 0) / 100) - gross) > 0.01:
            problems.append(f"read[{i}]: summa_kmta with VAT is {net * (1 + (vat or 0) / 100):.2f}, summa_kmga is {gross:.2f}")
        total += gross
    due = _number(value(form, "tehing[tasuda]") or "")
    if due is None or abs(due - total) > 0.05:
        problems.append(f"tehing[tasuda] is {due}, the rows add up to {total:.2f}")
    return lines, problems


def cmd_add_expense(client, args):
    period = writable(client, args.period)
    form = find_form(client.get("yyriarvestus.tehingud_form", perioodi_id=period["id"], tehingu_id="0"), "vana_tehingu_id")
    options = [(v, " ".join(text.split())) for v, text in next(c for c in form if c["name"] == "vana_tehingu_id")["options"] if v]
    like = [v for v, text in options if args.like == v or args.like.lower() in text.lower()]
    if len(like) != 1:
        raise UsageError("--like must match one earlier invoice: " + "; ".join(f"{v} {text}" for v, text in options))
    fill(form, "vana_tehingu_id", like[0])
    fill(form, "action", "vali_tehing")  # what picking it in the web UI does: Korto fills the form, saves nothing
    form, changes = find_form(client.post(payload(form)), "vana_tehingu_id"), []
    for item in args.fields:
        name, sep, new = item.partition("=")
        if not sep:
            raise UsageError(f"not FIELD=VALUE: {item}")
        changes.append((name, value(form, name), new))
        fill(form, name, new)
    recompute(form, [name for name, _, _ in changes])
    lines, problems = expense_lines(form)
    given = [name for name, _, _ in changes]
    lines = [(n, "", value(form, n)) for n in HEADER if n not in given] + lines
    lines += [] if "tehing[tasuda]" in given else [("tehing[tasuda]", "", value(form, "tehing[tasuda]"))]
    due, number = _number(value(form, "tehing[tasuda]") or "") or 0.0, value(form, "tehing[arve_nr]")

    def verify():
        listed = grid(client.get("yyriarvestus.tehingud", perioodi_id=period["id"]))
        if not any(f"{due:.2f}" in row and (not number or number in row) for row in listed):
            return f"no expense {number or ''} of {due:.2f} in the list"
    return submit(client, args, f"{period['name']}: new expense like {dict(options)[like[0]]}",
                  changes + lines, press(form, "Salvesta"), verify, problems)


COMMANDS = {"periods": cmd_periods, "readings": cmd_readings, "check": cmd_check, "summary": cmd_summary,
            "set-reading": cmd_set_reading, "delete-reading": cmd_delete_reading, "forecast": cmd_forecast,
            "distribute": cmd_distribute, "reopen": cmd_reopen, "add-expense": cmd_add_expense}
WRITES = {"set-reading": "enter or correct a reading", "delete-reading": "delete a reading",
          "forecast": "forecast the missing readings", "distribute": "distribute costs and make the invoices",
          "reopen": "reopen a distributed period", "add-expense": "enter an expense invoice like an earlier one"}


def main(argv=None, client=None):
    parser = argparse.ArgumentParser(prog="korto", description=__doc__.splitlines()[0])
    parser.add_argument("--env", default=os.environ.get("KORTO_ENV"),
                        help="file with KORTO_PHPSESSID and KORTO_UNION_ID (default: $KORTO_ENV)")
    parser.add_argument("--version", action="version", version=f"korto-cli {__version__} — unofficial, "
                        "not affiliated with or endorsed by OÜ Tarkvaralabor")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (("periods", "period ids and states"), ("readings", "meter readings of a period"),
                       ("check", "find mistakes before distributing costs"), ("summary", "distribution and invoices")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--json", action="store_true", help="machine-readable output")
        if name != "periods":
            p.add_argument("--period", help="period id or YYYY-MM (default: the open period)")
        if name == "check":
            p.add_argument("--ignore", nargs="*", default=[], metavar="APT:METER",
                           help="meters to skip entirely, e.g. those known to have no reading")
    for name, text in WRITES.items():
        p = sub.add_parser(name, help=f"{text} (a dry run unless --apply)")
        p.add_argument("--period", help="period id or YYYY-MM (default: the open period)")
        p.add_argument("--apply", action="store_true", help="send it to Korto; without it, only show what would be sent")
        if name.endswith("-reading"):
            p.add_argument("apartment", help="apartment number, or 'building' for the building meters")
            p.add_argument("meter", help="meter name or id, as `korto readings` shows it")
        if name == "set-reading":
            p.add_argument("value", help="the meter reading")
            p.add_argument("--forecast", action="store_true", help="mark it as an estimate, not a reading")
        if name in ("set-reading", "forecast", "distribute"):
            p.add_argument("--date", help="dd.mm.yyyy in the period (default: the period end)")
        if name == "forecast":
            p.add_argument("--months", type=int, default=6, choices=range(1, 13), metavar="1-12",
                           help="average of how many earlier months (default: 6)")
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
