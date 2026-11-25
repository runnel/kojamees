"""Offline tests. Fixtures are hand-written HTML with invented names and numbers."""
import contextlib
import datetime as dt
import io
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

import korto

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeClient:
    """Serves fixtures instead of Korto; records what was asked."""
    pages = {
        ("yyriarvestus.naidud", None): "readings_2026_10.html",
        ("yyriarvestus.naidud", "2"): "readings_2026_10.html",
        ("yyriarvestus.naidud", "1"): "readings_2026_09.html",
        ("yyriarvestus.tehingud", "2"): "expenses_missing.html",
        ("yyriarvestus.maaramised", "2"): "summary.html",
        ("yyriarvestus.arved", "2"): "summary.html",
    }

    def __init__(self):
        self.requests = []

    def get(self, screen, **params):
        self.requests.append((screen, params.get("perioodi_id")))
        return korto.ensure_logged_in(fixture(self.pages[(screen, params.get("perioodi_id"))]))


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = korto.main(list(argv), client=FakeClient())
    return code, out.getvalue()


class Parsing(unittest.TestCase):
    def test_periods(self):
        found = korto.periods(fixture("readings_2026_10.html"))
        self.assertEqual([p["name"] for p in found], ["2026 november", "2026 oktoober", "2026 september"])
        self.assertEqual(found[0], {"id": "3", "name": "2026 november", "state": "avatud"})

    def test_period_end(self):
        self.assertEqual(korto.period_end("2026 oktoober"), dt.date(2026, 10, 31))
        self.assertEqual(korto.period_end("2028 veebruar"), dt.date(2028, 2, 29))

    def test_readings(self):
        data = korto.readings(fixture("readings_2026_10.html"))
        heat = [b for b in data["building"] if b["unit"] == "MWh"]
        self.assertEqual(heat[0]["quantity"], 4.0)
        first = data["apartments"][0]
        self.assertEqual((first["apartment"], first["meter"], first["end"], first["quantity"]),
                         ("1", "Külm vesi", 12.5, 2.5))
        self.assertEqual((first["entered_by"], first["via_link"], first["entered_at"]),
                         ("Mari Maasikas", True, "30.10.2026 18:00"))
        self.assertTrue(data["apartments"][-1]["forecast"])
        self.assertIsNone(data["apartments"][2]["end"])

    def test_missing_invoices(self):
        self.assertEqual(korto.missing_invoices(fixture("expenses_missing.html")), ["Prügivedu", "Elekter"])
        self.assertEqual(korto.missing_invoices(fixture("expenses_one_missing.html")), ["Prügivedu"])
        self.assertEqual(korto.missing_invoices(fixture("summary.html")), [])

    def test_login_page_is_detected(self):
        with self.assertRaises(korto.SessionExpired):
            korto.ensure_logged_in(fixture("login.html"))


class Checks(unittest.TestCase):
    def setUp(self):
        current = korto.readings(fixture("readings_2026_10.html"))
        previous = korto.readings(fixture("readings_2026_09.html"))
        self.findings = korto.check(current, previous, dt.date(2026, 10, 31),
                                    korto.missing_invoices(fixture("expenses_missing.html")), {"4:Soe vesi"})

    def find(self, apartment, meter):
        return [(f["level"], f["message"]) for f in self.findings if (f["apartment"], f["meter"]) == (apartment, meter)]

    def test_reading_after_period_end_blocks(self):
        self.assertIn("block", [level for level, _ in self.find("1", "Küte")])

    def test_missing_reading_blocks(self):
        self.assertEqual(self.find("2", "Külm vesi"), [("block", "missing reading")])

    def test_more_than_building_blocks_and_jump_warns(self):
        messages = " ".join(m for _, m in self.find("2", "Küte"))
        self.assertIn("more than the whole building", messages)
        self.assertIn("over 5x", messages)

    def test_negative_and_round_numbers_warn(self):
        self.assertEqual(self.find("3", "Külm vesi")[0][0], "warn")
        self.assertIn("round number", self.find("3", "Küte")[0][1])

    def test_ignore_and_forecast_are_quiet(self):
        self.assertEqual(self.find("4", "Soe vesi"), [])
        self.assertEqual(self.find("4", "Küte"), [])
        self.assertEqual(self.find("1", "Külm vesi"), [])

    def test_missing_invoices_block(self):
        invoices = {f["meter"] for f in self.findings if f["apartment"] == "" and f["level"] == "block"}
        self.assertEqual(invoices, {"Prügivedu", "Elekter"})


class CommandLine(unittest.TestCase):
    def test_check_exit_code_and_previous_period(self):
        code, out = run("check", "--period", "2026-10", "--ignore", "4:Soe vesi")
        self.assertEqual(code, 1)
        self.assertIn("5 blocking, 4 warnings", out)

    def test_json_output(self):
        code, out = run("--json", "readings", "--period", "2")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["apartments"]), 8)

    def test_default_period_is_the_open_one(self):
        with self.assertRaises(KeyError):  # the open period (id 3) has no fixture: proves it was chosen
            run("readings")

    def test_summary(self):
        code, out = run("summary", "--period", "2026-10")
        self.assertEqual(code, 0)
        self.assertIn("1. Mari Maasikas | 10.00 | 10.00 | Näita", out)
        self.assertIn("Korter kokku", out)  # <br> inside a cell becomes a space

    def test_expired_session_exits_3_without_printing_the_cookie(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("KORTO_PHPSESSID='secret-value'\nKORTO_UNION_ID=1\n")
        login = io.BytesIO(fixture("login.html").encode())
        login.__enter__ = lambda: login
        login.__exit__ = lambda *a: None
        out, err = io.StringIO(), io.StringIO()
        try:
            with mock.patch.dict("os.environ", {}, clear=True), \
                 mock.patch("urllib.request.urlopen", return_value=login) as urlopen, \
                 contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = korto.main(["--env", f.name, "periods"])
        finally:
            os.unlink(f.name)
        self.assertEqual(code, 3)
        self.assertIn("session has expired", err.getvalue())
        self.assertNotIn("secret-value", out.getvalue() + err.getvalue())
        request = urlopen.call_args[0][0]
        self.assertEqual(request.get_header("Cookie"), "PHPSESSID=secret-value")
        self.assertTrue(request.get_header("User-agent").startswith("korto-cli/"))


if __name__ == "__main__":
    unittest.main()
