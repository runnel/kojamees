```text
╦╔═╔═╗  ╦╔═╗╔╦╗╔═╗╔═╗╔═╗
╠╩╗║ ║  ║╠═╣║║║║╣ ║╣ ╚═╗
╩ ╩╚═╝╚═╝╩ ╩╩ ╩╚═╝╚═╝╚═╝
```

**Kojamees is an MCP server and command line for [Korto Pro](https://korto.ee)**, the web application many Estonian apartment associations do their billing in. Unofficial: not affiliated with or endorsed by OÜ Tarkvaralabor. *Kojamees* is Estonian for the caretaker who walks the building every morning. This one walks the monthly billing round in Korto: it checks the readings, enters the expense invoices and distributes the costs, with your own Korto login, for you or for your AI assistant.

[![tests](https://github.com/runnel/kojamees/actions/workflows/test.yml/badge.svg)](https://github.com/runnel/kojamees/actions/workflows/test.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

`kojamees check` reads a billing period and lists what to fix before you distribute costs; a handful of commands then fill in the same forms you would, as dry runs unless you add `--apply`. It stays inside Korto's terms: see [Terms, law and data](#terms-law-and-data).

## Why

Korto Pro is the web application many Estonian apartment associations use for meter readings, expenses and monthly invoices. It has no API and no MCP server, so there is no supported way for a script or an AI assistant to work with it: everything goes through clicking in a browser. The monthly round is the same every month — check the readings, enter the expense invoices, forecast what is missing, distribute — and it belongs in a tool.

The expensive mistakes come in through resident self-service and are easy to miss in the web UI: a reading dated in the next month, which Korto then leaves out of the period; a heat reading larger than the whole building; a round `1.000 MWh` that is an estimate, not a reading. Once invoices go out, every fix is a credit note and a message to a neighbour.

Kojamees reads the same pages you see, with your login, one request a second, checks them, and fills in the forms of the monthly round the way you would.

## Who it is for

An apartment association administrator who uses Korto Pro, in a terminal or through an AI assistant such as Claude. Kojamees is young and run monthly by its author on one association.

It covers the monthly billing round and the period around it, and nothing else:

- **Log in:** with Korto's own Smart-ID, Mobile-ID or password login, kept the way your browser keeps it.
- **Read:** billing periods, meter readings and their history, the check, and Korto's tables: cost types, payments, balances, expenses, distribution, invoices.
- **Write, a dry run unless `--apply`:** enter or delete a reading, set the number of residents, enter an expense invoice, set a fixed price, import a bank statement's payments, forecast missing readings, distribute costs, reopen a distributed period, close a confirmed one and cancel the closing, open the next. See [docs/DESIGN.md](docs/DESIGN.md).
- **Download:** an apartment's invoice as the PDF Korto prints, for owners without e-mail.
- **Watch:** `kojamees selftest` compares Korto's forms with the ones Kojamees last saw.
- **Serve:** `kojamees mcp` offers the same commands to an AI assistant; a write needs the token of its own dry run.

Everything else in Korto is out of scope and stays in the web UI, for now: confirming invoices and cancelling a confirmation, sending invoices, e-mails or reminders to anyone, credit invoices, owners, documents, settings, and any association other than your own.

## What it looks like

```
$ kojamees check --period 2026-10 --ignore "4:Soe vesi"
level  apartment  meter      message
block  1          Küte       dated 02.11.2026, after the period end 31.10.2026
block  2          Külm vesi  missing reading
block  2          Küte       9.000 MWh is more than the whole building (4.000 MWh)
warn   2          Küte       9.000 is over 5x the previous period (0.400)
warn   2          Küte       round number 9.000 MWh: an estimate?
warn   3          Külm vesi  negative consumption -0.100 (normal after a forecast)
warn   3          Küte       round number 1.000 MWh: an estimate?
block             Prügivedu  expense invoice missing
block             Elekter    expense invoice missing

2026 oktoober: 5 blocking, 4 warnings
```

```
$ kojamees set-reading 1 "Külm vesi" 14.2
2026 november: Street 1-1 Külm vesi
field     now  after
end            14.200
date           30.11.2026
quantity       1.700
forecast  f    f

Korto form: yyriarvestus.naidud_muuda, action salvesta
baseline: matches
dry run: nothing was saved in Korto. Add --apply to save it.
```

Output from the test fixtures; names and numbers are invented.

Exit codes: 0 nothing blocking, or done · 1 blocking findings, or a change Korto does not show · 2 usage error, a Korto page Kojamees does not understand, or an expense that does not add up · 3 the login has ended · 4 Korto request failed.

## Install

Python 3.9 or newer, standard library only. On a Mac without developer tools, the first `git` or `python3` asks to install Apple's Command Line Tools, which bring both: click Install.

```bash
git clone https://github.com/runnel/kojamees && cd kojamees
mkdir -p ~/.local/bin && ln -s "$PWD/kojamees.py" ~/.local/bin/kojamees   # Linux, macOS
```

On Windows, install Python from python.org and run it as `py kojamees.py …`.

## Log in

```bash
kojamees login              # Smart-ID; or --mobile-id, or --password
```

The first time, it asks for your personal code, shows Smart-ID's verification code, waits while you enter PIN1 on your phone, and finds your association on Korto's first page; if your login opens several, it asks which. It keeps Korto's login in `~/.korto.env` (or the file `KORTO_ENV` or `--env` names), with the personal code unless you say no, and never prints the login. From then on every command logs in the way your browser does after a restart. After a year, as in the browser, it asks you to log in again.

A password is asked without echo every time and never stored. With an ID card or a bank login, which work only in a browser, copy the session instead: log in to Korto Pro in your browser, open the developer tools, then Application (Chrome) or Storage (Firefox, Safari) → Cookies → `https://pro.korto.ee`, and put the value of `PHPSESSID` in the env file as `KORTO_PHPSESSID=…`, beside `KORTO_UNION_ID=…`. That session ends when the browser's does.

Before the first `--apply`, let Kojamees see Korto's forms once, read the list, and keep it as the baseline:

```bash
kojamees selftest            # read only: every form the current period states allow
kojamees selftest --accept   # keep them in ~/.korto-baseline.json (or $KORTO_BASELINE)
```

Every `--apply` compares the form it is about to send with this baseline, and refuses when Korto has shipped a new version or the form has changed shape: run `kojamees selftest`, read the differences, and accept only what you understand. A form that the current period states do not show (the next period's form while one is open, say) is added on a later `selftest --accept`.

## Everyday commands

```bash
kojamees periods                      # period ids and states: avatud, kinnitatud, suletud
kojamees readings --period 2026-10    # every meter: start, end, date, quantity, forecast, who entered it
kojamees check --period 2026-10       # what to fix before distributing costs
kojamees summary --period 2026-10     # the distribution and invoice tables
kojamees table balances               # costs, payments, balances, expenses, distribution or invoices
kojamees history 2 Küte               # every reading of one meter, across periods
kojamees invoice-pdf 2 --period 2026-10   # the invoice document, saved as a private file; nothing is sent
```

Without `--period` the open period is used; `--period` also takes a period id. These commands take `--json`. `check --ignore APT:METER ...` skips those meters entirely, for example the meters of an empty apartment.

`check` blocks on a reading dated after the period end, an apartment heat reading above the building's heat meter (heat meters in MWh), a missing reading, and a cost type Korto itself reports as having no expense invoice. It warns on consumption over 5× the previous period (and at least 0.5 more), negative consumption (normal right after a forecast), a round heat quantity such as `1.000 MWh`, and a missing building heat reading.

## The monthly round

```bash
kojamees set-reading building Küte 50.25          # the building heat meter, from the heat invoice
kojamees set-reading 2 "Külm vesi" 120.5          # an apartment meter
kojamees delete-reading 2 Küte                     # remove a reading, e.g. one entered with the wrong date
kojamees add-expense --like heat "tehing[arve_nr]=101" "read[0][kogus]=5.123"
kojamees forecast                                  # Korto's forecast for every missing reading
kojamees check && kojamees distribute              # distribute costs and make the invoices
kojamees reopen                                    # reopen the last distributed period
```

Around the round:

```bash
kojamees set-price 041 60.00                       # a cost type's fixed unit price, without VAT
kojamees set-residents 2 3                         # an apartment's number of residents
kojamees import-payments statement.csv            # the bank statement, through Korto's own import
kojamees close --period 2026-10                    # close the confirmed period before the next one is confirmed
kojamees unclose --period 2026-10                  # cancel the closing, e.g. to correct a payment
kojamees new-period                                # open the next period once the open one is distributed
```

Each one is a dry run: it prints what would change and which Korto form and action it would post, and saves nothing. Add `--apply` to send the same form the web UI sends; Kojamees then reads the page back and exits with 1 if Korto does not show the change, or shows other readings changed too. If a command with `--apply` stops with an error or a timeout, the change may or may not be in Korto: look in the web UI before running it again. Writes work only in the open period, and dates default to the period end.

- `set-reading` and `delete-reading` take an apartment number, or `building`, and a meter name or id as `kojamees readings` shows it. Every other reading in the form is sent back unchanged (in Korto, unticking a row deletes its reading). Korto keeps one reading per meter and date and uses the latest, so `set-reading` refuses a second date for a meter that already has a reading: delete it first.
- `add-expense` starts from an earlier invoice (`--like` takes its id or part of the partner name; to do that, Korto fills in its form, as picking the invoice in the web UI does) and changes Korto's own form fields, which the dry run lists. For the rows you change it redoes the sums Korto's page redoes as you type: a new quantity or price gives a new net amount, a new net amount a new unit price, and the total `tehing[tasuda]` follows. It refuses an invoice whose rows and total do not add up, or one of the same partner and number (or amount, without a number) already in the period. Check the dry run against the invoice.
- `forecast --months 1-12` sets how many earlier months Korto averages (default 6).
- `reopen` reopens a distributed period, never one whose invoices are confirmed or that is closed.
- `close` needs the period named. It refuses when an older period is not closed, when the next period is already confirmed, or when Korto's closing page is not the one Kojamees knows; the dry run opens that page, which acts on nothing, and shows Korto's warnings and the newest payment.
- `unclose` takes a closed period whose next period is not yet confirmed, and works like `close`: the dry run opens Korto's page, `--apply` presses only on the page Kojamees knows. Close the period again before the next one's invoices are confirmed.
- `import-payments` sends the bank file unchanged to Korto's own import and lists Korto's preview: each row, the apartment Korto matched, whether Korto ticked it, and why Kojamees would not save it (already in Korto by date, amount and payer; no apartment; an amount that is not the bank's; a date outside what it checks). `--apply` saves the rows Korto ticked, never one Kojamees would refuse; `--rows 2,5` saves exactly the rows you name from the list instead. It never ticks a row on its own. Korto keeps the preview in the session: with a session copied from the browser, do not run it while the web UI import is open.
- `invoice-pdf` posts the invoice list's form exactly as its Näita button does, refuses if that form would carry anything else, does not overwrite a file, and writes it readable by you only: it holds a neighbour's personal data, so keep it out of any repository.
- `set-price` changes only a fixed unit price, refuses a price with more decimals than the cost type keeps, and checks that nothing else in the form moved.
- `new-period` sends Korto's own defaults, and only when they follow the newest period's pattern: the next month, its invoice date and due date.

## With an AI assistant (MCP)

`kojamees mcp` serves the commands above to an AI assistant over the Model Context Protocol. Log in and run `kojamees selftest --accept` in a terminal first: neither is offered to the assistant.

Claude Code, and Cowork in the Claude desktop app, which uses the same servers:

```bash
claude mcp add --scope user kojamees -- kojamees mcp
```

Claude Desktop's chat, in its `claude_desktop_config.json` (Settings → Developer → Edit Config), with full paths:

```json
{"mcpServers": {"kojamees": {"command": "/usr/bin/python3", "args": ["/Users/you/kojamees/kojamees.py", "mcp"]}}}
```

What the assistant can and cannot do:

- Read tools change nothing. A write tool is a dry run that answers with a token; it applies only when called again with the same arguments and that token, once, within ten minutes, and only if Korto still shows what the dry run showed. The assistant is told to show you the dry run and wait for your yes. Claude Code also asks you directly before each change is posted; other clients rely on their own approval prompt, which shows only the token.
- It cannot log in, accept a baseline, or name where a PDF goes: invoice PDFs are saved in your Downloads folder, or in `KORTO_PDF_DIR`. `import-payments` takes only the full path of a `.csv` or `.xml` statement.
- Through MCP, residents' names, readings and payers reach your AI provider. Your association, as the controller of that data, decides whether to allow it.
- Do not let an assistant read your env file: Korto's login would reach the AI provider.

## Terms, law and data

Kojamees is built to stay inside [Korto's terms of use](https://korto.ee/public/terms) and the law. This is our reading, not legal advice; whoever runs the tool is responsible for using it the same way.

- **Intended use only** (terms 3.3): only what the web UI offers a logged-in administrator, for that administrator's own association. No crawling, no bulk export, no other associations.
- **No disruption** (terms 4.1): one request at a time, across every Kojamees process on the computer (a lock file, `~/.korto.lock`), at least a second apart, no retry loops, `User-Agent: kojamees/<version>`. It acts only on your command, or your assistant's through MCP.
- **Nothing the web UI would not send**: a change is the same form, with the same fields, that clicking would submit, one change per command, a dry run unless you add `--apply`, and read back afterwards.
- **Your own login**: Kojamees logs in only through Korto's own login page's API, as the person whose Smart-ID, Mobile-ID or password it is, and keeps what the browser keeps: Korto's login token. Korto honours the token for a year, and Korto's logout does not end it: anyone with a copy of your env file can use your association's Korto, as with a copy of your browser's profile. Kojamees stops using it after a year, as the browser does; deleting the `KORTO_AUTH_TOKEN` line ends its use on that computer. It never stores a password and never prints the login. On Linux and macOS the env file is made readable by you only; on Windows Kojamees cannot do that.
- **No Korto content in this repository** (terms 11): fixtures are small HTML files written by hand.
- **No personal data in this repository** (GDPR): no names, readings, invoices or ids of real people or associations — not in code, tests, issues or pull requests. Your association is the data controller for what the tool reads. On your computer the tool keeps the env file (Korto's login, your association id, and your personal code and Mobile-ID phone number if you let it), the baseline (the shapes of Korto's forms), the lock file, and an invoice PDF only where you ask for it.
- **The operator decides**: Korto may end a subscription that breaches its terms (terms 10.3). If OÜ Tarkvaralabor asks us to change or stop something, or offers an API, we follow it — open an issue.

## Documentation

- [Design](docs/DESIGN.md): the read and write phases, what each command sends, the login and the MCP server.
- [Contributing](CONTRIBUTING.md): the tests, and the rules on data and Korto content.

## Licence

MIT. "Korto" is a trademark of its owner and is used here only to say what this tool works with.
