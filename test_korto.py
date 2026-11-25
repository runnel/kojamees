"""Offline tests. Fixtures are hand-written HTML with invented names and numbers."""
import contextlib
import datetime as dt
import io
import json
import os
import pathlib
import tempfile
import unittest
import urllib.error
from unittest import mock

import korto

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeClient:
    """Serves fixtures instead of Korto and records what was asked."""
    pages = {
        ("yyriarvestus.naidud", None): "readings_2026_10.html",
        ("yyriarvestus.naidud", "3"): "readings_2026_10.html",
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
    client, out, err = FakeClient(), io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = korto.main(list(argv), client=client)
    return code, out.getvalue(), err.getvalue(), client


class Parsing(unittest.TestCase):
    def test_periods(self):
        found = korto.periods(fixture("readings_2026_10.html"))
        self.assertEqual([p["name"] for p in found], ["2026 november", "2026 oktoober", "2026 september"])
        self.assertEqual(found[0], {"id": "3", "name": "2026 november", "state": "avatud"})

    def test_period_end(self):
        self.assertEqual(korto.period_end("2026 oktoober"), dt.date(2026, 10, 31))
        self.assertEqual(korto.period_end("2028 veebruar"), dt.date(2028, 2, 29))
        with self.assertRaises(korto.UsageError):
            korto.period_end("2026 I kvartal")

    def test_readings_in_open_period_layout(self):  # the October fixture has the leading checkbox column
        data = korto.readings(fixture("readings_2026_10.html"))
        self.assertEqual([b["quantity"] for b in data["building"] if b["unit"] == "MWh"], [4.0])
        first = data["apartments"][0]
        self.assertEqual((first["apartment"], first["meter"], first["meter_id"], first["end"], first["quantity"]),
                         ("1", "Külm vesi", "101", 12.5, 2.5))
        self.assertEqual((first["entered_by"], first["via_link"], first["entered_at"]),
                         ("Mari Maasikas", True, "30.10.2026 18:00"))
        self.assertTrue(data["apartments"][7]["forecast"])
        self.assertIsNone(data["apartments"][2]["end"])

    def test_unknown_layout_fails_loudly(self):
        renamed_input = fixture("readings_2026_10.html").replace("korterite_arvestid[", "renamed[")
        renamed_table = fixture("readings_2026_10.html").replace('id="korterite_arvestid"', 'id="renamed"')
        for page in (renamed_input, renamed_table):
            with self.assertRaises(korto.UsageError):
                korto.readings(page)

    def test_tables_survive_nested_tables_unclosed_cells_and_line_breaks(self):
        t = korto.tables('<table id="a"><tr><td>x<table><tr><td>in</td></tr></table>after<input name="n">'
                         '<td>open<br>cell</table><p>footer</p>')
        row = t["a"][0]
        self.assertEqual((row[0]["text"], row[0]["inputs"]), ("x after", [("n", "")]))
        self.assertEqual(row[1]["text"], "open cell")

    def test_missing_invoices(self):
        self.assertEqual(korto.missing_invoices(fixture("expenses_missing.html")), ["Prügivedu", "Elekter"])
        self.assertEqual(korto.missing_invoices(fixture("expenses_one_missing.html")), ["Prügivedu"])
        self.assertEqual(korto.missing_invoices(fixture("summary.html")), [])

    def test_login_page_is_detected(self):
        with self.assertRaises(korto.SessionExpired):
            korto.ensure_logged_in(fixture("login.html"))


class Checks(unittest.TestCase):
    def setUp(self):
        self.current = korto.readings(fixture("readings_2026_10.html"))
        self.previous = korto.readings(fixture("readings_2026_09.html"))
        self.findings = korto.check(self.current, self.previous, dt.date(2026, 10, 31),
                                    korto.missing_invoices(fixture("expenses_missing.html")), {"4:Soe vesi"})

    def find(self, apartment, meter):
        return [(f["level"], f["message"]) for f in self.findings if (f["apartment"], f["meter"]) == (apartment, meter)]

    def test_reading_after_period_end_blocks(self):
        self.assertEqual(self.find("1", "Küte"), [("block", "dated 02.11.2026, after the period end 31.10.2026")])

    def test_missing_reading_blocks(self):
        self.assertEqual(self.find("2", "Külm vesi"), [("block", "missing reading")])

    def test_more_than_building_blocks_and_jump_warns(self):
        self.assertEqual([level for level, _ in self.find("2", "Küte")], ["block", "warn", "warn"])
        self.assertIn("more than the whole building", self.find("2", "Küte")[0][1])
        self.assertIn("over 5x", self.find("2", "Küte")[1][1])

    def test_negative_and_round_numbers_warn(self):
        self.assertEqual(self.find("3", "Külm vesi")[0][0], "warn")
        self.assertIn("round number", self.find("3", "Küte")[0][1])

    def test_forecast_ignore_and_small_jumps_are_quiet(self):
        self.assertEqual(self.find("4", "Küte"), [])       # round 1.000, but a forecast
        self.assertEqual(self.find("4", "Soe vesi"), [])   # ignored
        self.assertEqual(self.find("5", "Soe vesi"), [])   # 8x the previous, but only 0.35 more
        self.assertEqual(self.find("1", "Külm vesi"), [])

    def test_missing_invoices_block(self):
        invoices = {f["meter"] for f in self.findings if f["apartment"] == "" and f["level"] == "block"}
        self.assertEqual(invoices, {"Prügivedu", "Elekter"})

    def test_missing_building_heat_is_reported_not_silent(self):
        current = dict(self.current, building=[])
        findings = korto.check(current, self.previous, dt.date(2026, 10, 31))
        self.assertIn("building heat", [f["meter"] for f in findings])

    def test_building_heat_uses_the_largest_positive_meter(self):
        current = dict(self.current, apartments=[dict(self.current["apartments"][1], quantity=3.0, date="31.10.2026")])
        two_meters = dict(current, building=[{"unit": "MWh", "quantity": 2.0}, {"unit": "MWh", "quantity": 4.0}])
        negative = dict(current, building=[{"unit": "MWh", "quantity": -0.2}])
        for case in (two_meters, negative):
            messages = [f["message"] for f in korto.check(case, self.previous, dt.date(2026, 10, 31))]
            self.assertFalse(any("whole building" in m for m in messages), messages)

    def test_previous_period_is_matched_by_meter_id(self):
        renamed = {"building": [], "apartments": [dict(r, meter="Renamed") for r in self.previous["apartments"]]}
        findings = korto.check(self.current, renamed, dt.date(2026, 10, 31))
        self.assertTrue(any("over 5x" in f["message"] for f in findings))


class CommandLine(unittest.TestCase):
    def test_check_exit_code_and_pages_read(self):
        code, out, _, client = run("check", "--period", "2026-10", "--ignore", "4:Soe vesi")
        self.assertEqual(code, 1)
        self.assertIn("5 blocking, 4 warnings", out)
        self.assertIn(("yyriarvestus.naidud", "1"), client.requests)     # the previous period
        self.assertIn(("yyriarvestus.tehingud", "2"), client.requests)   # Korto's missing-invoice warning

    def test_json_after_the_subcommand(self):
        code, out, _, _ = run("readings", "--period", "2", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["apartments"]), 9)

    def test_default_period_is_the_open_one(self):
        _, _, _, client = run("readings")
        self.assertEqual(client.requests[-1], ("yyriarvestus.naidud", "3"))

    def test_bad_period_is_a_usage_error(self):
        for period in ("2026-13", "2026-00", "999"):
            code, _, err, _ = run("readings", "--period", period)
            self.assertEqual((code, "period not found" in err), (2, True))

    def test_summary(self):
        code, out, _, _ = run("summary", "--period", "2026-10")
        self.assertEqual(code, 0)
        self.assertIn("1. Mari Maasikas | 10.00 | Show", out)
        self.assertIn("Total due", out)  # <br> inside a cell becomes a space


