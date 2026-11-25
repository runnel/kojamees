# Design

> korto-cli — unofficial command-line companion for Korto Pro. Not affiliated with or endorsed by OÜ Tarkvaralabor.

Status: 0.1 reads and checks a period. 0.2 fills in the forms of the monthly round. Every 0.2 command has been run as a dry run against a live open period. The first `--apply` of each one is checked by hand in the web UI.

## Goal

Let an apartment association administrator do the recurring monthly round in Korto Pro — check readings, enter expense invoices, forecast missing readings, distribute costs — with plain HTTP requests instead of clicking through the web UI, on their own account, at human pace, with nothing they could not do by hand.

## Principles

1. **Minimal code.** One file, Python 3.10+ standard library, no dependencies, no build step. 0.1 is about 300 lines of code; 0.2 adds about 370: a form parser, six commands and their read-back checks.
2. **Korto only, no building knowledge.** The tool knows Korto's pages and forms. Suppliers, apartments, exceptions, proration and prices live in the administrator's own notes and arrive as arguments.
3. **Inside Korto's terms and the law.** See "Terms, law and data" in the README. Sequential requests at least a second apart, honest User-Agent, the user's own session, no crawling, no Korto content or personal data in the repository.
4. **Fail loudly.** A page layout, field or button the parser does not find is an error, never "no findings" or a guess. A redirect is never followed: after a GET it means the session expired (login page) or a page korto-cli does not know; after a POST it is Korto's normal answer to a save.
5. **A write is what the browser would send.** A dry run by default. `--apply` posts the form as clicking would, and then reads the page back to verify.
6. **Same on Linux, macOS and Windows.** Stdlib only, UTF-8 output, CI on all three.

## Session

Korto Pro logs in through Google; the session is one cookie, `PHPSESSID`. The user copies it from a logged-in browser into an env file (`KORTO_PHPSESSID`, plus `KORTO_UNION_ID`). Plain HTTP with that cookie returns the same pages the browser sees, for as long as the browser session lasts. An expired session answers with a redirect to the login page.

## Pages

All under `/index.php?yhistu_id=…&perioodi_id=…&screen=…`:

| Screen | Used for | Parsed by |
|---|---|---|
| `yyriarvestus.naidud` | period selector, building meters (`#yldarvestid`), apartment meters (`#korterite_arvestid`), the forecast dialog | `periods()`, `readings()`, `forms()` |
| `yyriarvestus.naidud_muuda` (`korteri_id`, `arvesti_id=0`) | one apartment's readings form; `korteri_id=0` is the building meters | `forms()` |
| `yyriarvestus.tehingud` | Korto's "expense invoices missing for cost types …" warning, the expense list | `missing_invoices()`, `grid()` |
| `yyriarvestus.tehingud_form` (`tehingu_id=0`) | a new expense invoice | `forms()` |
| `yyriarvestus.perioodid_lukusta` | distribution: date and Korto's own warnings | `forms()` |
| `yyriarvestus.perioodid_vaata` | one period's page, with its "Taasava periood" button | `forms()` |
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

## 0.2 — the monthly round

Korto renders its write forms only in an open period; every command but `reopen` refuses another period.

| Command | Korto form, button and `action` | Guard rails |
|---|---|---|
| `korto set-reading APT\|building METER VALUE [--date D] [--forecast]` | `naidud_muuda`, Salvesta, `salvesta` | the row gets `nait`, `kuupaev`, `kogus`, `muudetud=t`; rows with any sign of a reading stay ticked (unticking deletes in Korto), empty rows are unticked; refuses a second date for a meter that has a reading; an apartment number found twice is refused |
| `korto delete-reading APT\|building METER` | `naidud_muuda`, Kustuta märgitud, `kustuta_margitud` | only the named row is ticked; reports the earlier entry Korto then uses |
| `korto forecast [--date D] [--months N]` | `naidud`, the Prognoosi dialog, `prognoosi` (`kuude_arv`, `kuupaev`) | the fields are filled inside that dialog; the other dialogs are disabled, as the page does |
| `korto distribute [--date D]` | `perioodid_lukusta`, Jaota kulud, `lukusta` (`jaotamise_kuupaev`) | the date defaults to the period end, not to today as in Korto; Korto's own warnings are printed |
| `korto reopen [--period …]` | `perioodid_vaata`, Taasava periood | the newest period by default; refuses open, confirmed and closed periods; a dry run never loads the reopen screen, which may act as soon as it is loaded |
| `korto add-expense --like ID\|NAME FIELD=VALUE …` | `tehingud_form`: `vali_tehing` fills it from the earlier invoice (Korto saves nothing; the partner is checked), then Salvesta, `salvesta` | for rows you change, redoes the sums in cents (`Decimal`, half up); refuses rows or a total that do not add up (rows to the cent, the total within 5 cents, as invoices round), and an invoice of the same partner already in the period by number, or by amount when there is no number |

Read-back: after `--apply` each command reads the page again. The readings commands also compare every other meter of the form, and `forecast` every reading that was already there: a change Korto made beyond the one asked for is an error, not silence. A command that fails after posting says the change may or may not be in Korto, so that nobody runs it twice blindly; the one write that would double, `add-expense`, also refuses an invoice of the same partner and number (or amount) already in the period.

How a form is sent: `forms()` lists every control of every form in document order, with what a browser would submit (ticked boxes, the selected option, not disabled fields). Korto repeats names (a hidden field and a text field called `naidud[0][nait]`); PHP keeps the last, so `fill()` sets the last. Korto's buttons only assign hidden fields before submitting (`this.form.elements['action'].value = …`, `setFormValues(this.form, {…})`); `press()` applies those assignments, and refuses a disabled button or one whose onclick it cannot read. A dialog button posts its `data-action` and disables the other dialogs. The CSRF token is a form field like any other. Korto answers a save with a redirect, which is not followed: the command reads the page back instead and exits 1 if the change is not there.

After a save Korto moves the readings form on to the next apartment. korto-cli never follows it, so a save never lands in the wrong apartment.

Out of scope on purpose: confirming or sending invoices, payments, closing periods, cost types and their prices, meter replacement, other associations.

## Repository

Public, MIT. `korto.py`, `test_korto.py`, `fixtures/`, `README.md`, `CONTRIBUTING.md`, `docs/DESIGN.md`. `.gitignore` keeps env files, logs, JSON and HTML dumps out of the root. CI runs the tests on Linux, macOS and Windows with Python 3.10 and 3.13, offline.
