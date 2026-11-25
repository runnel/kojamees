# Contributing

Thank you for helping. Three rules matter more than any feature:

1. **No real data, anywhere.** No names, addresses, apartment or meter numbers, readings,
   invoices, session cookies or ids of real people or associations — not in code, test
   fixtures, issues, pull requests or screenshots. Make up your examples.
2. **No Korto content.** Do not commit pages, HTML, scripts, texts or images copied from
   Korto. Fixtures are minimal HTML written by hand that only mimics the structure the
   parser needs.
3. **Stay inside the terms.** Read "Terms, law and data" in the README. One request at a
   time, at least one second apart, only for an explicit user command, only what the web UI
   allows the logged-in user to do. No crawling, no bulk export, no retry loops.

## Code

- Python 3.10+, standard library only, one file (`korto.py`). If a change needs a
  dependency or a second module, open an issue first.
- Keep it minimal: Korto pages and forms only. Knowledge about a particular association
  (suppliers, apartments, pricing, exceptions) belongs in the user's own notes and
  arguments, not in the code.
- Commands that change data in Korto must be dry runs by default, show exactly what they
  would send, need `--apply`, and read the result back to verify it.
- Run the tests: `python3 -m unittest -v`. They run offline.