class Transport(unittest.TestCase):
    def main_with_env(self, env_text, opener):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8-sig") as f:
            f.write(env_text)
        out, err = io.StringIO(), io.StringIO()
        try:
            with mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(korto._opener, "open", opener), \
                 mock.patch("time.sleep"), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = korto.main(["--env", f.name, "periods"])
                except SystemExit as exit_:  # argparse errors
                    code = exit_.code
        finally:
            os.unlink(f.name)
        return code, out.getvalue() + err.getvalue()

    def test_env_file_with_bom_and_comments_and_the_cookie_is_never_printed(self):
        opener = mock.Mock(return_value=io.BytesIO(fixture("login.html").encode()))
        code, output = self.main_with_env("KORTO_PHPSESSID='secret1' # from the browser\nKORTO_UNION_ID=7\n", opener)
        self.assertEqual(code, 3)
        self.assertNotIn("secret1", output)
        request = opener.call_args[0][0]
        self.assertEqual(request.get_header("Cookie"), "PHPSESSID=secret1")
        self.assertTrue(request.get_header("User-agent").startswith("korto-cli/"))
        self.assertIn("yhistu_id=7", request.full_url)

    def test_redirect_means_expired_and_is_not_followed(self):
        redirect = urllib.error.HTTPError("https://pro.korto.ee/", 302, "Found", {}, None)
        code, output = self.main_with_env("KORTO_PHPSESSID=secret2\nKORTO_UNION_ID=7\n", mock.Mock(side_effect=redirect))
        self.assertEqual(code, 3)
        self.assertNotIn("secret2", output)
        self.assertTrue(any(isinstance(h, korto._NoRedirect) for h in korto._opener.handlers))

    def test_malformed_cookie_and_missing_env_file_are_usage_errors(self):
        opener = mock.Mock()
        code, output = self.main_with_env("KORTO_PHPSESSID=bad value\nKORTO_UNION_ID=7\n", opener)
        self.assertEqual(code, 2)
        self.assertIn("KORTO_PHPSESSID", output)
        self.assertNotIn("bad value", output)
        opener.assert_not_called()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            korto.main(["--env", "/nonexistent/korto.env", "periods"])
        self.assertEqual(caught.exception.code, 2)

    def test_requests_are_at_least_a_second_apart(self):
        page = fixture("readings_2026_10.html").encode()
        with mock.patch.object(korto._opener, "open", side_effect=lambda *a, **k: io.BytesIO(page)), \
             mock.patch("time.sleep") as sleep:
            client = korto.Client("s", "7")
            client.get("a")
            client.get("b")
        self.assertGreater(sleep.call_args_list[1][0][0], 0.9)


if __name__ == "__main__":
    unittest.main()
