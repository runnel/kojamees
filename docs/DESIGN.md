# Design

> korto-cli — unofficial command-line companion for Korto Pro. Not affiliated with or endorsed by OÜ Tarkvaralabor.

Status: 0.1 reads and checks a period. 0.2 fills in the forms of the monthly round. 0.3 adds the period around it and a watch on Korto's forms. 0.4 adds the invoice PDF, cancelling a closing, and the payments import. Run with `--apply` on a live association: `set-reading`, `delete-reading`, `set-price`, `close`, `unclose` (all 25.11.2026). Every other write has run as a dry run against a live period; its first `--apply` is checked by hand in the web UI.

## Goal

Let an apartment association administrator do the recurring monthly round in Korto Pro — check readings, enter expense invoices, forecast missing readings, distribute costs — with plain HTTP requests instead of clicking through the web UI, on their own account, at human pace, with nothing they could not do by hand.

## Principles

1. **Minimal code.** One file, Python 3.9+ standard library, no dependencies, no build step. 0.1 is about 300 lines of code; 0.2 adds about 370: a form parser, six commands and their read-back checks.
2. **Korto only, no building knowledge.** The tool knows Korto's pages and forms. Suppliers, apartments, exceptions, proration and prices live in the administrator's own notes and arrive as arguments.
3. **Inside Korto's terms and the law.** See "Terms, law and data" in the README. Sequential requests at least a second apart, honest User-Agent, the user's own session, no crawling, no Korto content or personal data in the repository.
4. **Fail loudly.** A page layout, field or button the parser does not find is an error, never "no findings" or a guess. A redirect is never followed: after a GET it means the session expired (login page) or a page korto-cli does not know; after a POST it is Korto's normal answer to a save.
5. **A write is what the browser would send.** A dry run by default. `--apply` posts the form as clicking would, and then reads the page back to verify.
6. **Same on Linux, macOS and Windows.** Stdlib only, UTF-8 output, CI on all three.

## Session

Korto's login page offers a password, Mobile-ID, Smart-ID, an ID card and a bank. The billing pages need one cookie, `PHPSESSID`. Up to 0.4 the user copies it from a logged-in browser into an env file (`KORTO_PHPSESSID`, plus `KORTO_UNION_ID`); from 0.5 `kojamees login` keeps Korto's `auth_token` instead and logs in the way the browser does (see 0.5). Plain HTTP with that cookie returns the same pages the browser sees. An expired session answers with a redirect to the login page.

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

(As of 0.3. 0.4 brings `import-payments` and the invoice PDF after the experiments below; see 0.4.)

- **`import-payments`.** The dry run would have to upload the bank file for Korto's matching preview, and it is not known that an upload saves nothing: if Korto keeps the uploaded transactions somewhere, a dry run could make the real ISO import skip them as already present, and owners who paid would show as debtors. To settle it, in the web UI: upload a file, look at the preview, leave without saving, check the payments list, then upload the same file again and see whether any row is marked as already imported. With that evidence the command can come, ISO XML only, checked locally first.
- **`unconfirm`.** The destructive half of a round trip whose other half, confirming, is out of scope; it deletes credit invoices and what it does to invoices already e-mailed is unknown.
- **`selftest --write`.** It would write without `--apply`; every real `--apply` already reads back.
- **An invoice PDF.** The page's PDF button converts the invoice view, a summary without rows or payment details; the real invoice document comes from "Trüki ja saada märgitud", which also e-mails invoices.

Out of scope on purpose: confirming invoices, sending invoices, e-mails or reminders to anyone, deleting periods or cost types, editing cost types beyond a fixed price, meter replacement, other associations.

### Budget

About 300 lines of code on top of 0.2's 660; the guard rails are the larger part.

## 0.4 — invoices out, payments in

Captured read-only on 25.11.2026, with two experiments on the author's association.

### Commands

