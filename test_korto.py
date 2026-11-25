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
    """Serves fixtures instead of Korto, records what was asked and posted, and after a save serves
    the pages in `after` instead (what Korto would show once the change is in)."""
    pages = {
        ("yyriarvestus.naidud", None): "readings_2026_10.html",
        ("yyriarvestus.naidud", "3"): "readings_2026_10.html",
        ("yyriarvestus.naidud", "2"): "readings_2026_10.html",
        ("yyriarvestus.naidud", "1"): "readings_2026_09.html",
        ("yyriarvestus.tehingud", "2"): "expenses_missing.html",
        ("yyriarvestus.tehingud", "3"): "expenses_missing.html",
        ("yyriarvestus.maaramised", "2"): "summary.html",
        ("yyriarvestus.arved", "2"): "summary.html",
        ("yyriarvestus.naidud_muuda", "3"): "reading_form.html",
        ("yyriarvestus.perioodid_lukusta", "3"): "distribute.html",
        ("yyriarvestus.perioodid_vaata", "2"): "period_view.html",
        ("yyriarvestus.tehingud_form", "3"): "expense_form.html",
    }

    def __init__(self, after=None):
        self.requests, self.posts, self.after = [], [], after or {}

    def get(self, screen, **params):
        key = (screen, params.get("perioodi_id"))
        self.requests.append(key)
        return korto.ensure_logged_in(self.after[key] if self.posts and key in self.after else fixture(self.pages[key]))

    def post(self, fields):
        if ("action", "vali_tehing") in fields:  # picking an earlier invoice only fills the form
            return fixture("expense_prefilled.html")
        self.posts.append(fields)
        return ""


def run(*argv, after=None):
    client, out, err = FakeClient(after), io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = korto.main(list(argv), client=client)
    return code, out.getvalue(), err.getvalue(), client


def sent(fields, name):
    """Every value posted for a field; Korto (PHP) uses the last."""
    return [v for n, v in fields if n == name]


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


class Forms(unittest.TestCase):
    def test_a_form_sends_what_a_browser_would(self):
        form = korto.find_form(fixture("reading_form.html"), "naidud[0][arvesti_id]")
        fields = korto.payload(form)
        self.assertEqual(sent(fields, "naidud[1][nait]"), ["5.300", "5.300"])
        self.assertEqual(sent(fields, "naidud[0][prognoositud]"), ["f"])        # unticked checkbox: hidden default only
        self.assertEqual(sent(fields, "naidud[1][prognoositud]"), ["f", "t"])
        self.assertEqual(sent(fields, "note"), [])                             # disabled
        self.assertEqual(korto.value(form, "naidud[1][kuupaev]"), "30.11.2026")
        self.assertEqual(korto.value(korto.find_form(fixture("readings_2026_10.html"), "kuude_arv"), "kuude_arv"), "6")

    def test_press_runs_the_buttons_own_assignments(self):
        form = korto.find_form(fixture("reading_form.html"), "naidud[0][arvesti_id]")
        fields = korto.press(form, "Salvesta")  # the page's own button, not the dialog's of the same name
        self.assertEqual(sent(fields, "action"), ["salvesta"])
        self.assertEqual(sent(fields, "usage[amount]"), [""])  # an unopened dialog still sits in the form
        form = korto.find_form(fixture("readings_2026_10.html"), "kuude_arv")
        fields = korto.press(form, "Prognoosi")  # a dialog button: the other dialogs are disabled
        self.assertEqual((sent(fields, "action"), sent(fields, "note[text]")), (["prognoosi"], []))

    def test_unknown_fields_buttons_and_options_fail_loudly(self):
        form = korto.find_form(fixture("readings_2026_10.html"), "kuude_arv")
        for call in (lambda: korto.fill(form, "nope", "1"), lambda: korto.fill(form, "kuude_arv", "13"),
                     lambda: korto.press(form, "Nope"), lambda: korto.find_form("<form></form>", "kuude_arv")):
            with self.assertRaises(korto.UsageError):
                call()


SAVED = fixture("reading_form.html").replace('type="text" name="naidud[0][nait]" value=""', 'type="text" name="naidud[0][nait]" value="14.200"') \
    .replace('type="text" name="naidud[0][kuupaev]">', 'type="text" name="naidud[0][kuupaev]" value="30.11.2026">')


