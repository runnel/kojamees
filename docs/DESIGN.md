# Design

> korto-cli — unofficial command-line companion for Korto Pro. Not affiliated with or endorsed by OÜ Tarkvaralabor.

Status: 0.1 implements the read and check phase. The write phase (0.2) is designed below, not built.

## Goal

Let an apartment association administrator do the recurring monthly round in Korto Pro — check readings, enter expense invoices, forecast missing readings, distribute costs — with plain HTTP requests instead of clicking through the web UI, on their own account, at human pace, with nothing they could not do by hand.

## Principles

1. **Minimal code.** One file, Python 3.10+ standard library, no dependencies, no build step. 0.1 is about 300 lines of code; 0.2 should add no more than 200.
2. **Korto only, no building knowledge.** The tool knows Korto's pages and forms. Suppliers, apartments, exceptions, proration and prices live in the administrator's own notes and arrive as arguments.
3. **Inside Korto's terms and the law.** See "Terms, law and data" in the README. Sequential requests at least a second apart, honest User-Agent, the user's own session, no crawling, no Korto content or personal data in the repository.
4. **Fail loudly.** A page layout the parser does not understand is an error, never "no findings". A redirect means the session expired and is never followed, so the cookie cannot travel elsewhere.
5. **Writes are dry runs by default** (0.2). `--apply` is required, and every applied change is read back and verified.
6. **Same on Linux, macOS and Windows.** Stdlib only, UTF-8 output, CI on all three.

## Session

Korto Pro logs in through Google; the session is one cookie, `PHPSESSID`. The user copies it from a logged-in browser into an env file (`KORTO_PHPSESSID`, plus `KORTO_UNION_ID`). Plain HTTP with that cookie returns the same pages the browser sees, for as long as the browser session lasts. An expired session answers with a redirect to the login page.

## Pages

All under `/index.php?yhistu_id=…&perioodi_id=…&screen=…`:

| Screen | Used for | Parsed by |
|---|---|---|
| `yyriarvestus.naidud` | period selector, building meters (`#yldarvestid`), apartment meters (`#korterite_arvestid`) | `periods()`, `readings()` |
| `yyriarvestus.tehingud` | Korto's "expense invoices missing for cost types …" warning | `missing_invoices()` |
| `yyriarvestus.maaramised`, `yyriarvestus.arved` | distribution and invoice tables | `grid()` |

In `#korterite_arvestid` each meter cell carries a hidden `korterite_arvestid[<apartment id>][]` input whose value is the meter id; the other columns are read relative to that cell, because an open period adds a leading checkbox column.

## 0.1 — read and check

| Command | What it does |
|---|---|
| `korto periods` | period ids and states |
| `korto readings [--period ID\|YYYY-MM]` | building and apartment meters: start, end, date, quantity, forecast flag, who entered it, via resident link |
| `korto check [--period …] [--ignore APT:METER …]` | findings; exit 1 if any blocking |
| `korto summary [--period …]` | distribution and invoice tables |

`check` rules, each from a real mistake:

| Level | Rule |
|---|---|
| block | reading dated after the period end (Korto leaves it out of the period) |
| block | apartment heat quantity above the building heat meter; if there is no building heat reading, a warning says the comparison was skipped |
| block | missing reading; `--ignore APT:METER` skips a meter entirely |
| block | a cost type Korto reports as having no expense invoice |
| warn | over 5× the previous period and at least 0.5 more; the previous period is matched by meter id |
| warn | negative quantity (normal right after a forecast) |
| warn | round heat quantity (`x.000 MWh`) not marked as a forecast |

Tests run offline on hand-written fixtures with invented data, one per rule, including the open-period layout.

## 0.2 — write actions for the monthly round

Built while a period is open: Korto renders write forms only then. Each action builds its payload from the live form, prints the diff, posts only with `--apply`, then re-reads to verify.

| Command | Korto action | Guard rails |
|---|---|---|
| `korto set-reading APT METER VALUE --date D [--forecast]` | apartment form (`korteri_id=…&screen=yyriarvestus.naidud_muuda`), Save | every other row resubmitted unchanged and checked: unchecking deletes in Korto |
| `korto delete-reading APT METER` | apartment form, "Delete marked" | only the named row is marked |
| `korto set-building METER VALUE --date D` | building meters form | only the named row is submitted |
| `korto forecast --date D [--months 6]` | "Forecast marked" (`kuude_arv`, `kuupaev`) | apartments missing readings only |
| `korto distribute --date D` | `yyriarvestus.perioodid_lukusta`, field `jaotamise_kuupaev` | date required: Korto defaults to today |
| `korto reopen` | `yyriarvestus.perioodid_taasava` | — |
| `korto add-expense --like EXPENSE_ID key=value …` | new expense form prefilled from an earlier invoice | the caller sets every changed field; totals shown before posting |

Korto keys a reading by meter and date: saving a reading with a different date adds a second entry, and the period uses the latest. `set-reading` must therefore warn when a later entry exists, and `delete-reading` removes it.

Out of scope on purpose: confirming or sending invoices, payments, closing periods, other associations.

First step of 0.2: add a small `form()` parser (fields, checkboxes, button names and values), dump each write form once with a period open, record one manual submit per action from the browser's network log, then encode. Open questions: the `action` values behind Save, Delete marked and Forecast; the building meters form; how the expense template loads; which computed fields (row sums, VAT, rounding, total) the server trusts.

## Repository

Public, MIT. `korto.py`, `test_korto.py`, `fixtures/`, `README.md`, `CONTRIBUTING.md`, `docs/DESIGN.md`. `.gitignore` keeps env files, logs, JSON and HTML dumps out of the root. CI runs the tests on Linux, macOS and Windows with Python 3.10 and 3.13, offline.