| Command | Korto form, button and `action` | Guard rails |
|---|---|---|
| `korto invoice-pdf APT [--period …] [--out FILE]` | `arved`: the row's Näita button posts the page's form with `screen=arved_pdf` and the invoice id | a download: the real invoice document, as printed for owners without e-mail; every invoice of the apartment (co-owners have one each), never overwriting a file; checks it is a PDF; never e-mails anyone (that is "Trüki ja saada märgitud", out of scope) |
| `korto unclose --period …` | `perioodid_vaata`, Tühista sulgemine opens `perioodid_tyhista_sulgemine`; its Tühista, `tyhista` | a closed period only; refuses when the next period is already confirmed or closed, since its opening balances came from this one; like `close`, the dry run opens Korto's page and `--apply` presses only on the page korto-cli knows |
| `korto import-payments FILE [--rows N,…]` | `laekumised_import_fail`: Laadi fail (`action=upload`, multipart, the file sent unchanged; its redirect is not followed) → `laekumised_import_andmed`, read with its own request: Korto's preview; its Salvesta, `salvesta` | the open period only; the dry run uploads and lists the preview with a reason for every row korto-cli would not save: already in Korto (date, amount and payer, counted, against the lists of the open period and the two before it, each asked with an explicit date span, because the list is per period and filtered by date), no apartment, an amount that is not positive or not the bank's, a date it cannot read, dated before the previous period or past the span it checks; `--apply` saves the rows Korto ticked minus those, or exactly the rows named with `--rows`, refusing a named row with a reason; it never ticks on its own judgement, and refuses when nothing is left to save; the preview's shape is fixed in code (fields per row, the two buttons) and its stamp must match the baseline entry of the upload form; read-back: the payments of both periods after, less before, must be exactly the rows sent |

**Experiments.** The invoice list's Näita button posts the list's form with `screen=arved_pdf` and answers with the real invoice document, the one printed for owners without e-mail. Tühista sulgemine opened Korto's page ("Kas soovid perioodi sulgemise tühistada?", Tühista and Tagasi) and left the period closed. An upload of a statement that had been imported before saved nothing: the period's payments were unchanged, and Korto keeps the preview in the session until Salvesta. In that preview Korto ticked none of the nine rows, all already in Korto, and matched six to an apartment. Run as korto-cli, the same statement listed all nine rows as already in Korto. Whether Korto ticks new rows by default is seen on the next statement with new payments; until then `--rows` is how a human picks them. The statement is a CSV: Korto has no transaction-id check for it, so korto-cli's own check is the only guard against importing a statement twice. The payments list was seen unpaged up to 13 rows; past 50, korto-cli refuses, because a paged list would hide payments from the duplicate check. Because the preview lives in the browser session korto-cli shares, do not run `import-payments` while the web UI import is open.

**Period steps.** `close` and `unclose` share one table: the button on the period page, the screen it must only open, the button on that screen and the `action` it must set, and the states before and after. Confirming invoices and cancelling a confirmation are the same kind of step; their pages exist only once a period is distributed or confirmed, so they are added to the table after they are seen, in the December 2026 round, not guessed now.

### Waiting for a period in the right state

