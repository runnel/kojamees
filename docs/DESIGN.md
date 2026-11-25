# Design

> korto-cli — unofficial command-line companion for Korto Pro. Not affiliated with or endorsed by OÜ Tarkvaralabor.

Status: 0.1 reads and checks a period. 0.2 fills in the forms of the monthly round. 0.3 adds the period around it and a watch on Korto's forms. Run with `--apply` on a live association: `set-reading`, `delete-reading` (25.11.2026), `set-price` and `close` (25.11.2026). Every other write has run as a dry run against a live period; its first `--apply` is checked by hand in the web UI.

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

Korto renders its write forms only in an open period; these commands, except `reopen`, refuse another period.

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

## 0.3 — the period around the round, and a watch on Korto

Forms captured read-only from a live association on 25.11.2026. The design was reviewed before any code; the review's changes are in.

A period moves through four states in order, none skipped: avatud (open) → jaotatud (distributed) → kinnitatud (confirmed: invoices numbered and visible to owners) → suletud (closed: late-payment interest and closing balances, which open the next period). Closing must happen before the next period's invoices are confirmed, or the next period starts from zero balances.

### Read

| Command | Korto page | Notes |
|---|---|---|
| `korto table NAME [--period …]` | NAME is `costs`, `payments`, `balances`, `expenses`, `distribution` or `invoices`: `kululiigid`, `laekumised`, `saldod`, `tehingud`, `maaramised`, `arved` | the table whose header has the column korto-cli expects for NAME, else an error; `--json` |
| `korto history APT\|building METER` | `naidud_ajalugu` (`arvesti_id`) | every reading of one meter across periods: date, reading, quantity, forecast, period |

### Write

| Command | Korto form, button and `action` | Guard rails |
|---|---|---|
| `korto new-period` | `perioodid_form` (`perioodi_id=0`, reached by GET like the page's own "Uus periood" form), Salvesta, `salvesta` | refuses while any period is open; refuses unless Korto's default is the newest period's next month, with the invoice date its last day and the due date the 14th after; the dry run lists every field the form sends; warns when the previous period is confirmed but not closed |
| `korto close --period …` | `perioodid_vaata`, Sulge periood opens `perioodid_sulge`, Korto's closing page; its Sulge, `sulge` | the period is required, and must be confirmed; refuses when an older period is not closed, or when the next period is already confirmed or closed; the dry run opens the closing page and prints its warnings, the period's payment count and newest payment date; `--apply` presses Sulge only on the closing page korto-cli knows (below) |
| `korto set-price CODE PRICE` | `kululiigid` gives the cost type id (`kululiigid_vaata` link text is the code, then a space); `kululiigid_form`, Salvesta, `salvesta` (`kululiik[fikseeritud_hind]`) | open period only (cost types belong to a period); the code must match exactly once; only a fixed unit price (`kululiik[hinna_reegel]` is `H`); the price is without VAT, in `Decimal`, and refused if it has more decimals than the cost type keeps (`kululiik[hinna_tapsus]`); read-back compares every other field of the form |
| `korto set-residents APT N` | `naidud_muuda`, `elanike_arv` and `korter_muudetud=t`, Salvesta | the same row rules as `set-reading`, which posts the same form; read-back checks the number and every reading |

**The closing page.** The design first allowed one press only and an observation, because a page answering Sulge periood could have offered the next period's confirmation, a new period, or the period's delete button. The live close of 25.11.2026 answered: Sulge periood opens a closing page and changes nothing (the period stayed confirmed), like Jaota kulud. That page is now written into the code: its form posts `screen=perioodid_sulge` for this period, has nothing to fill in, and has exactly two buttons, Sulge (`action=sulge`) and Tagasi. Anything else is described and refused before any write; the closing page is also in the baseline. `reopen` stays one press: Taasava periood carries a `confirm()`, so Korto acts on it at once.

### Watch on Korto

Korto can change its forms at any time. Failing loudly on a missing field or button is not enough: a new field Korto starts to expect is invisible to the parser.

- **Fingerprint.** A form reduces to its shape: method, action URL and enctype; for each field name, with numbers and ids replaced by `N`, the order of its control types (PHP keeps the last of repeated names, so order matters), disabled flags and radio values; the values of the hidden `screen` and `action`; for every button its label, its normalised onclick, whether it asks `confirm()`, its dialog and `data-action`. Never select options or row counts: they change all the time and carry names.
- **Version stamp.** Every page loads Korto's scripts as `javascript/…js?YYYYMMDD`; the stamp changes when Korto ships.
- **Baseline.** `korto selftest`, read only, loads the forms the current period states allow (the readings forms, the forecast dialog, distribution, a prefilled expense, every cost type with a fixed price, whose forms differ by type; the closing page of a confirmed period and the page of a distributed one; the new-period form when none is open), prints each one's differences from the baseline, and with `--accept` stores those entries with their stamp. The baseline is a local file (`KORTO_BASELINE`, default `~/.korto-baseline.json`) keyed by association, never in the repository: it holds this association's form shapes.
- **Apply.** Before posting, every write computes the fingerprint of the very form it is about to send and compares it, with the page's stamp, to the baseline entry for that form. No entry, no stamp, a different stamp or a different shape: it refuses and names the form. The dry run says the same thing in advance. The confirmation page of an unknown answer and the expense prefill cannot be fingerprinted before they are seen, so their shapes are fixed in code.

### Left out, with reasons

- **`import-payments`.** The dry run would have to upload the bank file for Korto's matching preview, and it is not known that an upload saves nothing: if Korto keeps the uploaded transactions somewhere, a dry run could make the real ISO import skip them as already present, and owners who paid would show as debtors. To settle it, in the web UI: upload a file, look at the preview, leave without saving, check the payments list, then upload the same file again and see whether any row is marked as already imported. With that evidence the command can come, ISO XML only, checked locally first.
- **`unconfirm`.** The destructive half of a round trip whose other half, confirming, is out of scope; it deletes credit invoices and what it does to invoices already e-mailed is unknown.
- **`selftest --write`.** It would write without `--apply`; every real `--apply` already reads back.
- **An invoice PDF.** The page's PDF button converts the invoice view, a summary without rows or payment details; the real invoice document comes from "Trüki ja saada märgitud", which also e-mails invoices.

Out of scope on purpose: confirming invoices, sending invoices, e-mails or reminders to anyone, deleting periods or cost types, editing cost types beyond a fixed price, meter replacement, other associations.

### Budget

About 300 lines of code on top of 0.2's 660; the guard rails are the larger part.

## Repository

Public, MIT. `korto.py`, `test_korto.py`, `fixtures/`, `README.md`, `CONTRIBUTING.md`, `docs/DESIGN.md`. `.gitignore` keeps env files, logs, JSON and HTML dumps out of the root. CI runs the tests on Linux, macOS and Windows with Python 3.10 and 3.13, offline.