class Writes(unittest.TestCase):
    def test_dry_run_reads_but_never_posts(self):
        for argv in (("set-reading", "1", "Külm vesi", "14,2"), ("delete-reading", "1", "Küte"), ("forecast",),
                     ("distribute",), ("reopen", "--period", "2026-10"), ("add-expense", "--like", "Heat")):
            code, out, _, client = run(*argv)
            self.assertEqual((code, client.posts), (0, []), argv)
            self.assertIn("dry run: nothing was sent", out)

    def test_set_reading_posts_one_changed_row_and_keeps_every_reading(self):
        code, out, err, client = run("set-reading", "1", "Külm vesi", "14.2", "--apply", after={
            ("yyriarvestus.naidud_muuda", "3"): SAVED})
        self.assertEqual(code, 0, err)
        fields = client.posts[0]
        self.assertEqual([sent(fields, f"naidud[0][{k}]")[-1] for k in ("nait", "kuupaev", "kogus", "muudetud")],
                         ["14.200", "30.11.2026", "1.700", "t"])
        self.assertEqual(sent(fields, "action"), ["salvesta"])
        self.assertEqual(sent(fields, "naidud[1][salvestada]")[-1], "t")  # has a reading: unticking would delete it
        self.assertEqual(sent(fields, "naidud[2][salvestada]"), ["f"])     # empty: left out
        self.assertIn("1.700", out)

    def test_set_reading_reports_a_change_korto_did_not_take(self):
        code, _, err, _ = run("set-reading", "1", "Külm vesi", "14.2", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("Korto did not take the change", err)

    def test_set_reading_refuses_a_second_entry_and_bad_input(self):
        for argv, message in ((("Küte", "6", "--date", "29.11.2026"), "already has 5.300 dated 30.11.2026"),
                              (("vesi", "1"), "matches 2"), (("Küte", "6", "--date", "01.12.2026"), "--date must be"),
                              (("Küte", "six"), "not a number")):
            code, _, err, client = run("set-reading", "1", *argv, "--apply")
            self.assertEqual((code, client.posts), (2, []), argv)
            self.assertIn(message, err)
        code, _, err, _ = run("set-reading", "9", "Küte", "1")
        self.assertIn("no apartment 9", err)

    def test_writes_need_an_open_period(self):
        code, _, err, _ = run("set-reading", "--period", "2026-10", "1", "Küte", "6")
        self.assertEqual(code, 2)
        self.assertIn("only in an open period", err)

    def test_delete_reading_marks_only_that_row(self):
        _, _, _, client = run("delete-reading", "1", "Küte", "--apply")
        fields = client.posts[0]
        self.assertEqual(sent(fields, "action"), ["kustuta_margitud"])
        self.assertEqual([sent(fields, f"naidud[{i}][salvestada]")[-1] for i in range(3)], ["f", "t", "f"])

    def test_forecast_posts_months_date_and_action(self):
        code, out, _, client = run("forecast", "--months", "3", "--apply")
        fields = client.posts[0]
        self.assertEqual([sent(fields, k) for k in ("action", "kuude_arv", "kuupaev")], [["prognoosi"], ["3"], ["30.11.2026"]])
        self.assertIn("2 Külm vesi", out)
        self.assertEqual(code, 1)  # the fixture still shows the readings missing

    def test_distribute_uses_the_period_end_and_shows_kortos_warnings(self):
        after = {("yyriarvestus.naidud", None): fixture("readings_2026_10.html").replace("(avatud)", "(jaotatud)")}
        code, out, err, client = run("distribute", "--apply", after=after)
        self.assertEqual(code, 0, err)
        self.assertIn('Korto warns: Kululiigi "Elekter" kuluarve on sisestamata!', out)
        self.assertEqual(sent(client.posts[0], "jaotamise_kuupaev") + sent(client.posts[0], "action"), ["30.11.2026", "lukusta"])

    def test_reopen_never_loads_the_reopen_screen_in_a_dry_run(self):
        code, _, _, client = run("reopen", "--period", "2026-10")
        self.assertEqual(code, 0)
        self.assertNotIn("yyriarvestus.perioodid_taasava", [screen for screen, _ in client.requests])
        _, _, _, client = run("reopen", "--period", "2026-10", "--apply")
        self.assertEqual(sent(client.posts[0], "screen"), ["yyriarvestus.perioodid_taasava"])
        code, _, err, _ = run("reopen")
        self.assertEqual((code, "already open" in err), (2, True))

    def test_add_expense_redoes_the_sums_and_refuses_a_stale_total(self):
        code, out, _, client = run("add-expense", "--like", "heat", "read[0][kogus]=5", "tehing[arve_nr]=101",
                                   "tehing[makstud]=t", "--apply")
        fields = client.posts[0]
        self.assertEqual([sent(fields, k)[-1] for k in ("read[0][summa_kmta]", "read[0][summa_kmga]", "tehing[tasuda]",
                                                         "tehing[makstud]", "action")], ["250.00", "310.00", "310.00", "t", "salvesta"])
        self.assertEqual(code, 1)  # the fixture list does not have it afterwards
        code, out, _, client = run("add-expense", "--like", "Heat", "read[0][kogus]=5", "tehing[tasuda]=248.00", "--apply")
        self.assertEqual((code, client.posts), (2, []))
        self.assertIn("problem: tehing[tasuda] is 248.0, the rows add up to 310.00", out)
        code, _, err, _ = run("add-expense", "--like", "31.10.2026")
        self.assertEqual(code, 2)
        self.assertIn("--like must match one earlier invoice: 11 Heat Supplier", err)


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

    def test_a_redirect_after_a_post_is_a_save_but_one_to_the_login_page_is_expiry(self):
        def redirect(to):
            return mock.Mock(side_effect=urllib.error.HTTPError(korto.BASE, 302, "Found", {"Location": to}, None))
        client = korto.Client("s", "7")
        with mock.patch("time.sleep"):
            with mock.patch.object(korto._opener, "open", redirect("index.php?screen=yyriarvestus.naidud_muuda")):
                self.assertEqual(client.post([("action", "salvesta")]), "")
                with self.assertRaises(korto.UsageError):
                    client.get("yyriarvestus.perioodid")
            with mock.patch.object(korto._opener, "open", redirect("https://pro.korto.ee/index.php?screen=sisenemine.sisenemine")):
                with self.assertRaises(korto.SessionExpired):
                    client.post([("action", "salvesta")])

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