Asked for, seen in Korto, not yet captured, each a binding of a button rather than new logic: confirming invoices (a distributed period; it numbers the invoices and notifies owners, so it needs the previous period closed, `check` without blocking findings, and never a send), cancelling a confirmation (a confirmed period; it deletes credit invoices), a credit invoice (a confirmed period's invoice), the supplier payments export ("Ekspordi maksed", `tehingud_eksport`; treated as a write, the file holds IBANs and amounts), and asking for an expense's approval ("Küsi kinnitus"; it e-mails the approver, so never twice for the same expense). The design review of 0.4 advised against the last two and the credit invoice; whether they come is the association's decision, after their pages are seen.

### Not tied to one association

The code holds no association's data. `new-period` takes the invoice and due dates' pattern from the newest period, not from one association's habit. The `check` rules on heat apply to meters in MWh only; other heat meters get the other rules.

## 0.5 — sign in once, and an MCP server

Two things stand between an administrator and Kojamees: copying a cookie out of the browser's developer tools, and a terminal. 0.5 removes the first with Korto's own login and the browser's way of keeping a session, and the second with an MCP server over the same commands. It also runs on the Python every Mac already has. What a write sends does not change.

### Seen in Korto (26.11.2026)

- Korto's login page (`korto.ee/public/login/user`) talks to `https://pro.korto.ee/api/`, a GraphQL endpoint, sending `Origin: https://korto.ee`. Its page script has `login(id, password)`, `smartIDLogin(id)` (answers with the verification code), `smartIDStatus` (polled; bound to the cookies), `mobileIDLogin(phone_number, id)`, `mobileIDStatus`, `banklinkLogin(bank)` and `logout`. Each answers with one value.
- A confirmed Smart-ID login sets two cookies on `pro.korto.ee`, path `/`: `PHPSESSID`, a session cookie without flags, and `auth_token`, a JWT without an expiry claim, `Secure` and `HttpOnly`, kept for a year.
- `auth_token` is what logs a request in. Sent alone, the billing pages open and Korto answers with a new `PHPSESSID`, which then opens them alone (E2). Sent with a `PHPSESSID` Korto has never seen, as an expired one would be, the page opens and Korto takes that id on (E1). The login's own `PHPSESSID` does not open the pages before a first visit with `auth_token`.
- No answer refreshes `auth_token` (E3).
- Korto's `logout` answers true but does not end the token: afterwards the same `auth_token` still opened the pages, with its own session and with an unknown one (E5).
- `index.php` without a screen sends the browser on to `avaleht.avaleht` without an association (E4): it does not tell which association to use.
- The browser's session stayed valid through three logins and a logout: Korto keeps parallel sessions.
- So the browser keeps `PHPSESSID` until it quits and `auth_token` for a year, and after a restart `auth_token` logs it in again. That is why a Korto login seems to last for ever.
- Later, not blocking: whether a new login or a password change ends an older token.

### Login

| Command | Korto | Guard rails |
|---|---|---|
| `kojamees login [--mobile-id \| --password]` | Smart-ID by default: `smartIDLogin`, then `smartIDStatus` every 2 s; Mobile-ID the same with `mobileIDLogin`; `--password`: `login` | It first checks that `KORTO_UNION_ID` is set: the association, copied from any Korto address (E4). The personal code comes from `KORTO_ID` (environment or env file); otherwise it is asked without echo, only when stdin is a terminal, followed by "Remember it in FILE? [Y/n]", default yes. Its check digit is verified first: a typo can be someone else's valid code, and the Smart-ID request would go to their phone. The Mobile-ID phone number works the same way (`KORTO_PHONE`). A password is asked only in a terminal, without echo, every time; never an argument, never the environment, never stored. With the code saved, `login` runs without a terminal: it prints the verification code at once (flushed) and waits, so an assistant can start it and the person confirms on the phone. It polls one request at a time, at most 2.5 minutes, and shows Korto's own error. Then it opens the readings page with `auth_token` alone and saves nothing unless that works. It saves `KORTO_AUTH_TOKEN`, never printed; `KORTO_AUTH_UNTIL`, the date its cookie ends; and `KORTO_LOGIN`, how it logged in. |

**No logout command.** Korto's own logout does not end the token (E5), so a `logout` command would promise what it cannot do. Deleting `KORTO_AUTH_TOKEN` from the env file ends Kojamees's use on that machine; a copy of the file keeps working until the token's year is out, as a copy of the browser's profile would.

ID card and bank logins stay in the browser: the card is a TLS client certificate, and the bank login ends in the browser. For them the 0.4 way stays, copying `PHPSESSID`; copying `auth_token` instead gives the year-long login.

**The env file.** `--env`, else `KORTO_ENV`, else `~/.korto.env`; a missing default file reads as empty, so setups that use only the environment keep working. Only `login` writes it; a run never does. The file may be the user's general file of secrets, so the writer reads it exactly as `load_env` does (the first `KEY=VALUE` line of a key counts; comments and `export` lines are not read) and changes only that first line of each `KORTO_` key it owns. It resolves a symlink first, re-reads the file just before writing, works in bytes (a UTF-8 byte order mark stays), keeps every other byte, and appends a missing key (after a newline if the file does not end in one). It writes a temporary file in the target's folder, created readable by its owner only before anything is written to it, and renames it over the original. On Windows the standard library cannot restrict a file to its owner, and the README says so. If the write fails, `login` says so and nothing is kept; nothing changed in Korto.

The environment wins over the file, as before. If it sets `KORTO_PHPSESSID` or `KORTO_AUTH_TOKEN`, `login` refuses and says so, since what it saved would not be used. With a token, `KORTO_PHPSESSID` is ignored wherever it comes from: a stale id would be taken on by Korto (E1) and shared by every run and perhaps the browser. `login` says so when the file still holds a 0.4 `KORTO_PHPSESSID` line.

### Keeping the session, as the browser does

- Each run starts the way the browser does after a restart: its first request carries `auth_token` alone, Korto answers with a new `PHPSESSID` (E2), and the run sends both from then on. The MCP server keeps its `PHPSESSID` for as long as it runs. Nothing is written back to the env file.
- A `PHPSESSID` is taken only from an answer that passed `ensure_logged_in`, only if its value is valid (`[A-Za-z0-9,-]+`, the check `main` already makes), and never from a deletion. If an answer carries none, because Korto took on the id it was sent (E1), the run keeps the id it has.
- `auth_token` makes a lapsed `PHPSESSID` good again on the next request (E1). No renewal step and no repeated request are needed.
- No POST goes out before the run holds a `PHPSESSID`. Every command starts with a GET; the client refuses otherwise, since a token-only POST never passes `ensure_logged_in` and its session would be dropped.
- The token is used until `KORTO_AUTH_UNTIL`, the day its cookie would have ended in the browser. A JWT without an expiry claim might be accepted for longer, but the browser would have dropped it; so does Kojamees.
- When Korto no longer accepts `auth_token`, or that day has passed, and the command runs in a terminal outside the MCP server (a server flag decides, not `isatty`), Kojamees asks "Korto's login has ended. Log in again now? [Y/n]", logs in the way `KORTO_LOGIN` says, and runs the command again from the start, but only if this run has not sent a saving POST. The only saving POST is the one in `submit`, which records it just before posting. A login can end during the read-back after a save, and running `delete-reading` again would then delete the earlier entry Korto now uses. After a save it exits 3 with the "may or may not be in Korto" message. Without a terminal (the MCP server, a script) it exits 3.
- An env file with only `KORTO_PHPSESSID`, a 0.4 setup, works as in 0.4.
- **One request at a time, across processes.** The CLI and the MCP server, or two MCP clients, share a lock file in the home folder, held for each request: `fcntl` on Linux and macOS, `msvcrt` on Windows, retried without blocking there, since a request may hold it for up to a minute. A lock dies with its process, so none is ever stale. The file also holds the wall-clock time of the last request, so the one-second spacing holds across processes.

The login is kept like the browser's on purpose. Korto keeps a browser logged in for a year on any computer, and its logout does not end that; a command-line companion that asked for Smart-ID every month would be worse than the browser and no safer.

### MCP server

`kojamees mcp` speaks the Model Context Protocol over stdio with the standard library: it reads `sys.stdin.buffer` as UTF-8 and writes one compact JSON message per line, ASCII-escaped, to the real stdout saved when it starts. It handles `initialize`, `tools/list`, `tools/call`, `ping` and `notifications/cancelled`.

Seen on 26.11.2026: a local stdio MCP server configured only for Claude Code (`~/.claude.json`) answered in a Cowork task in Claude Desktop. Cowork runs local MCP servers on the computer, outside its VM, with the computer's network, and its domain allowlist does not apply to them; so `kojamees mcp` reaches Korto from Cowork too. In spring 2026 such servers did not reach Cowork reliably.

- **Tools are the commands.** Each tool's input schema is built from that command's argparse definition: positional and optional arguments become properties, choices become enums, flags become booleans. A call builds argv from the JSON, parses it, and refuses unless the parsed result equals the JSON exactly (`--rows` is offered as an array of integers and compared as a set) and `apply` is what the gate decided; a string value that starts with `-` and is not a number is refused, since argparse would read it as a flag (`"--apply"` in `add-expense` fields would otherwise apply without the gate). The command runs in the same process with stdout and stderr captured, argparse's exits included. Exit 1 of `check` is a finding; exit 1 of a write (Korto does not show the change), 2, 3 and 4 are errors.
- **The env file at each call.** The server reads the env file at the start of every call, so a `kojamees login` in a terminal takes effect at once; a changed token drops the `PHPSESSID` the server held.
- **Not offered.** `login`: no personal code, PIN or password passes through a conversation, and an ended session answers "run `kojamees login`". `selftest --accept`: accepting the baseline is the guard on every write, so a person does it in a terminal; `selftest` itself is offered, read only. `--env`: the server's file is fixed when it starts.
- **Files.** `invoice-pdf` writes only into one folder (`KORTO_PDF_DIR`, default the Downloads folder), never the server's working directory, which may be a repository. `import-payments` accepts a `.csv` or `.xml` file only, never the env file: its dry run already uploads the file to Korto.
- **Annotations.** Every tool has a title; read commands are `readOnlyHint`, writes `destructiveHint`.
- **The write gate.** Every save in Korto is the one post in `submit`: the distribution, the import's Salvesta and the expense save included. The other POSTs save nothing (the PDF, the confirmation pages, the upload, the expense prefill). The hook sits right before that post, with the dry run's text. In the CLI the hook passes with `--apply`. In the server it decides in the same run that posts, so nothing is read again between the check and the post.
  - A write call without `apply` runs the dry run. The server keeps a token for it, made with a key created at start, that records the tool, the argv and a hash of the dry-run text, and answers with the text and the token.
  - An apply call takes only the token, so the assistant cannot change the arguments. The server runs the stored argv with `apply`. At the hook the new dry-run text must hash to the stored one, within 10 minutes, once. If Korto shows anything different, the call refuses and answers with the new dry run.
  - Where the client supports elicitation, the hook asks the person directly with `elicitation/create` and the dry-run text, and posts only on accept. The server keeps reading stdin while it waits: it answers `ping`, aborts on cancellation, and answers any other request with an error; no threads. The person, not the assistant, then sees and approves exactly what is posted. Claude Desktop and Cowork declare elicitation but, by user reports in 2026, never show the request, so the call would hang: elicitation is used only with a client that says it is Claude Code. The wait is capped at two minutes, so a post never goes out long after its reads; the lock is not held while waiting. The list is a convenience, not a guarantee: `clientInfo` is self-reported, and a headless or SDK client may answer without a person. A false claim fails safe: the call waits and posts nothing.
  - Without elicitation the gate guarantees that what is posted is what the assistant was shown. Whether the person saw it depends on the assistant, and on the client's approval prompt, which shows only the token; "always allow" removes even that. Names and payers in a dry run can carry text aimed at the assistant. The server's instructions and the README say this.
- **Stdout.** Nothing but protocol messages reaches stdout. No command asks a question in the server.

### Python 3.9

macOS ships Python 3.9.6 with its Command Line Tools, which it offers to install the first time `git` or `python3` runs. The code runs on it except one line: closing a redirect's `HTTPError` (2 of 68 tests fail on 3.9). 0.5 supports 3.9 and CI adds it, so any Mac runs Kojamees after one click.

### Name

`korto-cli` named the tool after a trademark it does not own, and said nothing about MCP. From 0.5 it is **Kojamees** (decided 26.11.2026): Estonian for the caretaker who walks the building every morning. The README opens with the name and, right under it, what ties it to Korto: "an MCP server and command line for Korto Pro", unofficial. The repository becomes `runnel/kojamees` (GitHub keeps redirecting the old address, clones included), the command and file `kojamees` and `kojamees.py`, the User-Agent `kojamees/<version>`, the MCP server `kojamees`. The repository is renamed before the 0.5 pull request is merged, since its badges point at the new name. The `KORTO_` variables, `~/.korto.env` and the baseline file keep their names: they say what they hold, and existing setups keep working.

### What the README must say

- Kojamees logs in through Korto's own login and keeps, in the env file, the personal code, the Mobile-ID phone number and Korto's `auth_token`; never a password.
- Kojamees stops using the token a year after the login, when the browser would drop it. Korto's logout does not end it, and Korto may accept a copy for longer: anyone with a copy of the env file can use the association's Korto, as with a copy of the browser's profile. Deleting the line ends Kojamees's use on that machine.
- Do not let an AI assistant read the env file: the token would reach the AI provider.
- "It never logs in" and the list of what it stores change: the token and its date, the personal code, the phone number, the lock file, and invoice PDFs in the Downloads folder.
- "It acts only on your command" now includes your assistant's commands through MCP. A write is posted only with the token of its own dry run; whether you saw that dry run depends on your assistant and on your client's approval prompt. Claude Code can ask you directly.
- Through MCP, residents' names, readings and payers reach the AI provider. The association, as data controller, decides whether to allow that.
- On Windows the env file cannot be made private by Kojamees.
- Python 3.9 or newer.
- The name: Kojamees, unofficial, for Korto Pro, on the first lines.

### Tests

Offline, like the rest:
- `tools/list` against a stored snapshot.
- Login against a fake GraphQL answer: the code, polling, the check with `auth_token` alone, nothing saved when it fails, no prompt without a terminal, and a wrong check digit.
- The env writer: duplicate keys, `export` lines, CRLF, no final newline, and a file of other secrets kept byte for byte. A run never writes the file.
- The session: the first request carries `auth_token` alone; a `PHPSESSID` is taken only from a logged-in answer; a deletion or an invalid value is ignored; a 0.4 file behaves as in 0.4.
- An ended login: the rerun happens only without a saving POST, and the command exits 3 without a terminal.
- The gate: an injected flag refused, a changed dry run, a reused or old token, an apply call by token only, and elicitation accept, decline, cancel and a ping while waiting.
- One valid call of every tool round-trips through the schema and argparse.
- The lock, the Windows retry included; the file rules (`--out` refused in the server, a `.csv` that is a symlink to the env file); a BOM in the env file; both keys present; the token's date; the elicitation cap; `notifications/cancelled`.
- Stdout carries only protocol, and stdin is read as UTF-8.

### Budget

About 140 lines for login and the env file, 30 for the session and the lock, 250 for the MCP server with its gate. Nearly all of it is guard rails.

## Repository

Public, MIT. `korto.py`, `test_korto.py`, `fixtures/`, `README.md`, `CONTRIBUTING.md`, `docs/DESIGN.md`. `.gitignore` keeps env files, logs, JSON and HTML dumps out of the root. CI runs the tests on Linux, macOS and Windows with Python 3.9 and 3.13, offline.
