# korto-cli — design

> korto-cli — unofficial command-line companion for Korto Pro. Not affiliated with or endorsed by OÜ Tarkvaralabor.

Status: design, 2026-11-25. Nothing built yet. Written to be published as-is in a
**public** repository: it contains no building, person, account or reading data.

## Goal

A minimal, unofficial command-line tool that lets an apartment association administrator
do the recurring monthly work in Korto Pro (readings, expense invoices, forecast, cost
distribution) with plain HTTP requests instead of clicking through the web UI — on their
own account, at human pace, with nothing they could not do by hand.

## Principles

1. **Minimal code.** One file, Python 3 standard library only, no dependencies, no build
   step. Target: PR 1 ≤ 250 lines, PR 2 ≤ 200 lines (excluding tests).
2. **Generic Korto, no building knowledge in code.** The tool knows Korto's pages and
   forms. It does not know suppliers, apartments, exceptions, proration or pricing rules —
   those live in the operator's private notes and are passed in as arguments.
3. **Compliance first** (see "Terms, law and data" — repeated in README and CONTRIBUTING).
4. **Writes are dry runs by default.** `--apply` is required to change anything; every
   applied change is read back and verified.
5. **Runs the same on Linux, macOS and Windows.** Stdlib only (`urllib`, `html.parser`,
   `json`, `argparse`, `pathlib`); no shell-outs, no OS-specific paths. Python ≥ 3.10.

## Terms, law and data

Based on Korto's terms of use (korto.ee/public/terms, operator OÜ Tarkvaralabor), read
on 2026-11-25. Not legal advice.

- **Intended use only (ToS 3.3).** The tool performs the same actions the web UI offers to
  a logged-in administrator, for that administrator's own association. No crawling, no
  bulk export, no access to other associations.
- **No disruption (ToS 4.1).** Sequential requests only, minimum 1 s between requests, no
  retries in loops, honest `User-Agent: korto-cli/<version> (+<repo url>)`. The tool is
  not a crawler; it only acts on an explicit command (robots.txt targets crawlers, and the
  tool respects its spirit by never crawling).
- **Your own session.** Authentication is the user's own Korto session cookie, supplied by
  the user. The tool never logs in, never stores passwords, never prints the cookie.
  Treat the cookie like a password.
- **No Korto content in the repo (ToS 11).** No copies of Korto pages, scripts, texts or
  images. Test fixtures are minimal HTML written by hand that only mimics field names.
- **No personal data in the repo (GDPR).** No names, addresses, readings, invoices, ids or
  logs — not in code, tests, issues or pull requests. The association is the data
  controller; the tool keeps nothing except an optional local audit log outside the repo.
- **If Korto objects or offers an API, we follow it.** The README invites the operator to
  get in touch.
- **Risk stated plainly:** the operator may end a subscription that breaches the terms
  (ToS 10.3). Users run the tool at their own risk.

## Configuration

Environment variables (or a `.env` file whose path the user passes with `--env`):

- `KORTO_PHPSESSID` — session cookie. The user copies it from a logged-in browser
  (DevTools console: `document.cookie.match(/PHPSESSID=([^;]+)/)[1]`).
- `KORTO_UNION_ID` — the association id (visible in Korto URLs).

Nothing else. If a response is the login page → exit code 3, "session expired".

## Architecture (single file `korto.py`)

```
transport   get(screen, **params) / post(fields)      ~25 lines  (cookie, UA, 1 s pacing, login detection)
parse       tables(html) -> rows of cells (+title/alt) ~40 lines  (html.parser)
            form(html) -> fields, checkboxes, buttons  ~40 lines
domain      readings(period) -> records                 ~30 lines
            check(period) -> findings                   ~50 lines
            write actions (PR 2)                        ~150 lines
cli         argparse subcommands, table/--json output   ~40 lines
```

Korto pages used (all under `/index.php?yhistu_id=…&perioodi_id=…&screen=…`):
`yyriarvestus.naidud` (readings), `yyriarvestus.tehingud` (expenses + "invoices missing"
warning), `yyriarvestus.maaramised` (distribution), `yyriarvestus.arved` (invoices),
`yyriarvestus.perioodid` (period state, period list), apartment form
`yyriarvestus.naidud_muuda&korteri_id=…`, `yyriarvestus.perioodid_lukusta`,
`yyriarvestus.perioodid_taasava`.

## PR 1 — read and check (no writes)

| Command | What it does |
|---|---|
| `korto periods` | list periods with id and state (open / distributed / closed) |
| `korto readings [--period ID]` | every apartment meter: start, end, date, quantity, forecast flag, entered by, via resident link |
| `korto check [--period ID] [--ignore APT:METER …]` | findings (exit 1 if any blocking) |
| `korto summary [--period ID]` | distribution matrix and invoice table |

`check` rules — generic, each derived from a real mistake:

1. A reading dated after the period end (it would be ignored or land in the wrong period).
2. A heat reading whose consumption exceeds the whole building's heat meter.
3. Consumption more than 5× the previous period (previous period read the same way).
4. Negative consumption — warning only; normal after a forecast.
5. Cold and hot water deltas swapped compared with the previous period.
6. A round-number delta (x.000) entered by a resident — likely an estimate.
7. Missing readings (minus `--ignore`).
8. Korto's own "expense invoices missing for cost types …" warning, surfaced.

Tests: hand-written HTML fixtures with invented names and numbers, one per rule.

## PR 2 — write actions for the monthly cycle

Built while a period is open (write forms are only rendered then). Each action: build the
payload from the live form, show the diff, `--apply` to post, re-read to verify.

| Command | Korto action | Guard rails |
|---|---|---|
| `korto set-reading APT METER VALUE --date D [--forecast]` | apartment form, Save | every other row is resubmitted unchanged and checked (unchecking deletes in Korto) |
| `korto delete-reading APT METER` | apartment form, "Delete marked" | only the named row is marked; native confirm bypassed by posting directly |
| `korto set-building METER VALUE --date D` | building meters form | only the named row is submitted |
| `korto forecast --date D [--months 6]` | "Forecast marked" | apartments missing readings only |
| `korto distribute --date D` | period lock page | refuse without explicit date (Korto defaults to today) |
| `korto reopen` | period reopen page | — |
| `korto add-expense --like EXPENSE_ID key=value …` | new expense form prefilled from an earlier invoice | the caller sets every changed field (number, dates, quantities, amounts, total); tool shows totals before posting |

Not included, on purpose: confirming or sending invoices, payments, closing periods,
anything about other associations.

Capture step at the start of PR 2 (once): dump the live forms' fields and button values
with PR 1's `form()` parser, record one manual submit per action from the browser network
log, then encode. Open questions: the `action` values behind Save / Delete marked /
Forecast, the building meters form `screen`, how the expense template loads, and which
computed fields (row sums, VAT, rounding, total) the server trusts.

## Repository

- Public GitHub repo `korto-cli`, MIT licence.
- Files: `korto.py`, `test_korto.py`, `fixtures/`, `README.md` (usage + "Terms, law and
  data" in full), `CONTRIBUTING.md` (no real data in issues/PRs/fixtures; same terms),
  `.gitignore` (`.env`, `*.log`, `audit*.jsonl`, `config*.json`).
- CI: GitHub Actions running `python -m unittest` on ubuntu-latest and macos-latest
  (proves the Linux/macOS claim), no network.

## Name

`korto-cli` (command `korto`). The name says what the tool works with; the first line
of the README, the repo description and `korto --version` all state that it is
unofficial and not affiliated with or endorsed by OÜ Tarkvaralabor.
