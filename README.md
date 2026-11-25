# korto-cli

**Unofficial command-line companion for [Korto Pro](https://korto.ee). Not affiliated with
or endorsed by OÜ Tarkvaralabor.**

Korto Pro is the web application many Estonian apartment associations (korteriühistud)
use for meter readings, expenses and monthly invoices. It has no administrator API. The
monthly routine — check the readings, enter expense invoices, forecast missing readings,
distribute costs — is the same every month and slow to click through.

`korto-cli` does the reading and checking part with plain HTTP requests, using **your own
logged-in session**, at human pace. It does nothing you could not do by hand in the web UI.

> Read [Terms, law and data](#terms-law-and-data) before you use it.

## What it does (version 0.1)

| Command | What you get |
|---|---|
| `korto periods` | periods with id and state (`avatud`, `kinnitatud`, `suletud` …) |
| `korto readings [--period ID\|YYYY-MM]` | building meters and every apartment meter: start, end, date, quantity, forecast flag, who entered it and whether through the resident link |
| `korto check [--period …] [--ignore APT:METER …]` | mistakes to fix before distributing costs (exit code 1 if any blocking) |
| `korto summary [--period …]` | the distribution table and the invoice table |

Every command accepts `--json`. Without `--period` the open period is used.

`korto check` looks for:

- **block** — a reading dated after the period end (it will not count for the period);
- **block** — an apartment heat reading larger than the whole building's heat meter;
- **block** — a missing reading (use `--ignore` for meters that are known to have none);
- **block** — cost types Korto itself reports as having no expense invoice;
- **warn** — consumption over 5× the previous period;
- **warn** — negative consumption (normal right after a forecast);
- **warn** — a round heat reading such as `1.000 MWh`, which is usually an estimate.

Each rule comes from a real mistake that went unnoticed in the web UI.

Writing (entering readings, expense invoices, forecast, distribution) is planned for
version 0.2, dry-run by default. See [docs/DESIGN.md](docs/DESIGN.md).

## Requirements

Python 3.10 or newer, standard library only. Linux, macOS and Windows. Nothing to install:
download `korto.py`.

## Configuration

Two values, from the environment or from a file you pass with `--env` (or `$KORTO_ENV`):

```
KORTO_PHPSESSID=…   # your Korto session cookie
KORTO_UNION_ID=…    # your association id: yhistu_id=… in Korto URLs
```

To get the session cookie, log in to Korto Pro in your browser, open the developer
console (Chrome: Cmd+Option+J / Ctrl+Shift+J) and run:

```js
document.cookie.match(/PHPSESSID=([^;]+)/)[1]
```

**Treat the cookie like a password.** Keep the file outside any repository. `korto-cli`
never prints it. Logging out in the browser ends the session for `korto-cli` too; when it
expires the tool exits with code 3 and asks for a new one.

## Usage

```
$ python3 korto.py --env ~/.korto.env check --period 2026-10 --ignore "4:Soe vesi"
level  apartment  meter      message
block  1          Küte       dated 02.11.2026, after the period end 31.10.2026
block  2          Külm vesi  missing reading
block  2          Küte       9.000 MWh is more than the whole building (4.000 MWh)
…
2026 oktoober: 5 blocking, 4 warnings
```

(Example output from the test fixtures; names and numbers are invented.)

## Terms, law and data

`korto-cli` is built to stay inside [Korto's terms of use](https://korto.ee/public/terms)
and the law. This is our reading, not legal advice. If you use the tool, you are
responsible for using it the same way.

- **Intended use only (terms 3.3).** It performs actions the web UI offers to a logged-in
  administrator, for that administrator's own association. No crawling, no bulk export, no
  access to other associations.
- **No disruption (terms 4.1).** One request at a time, at least one second apart, no retry
  loops. Every request says who it is: `User-Agent: korto-cli/<version>`. It is not a
  crawler and acts only on your explicit command.
- **Your own session.** It uses the session cookie you supply. It never logs in, never
  stores a password, never prints the cookie.
- **No Korto content here (terms 11).** This repository contains no copies of Korto pages,
  scripts, texts or images. Test fixtures are small HTML files written by hand.
- **No personal data here (GDPR).** No names, addresses, readings, invoices or ids of real
  people or associations — not in code, tests, issues or pull requests. Your association is
  the data controller for what the tool reads; the tool stores nothing.
- **The operator decides.** Korto may end a subscription that breaches its terms
  (terms 10.3). If OÜ Tarkvaralabor asks us to change or stop something, or offers an API,
  we will follow it — please open an issue or get in touch.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Small, readable, standard library only.

## Licence

MIT — see [LICENSE](LICENSE). "Korto" is a trademark of its owner and is used here only to
say what this tool works with.
