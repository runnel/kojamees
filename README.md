# korto-cli

[![tests](https://github.com/runnel/korto-cli/actions/workflows/test.yml/badge.svg)](https://github.com/runnel/korto-cli/actions/workflows/test.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Unofficial command-line companion for [Korto Pro](https://korto.ee). Not affiliated with or endorsed by OÜ Tarkvaralabor. `korto check` reads a billing period with your own Korto session and lists what to fix before you distribute costs. It stays inside Korto's terms: see [Terms, law and data](#terms-law-and-data).

## Why

Korto Pro is the web application many Estonian apartment associations use for meter readings, expenses and monthly invoices. It has no API and no MCP server, so there is no supported way for a script or an AI assistant to work with it: everything goes through clicking in a browser. In 2027 that makes little sense for running an association. The monthly round is the same every month — check the readings, enter the expense invoices, forecast what is missing, distribute — and it belongs in a tool.

The expensive mistakes come in through resident self-service and are easy to miss in the web UI: a reading dated in the next month, which Korto then leaves out of the period; a heat reading larger than the whole building; a round `1.000 MWh` that is an estimate, not a reading. Once invoices go out, every fix is a credit note and a message to a neighbour.

korto-cli reads the same pages you see, with your session, one request a second, and checks them.

## Who it is for

An apartment association administrator who uses Korto Pro and a terminal. korto-cli is young and run monthly by its author on one association.

It covers the monthly billing round and nothing else:

- **0.1 (now), read only:** billing periods, meter readings, the check, the distribution and invoice tables.
- **0.2 (planned), dry-run by default:** enter readings, enter an expense invoice, forecast missing readings, distribute costs, reopen a period. See [docs/DESIGN.md](docs/DESIGN.md).

Everything else in Korto is out of scope and stays in the web UI: confirming and sending invoices, payments, residents and owners, documents, messages, settings, and any association other than your own.

## What it looks like

```
$ korto check --period 2026-10 --ignore "4:Soe vesi"
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

Output from the test fixtures; names and numbers are invented.

Exit codes: 0 nothing blocking · 1 blocking findings · 2 usage error, or a Korto page korto-cli does not understand · 3 session expired · 4 Korto request failed.

## Install

```bash
git clone https://github.com/runnel/korto-cli && cd korto-cli
mkdir -p ~/.local/bin && ln -s "$PWD/korto.py" ~/.local/bin/korto   # Linux, macOS
```

On Windows run it as `py korto.py …`. Requires Python >= 3.10, standard library only.

## Minimal config

korto-cli needs two values: your Korto session cookie and your association id.

1. Log in to Korto Pro in your browser. Open the developer tools, then Application (Chrome) or Storage (Firefox, Safari) → Cookies → `https://pro.korto.ee`, and copy the value of `PHPSESSID`.
2. Copy the number after `yhistu_id=` from any Korto URL.
3. In a text editor, save both in `~/.korto.env`, outside any repository (an editor keeps the cookie out of your shell history):

```
KORTO_PHPSESSID=paste-the-cookie-value-here
KORTO_UNION_ID=12345
```

4. Make the file private and point korto-cli at it (or pass `--env` each time):

```bash
chmod 600 ~/.korto.env
echo 'export KORTO_ENV=~/.korto.env' >> ~/.zshrc     # or ~/.bashrc
```

The cookie is a password to your Korto account: korto-cli never prints it. It lasts as long as the browser session; logging out ends it for korto-cli too, which then exits with 3 and you copy a fresh one.

## Everyday commands

```bash
korto periods                      # period ids and states: avatud, kinnitatud, suletud
korto readings --period 2026-10    # every meter: start, end, date, quantity, forecast, who entered it
korto check --period 2026-10       # what to fix before distributing costs
korto summary --period 2026-10     # the distribution and invoice tables
```

Without `--period` the open period is used; `--period` also takes a period id. Every command takes `--json`. `check --ignore APT:METER ...` skips those meters entirely, for example the meters of an empty apartment.

`check` blocks on a reading dated after the period end, an apartment heat reading above the building's heat meter, a missing reading, and a cost type Korto itself reports as having no expense invoice. It warns on consumption over 5× the previous period (and at least 0.5 more), negative consumption (normal right after a forecast), a round heat quantity such as `1.000 MWh`, and a missing building heat reading.

## Terms, law and data

korto-cli is built to stay inside [Korto's terms of use](https://korto.ee/public/terms) and the law. This is our reading, not legal advice; whoever runs the tool is responsible for using it the same way.

- **Intended use only** (terms 3.3): only what the web UI offers a logged-in administrator, for that administrator's own association. No crawling, no bulk export, no other associations.
- **No disruption** (terms 4.1): one request at a time, at least a second apart, no retry loops, `User-Agent: korto-cli/<version>`. It acts only on your command.
- **Your own session**: it never logs in, never stores a password, never prints the cookie.
- **No Korto content in this repository** (terms 11): fixtures are small HTML files written by hand.
- **No personal data in this repository** (GDPR): no names, readings, invoices or ids of real people or associations — not in code, tests, issues or pull requests. Your association is the data controller for what the tool reads; the tool stores nothing.
- **The operator decides**: Korto may end a subscription that breaches its terms (terms 10.3). If OÜ Tarkvaralabor asks us to change or stop something, or offers an API, we follow it — open an issue.

## Documentation

- [Design](docs/DESIGN.md): the read and write phases, what each command sends.
- [Contributing](CONTRIBUTING.md): the tests, and the rules on data and Korto content.

## Licence

MIT. "Korto" is a trademark of its owner and is used here only to say what this tool works with.
