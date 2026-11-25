#!/usr/bin/env python3
"""korto-cli — unofficial command-line companion for Korto Pro.

Not affiliated with or endorsed by OÜ Tarkvaralabor. It uses your own Korto session to
read the same pages the web UI shows you, at human pace. See README.md, "Terms, law and
data", before using it.
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

__version__ = "0.1.0"
BASE = "https://pro.korto.ee/index.php"
USER_AGENT = f"korto-cli/{__version__} (+https://github.com/runnel/korto-cli)"
MIN_INTERVAL = 1.0  # seconds between requests: human pace, never parallel
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
        raise SessionExpired("Korto did not return a logged-in page: the session has expired. "
                             "Copy PHPSESSID from a logged-in browser again.")
    return html


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):  # Korto redirects an expired session to its login page
        return None                     # never follow: the cookie must not travel anywhere else


_opener = urllib.request.build_opener(_NoRedirect)


class Client:
    def __init__(self, session_id, union_id):
        self.session_id, self.union_id, self._last = session_id, union_id, 0.0

    def get(self, screen, **params):
        query = urllib.parse.urlencode({"yhistu_id": self.union_id, **params, "screen": screen})
        time.sleep(max(0.0, self._last + MIN_INTERVAL - time.monotonic()))
        request = urllib.request.Request(f"{BASE}?{query}", headers={
            "Cookie": f"PHPSESSID={self.session_id}", "User-Agent": USER_AGENT})
        try:
            with _opener.open(request, timeout=30) as response:
                return ensure_logged_in(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as error:
            if 300 <= error.code < 400:
                raise SessionExpired("Korto redirected to its login page: the session has expired. "
                                     "Copy PHPSESSID from a logged-in browser again.") from None
            raise
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
        return float(text.replace(" ", "").replace(",", ".")) if text else None
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
        apartments.append({
            "apartment": row[k - 2]["text"],
            "meter": text[len(address):].strip() if address and text.startswith(address) else text,
            "meter_id": next(v for n, v in row[k]["inputs"] if n.startswith(METER_INPUT)),
            "start": _number(row[k + 1]["text"]), "end": _number(row[k + 2]["text"]), "date": row[k + 3]["text"],
            "quantity": _number(row[k + 4]["text"]), "forecast": "Prognoositud" in row[k + 6]["titles"],
            "unit": row[k + 7]["text"], "entered_by": entered.group(1) if entered else None,
            "via_link": bool(entered and entered.group(2)), "entered_at": entered.group(3) if entered else None})
    if any(len(r) > 3 for r in rows) and not apartments:
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


def resolve(client, wanted):
    """Period for an id or YYYY-MM; default: the open period, else the newest."""
    available = periods(client.get("yyriarvestus.naidud"))
    if not wanted:
        match = next((p for p in available if p["state"] == "avatud"), available[0] if available else None)
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


COMMANDS = {"periods": cmd_periods, "readings": cmd_readings, "check": cmd_check, "summary": cmd_summary}


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
    args = parser.parse_args(argv)
    if client is None:
        env = load_env(args.env)
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
        print(f"could not reach Korto: {error}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
