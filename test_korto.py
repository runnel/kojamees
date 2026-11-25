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
import urllib.parse
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
        ("yyriarvestus.perioodid_vaata", "1"): "period_closed.html",
        ("yyriarvestus.laekumised", "1"): "payments.html",
        ("yyriarvestus.arved", "1"): "invoices.html",
        ("yyriarvestus.laekumised_import_fail", "3"): "import_upload.html",
        ("yyriarvestus.laekumised_import_andmed", "3"): "import_preview.html",
        ("yyriarvestus.laekumised", "3"): "summary.html",  # no payments yet
        ("yyriarvestus.tehingud_form", "3"): "expense_form.html",
        ("yyriarvestus.kululiigid", "3"): "cost_types.html",
        ("yyriarvestus.kululiigid_form", "3"): "cost_type_form.html",
        ("yyriarvestus.perioodid_form", "0"): "period_form.html",
        ("yyriarvestus.naidud_ajalugu", "3"): "history.html",
        ("yyriarvestus.saldod", "2"): "balances.html",
        ("yyriarvestus.laekumised", "2"): "payments.html",
    }

    union_id = "7"

    def __init__(self, after=None, pages=None, stamp="20261029"):
        self.requests, self.posts, self.prefills, self.after, self.html = [], [], [], after or {}, pages or {}
        self.stamp = stamp

    def get(self, screen, **params):
        key = (screen, params.get("perioodi_id"))
        self.requests.append(key)
        html = self.after[key] if self.posts and key in self.after else self.html.get(key) or fixture(self.pages[key])
        return korto.ensure_logged_in(html)

    def upload(self, fields, name, filename, data):
        urllib.parse.urlencode(fields)
        self.prefills.append(list(fields) + [(name, filename)])  # Korto keeps the preview and saves nothing
        return ""

    def post(self, fields, raw=False):
        urllib.parse.urlencode(fields)  # refuses what the real Client would refuse
        if raw:
            self.prefills.append(fields)  # a download
            return self.html.get("pdf") or ("application/pdf", b"%PDF-1.7 invoice " + dict(fields)["arve_id"].encode())
        if ("action", "vali_tehing") in fields:  # picking an earlier invoice only fills the form
            self.prefills.append(fields)
            return fixture("expense_prefilled.html")
        steps = {"yyriarvestus.perioodid_sulge": "closing", "yyriarvestus.perioodid_tyhista_sulgemine": "unclosing"}
        if dict(fields).get("screen") in steps and not dict(fields).get("action"):
            self.prefills.append(fields)  # the period page's button only opens Korto's confirmation page
            name = steps[dict(fields)["screen"]]
            return self.html.get(name) or fixture(f"{name}.html")
        self.posts.append(fields)
        return ""


def run(*argv, after=None, pages=None, stamp="20261029"):
    client, out, err = FakeClient(after, pages, stamp), io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = korto.main(list(argv), client=client)
    return code, out.getvalue(), err.getvalue(), client


BASELINE = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name


def setUpModule():  # every --apply in these tests passes the baseline guard, as a user's would after selftest
    os.environ["KORTO_BASELINE"] = BASELINE
    os.unlink(BASELINE)
    code, out, err, _ = run("selftest", "--accept")
    assert code == 0, out + err


def tearDownModule():
    os.environ.pop("KORTO_BASELINE", None)
    if os.path.exists(BASELINE):
        os.unlink(BASELINE)


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
        odd = korto.forms('<form><input type="hidden" name="action"><button disabled onclick="this.form.elements'
                          '[\'action\'].value = \'go\';">Go</button><button onclick="submitIt()">Odd</button></form>')[0]
        for call in (lambda: korto.fill(form, "nope", "1"), lambda: korto.fill(form, "kuude_arv", "13"),
                     lambda: korto.fill(form, "kuude_arv", True), lambda: korto.press(form, "Nope"),
                     lambda: korto.find_form("<form></form>", "kuude_arv"), lambda: korto.press(odd, "Go"),
                     lambda: korto.press(odd, "Odd")):
            with self.assertRaises(korto.UsageError):
                call()


LISTED = ('<select name="perioodi_id"></select><table><tr><td>30.11.2026</td><td>Heat Supplier</td><td>kulu</td>'
          '<td>101</td><td>310.00</td></tr></table>')
SAVED = fixture("reading_form.html").replace('type="text" name="naidud[0][nait]" value=""', 'type="text" name="naidud[0][nait]" value="14.200"') \
    .replace('type="text" name="naidud[0][kuupaev]">', 'type="text" name="naidud[0][kuupaev]" value="30.11.2026">')


class Writes(unittest.TestCase):
    def test_dry_run_reads_but_never_posts(self):
        for argv in (("set-reading", "1", "Külm vesi", "14,2"), ("delete-reading", "1", "Küte"), ("forecast",),
                     ("distribute",), ("reopen", "--period", "2026-10"), ("add-expense", "--like", "Heat")):
            code, out, _, client = run(*argv)
            self.assertEqual((code, client.posts), (0, []), argv)
            self.assertIn("dry run: nothing was saved", out)
        self.assertEqual(len(client.prefills), 1)  # add-expense asks Korto to prefill its form, as the web UI does

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
        hidden_only = fixture("reading_form.html").replace('type="hidden" name="naidud[2][nait]" value=""',
                                                           'type="hidden" name="naidud[2][nait]" value="3.100"')
        _, _, _, client = run("set-reading", "1", "Külm vesi", "14.2", "--apply",
                              pages={("yyriarvestus.naidud_muuda", "3"): hidden_only})
        self.assertEqual(sent(client.posts[0], "naidud[2][salvestada]")[-1], "t")  # any sign of a reading keeps it
        _, _, _, client = run("set-reading", "1", "Külm vesi", "14.2", "--forecast", "--apply")
        self.assertEqual(sent(client.posts[0], "naidud[0][prognoositud]"), ["f", "t"])

    def test_set_reading_reports_a_change_korto_did_not_take(self):
        code, _, err, _ = run("set-reading", "1", "Külm vesi", "14.2", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("Korto does not show the change as sent", err)
        harm = SAVED.replace('value="5.300"', 'value="5.000"')  # another meter's reading changed too
        code, _, err, _ = run("set-reading", "1", "Külm vesi", "14.2", "--apply", after={("yyriarvestus.naidud_muuda", "3"): harm})
        self.assertEqual(code, 1)
        self.assertIn("it also changed Street 1-1 Küte", err)
        with mock.patch.object(FakeClient, "post", side_effect=OSError("timed out")):
            code, _, err, _ = run("set-reading", "1", "Külm vesi", "14.2", "--apply")
        self.assertEqual(code, 4)
        self.assertIn("may or may not be in Korto", err)

    def test_set_reading_refuses_a_second_entry_and_bad_input(self):
        for argv, message in ((("Küte", "6", "--date", "29.11.2026"), "already has 5.300 dated 30.11.2026"),
                              (("vesi", "1"), "matches 2"), (("Küte", "6", "--date", "01.12.2026"), "--date must be"),
                              (("Küte", "six"), "not a number")):
            code, _, err, client = run("set-reading", "1", *argv, "--apply")
            self.assertEqual((code, client.posts), (2, []), argv)
            self.assertIn(message, err)
        code, _, err, _ = run("set-reading", "9", "Küte", "1")
        self.assertIn("no apartment 9", err)
        twice = fixture("readings_2026_10.html").replace('korterite_arvestid[1][]" value="102"', 'korterite_arvestid[77][]" value="102"')
        code, _, err, _ = run("set-reading", "1", "Küte", "1", pages={("yyriarvestus.naidud", "3"): twice})
        self.assertEqual(code, 2)
        self.assertIn("more than one with that number", err)

    def test_writes_need_an_open_period(self):
        code, _, err, _ = run("set-reading", "--period", "2026-10", "1", "Küte", "6")
        self.assertEqual(code, 2)
        self.assertIn("only in an open period", err)

    def test_delete_reading_marks_only_that_row(self):
        code, _, _, client = run("delete-reading", "1", "Küte", "--apply")
        fields = client.posts[0]
        self.assertEqual(sent(fields, "action"), ["kustuta_margitud"])
        self.assertEqual([sent(fields, f"naidud[{i}][salvestada]")[-1] for i in range(3)], ["f", "t", "f"])
        self.assertEqual(code, 1)  # the fixture still shows it
        earlier = fixture("reading_form.html").replace('value="5.300"', 'value="5.200"').replace('value="30.11.2026"', 'value="29.11.2026"')
        code, out, _, _ = run("delete-reading", "1", "Küte", "--apply", after={("yyriarvestus.naidud_muuda", "3"): earlier})
        self.assertEqual(code, 0)
        self.assertIn("Korto now uses an earlier entry: 5.200 dated 29.11.2026", out)

    def test_forecast_posts_months_date_and_action(self):
        code, out, _, client = run("forecast", "--months", "3", "--apply")
        fields = client.posts[0]
        self.assertEqual([sent(fields, k) for k in ("action", "kuude_arv", "kuupaev")], [["prognoosi"], ["3"], ["30.11.2026"]])
        self.assertEqual(sent(fields, "note[text]"), [])  # the other dialog, with its own kuupaev, is not sent
        harm = {("yyriarvestus.naidud", "3"): fixture("readings_2026_10.html").replace(">5.300<", ">5.400<")}
        code, _, err, _ = run("forecast", "--apply", after=harm)
        self.assertEqual(code, 1)
        self.assertIn("it also changed 1 Küte", err)
        self.assertIn("2 Külm vesi", out)
        self.assertEqual(code, 1)  # the fixture still shows the readings missing

    def test_distribute_uses_the_period_end_and_shows_kortos_warnings(self):
        after = {("yyriarvestus.naidud", None): fixture("readings_2026_10.html").replace("(avatud)", "(jaotatud)")}
        code, out, err, client = run("distribute", "--apply", after=after)
        self.assertEqual(code, 0, err)
        self.assertIn('Korto warns: Kululiigi "Elekter" kuluarve on sisestamata!', out)
        self.assertEqual(sent(client.posts[0], "jaotamise_kuupaev") + sent(client.posts[0], "action"), ["30.11.2026", "lukusta"])

    def test_reopen_never_loads_the_reopen_screen_in_a_dry_run(self):
        code, out, _, client = run("reopen", "--period", "2026-10")
        self.assertEqual(code, 0)
        self.assertNotIn("yyriarvestus.perioodid_taasava", [screen for screen, _ in client.requests])
        self.assertIn("Korto form: yyriarvestus.perioodid_taasava", out)  # the dry run says what would be posted
        code, _, _, client = run("reopen", "--period", "2026-10", "--apply")
        self.assertEqual((code, sent(client.posts[0], "screen")), (1, ["yyriarvestus.perioodid_taasava"]))
        opened = {("yyriarvestus.naidud", None): fixture("readings_2026_10.html").replace("(jaotatud)", "(avatud)")}
        self.assertEqual(run("reopen", "--period", "2026-10", "--apply", after=opened)[0], 0)
        confirmed = {("yyriarvestus.naidud", None): fixture("readings_2026_10.html").replace("(jaotatud)", "(kinnitatud)")}
        for argv, pages in ((("reopen",), None), (("reopen", "--period", "2026-09"), None), (("reopen", "--period", "2026-10"), confirmed)):
            code, _, err, _ = run(*argv, pages=pages)  # open; closed; invoices confirmed
            self.assertEqual((code, "only a distributed period" in err), (2, True), argv)

    def test_add_expense_redoes_the_sums_and_refuses_a_stale_total(self):
        code, out, err, client = run("add-expense", "--like", "heat", "read[0][kogus]=5", "tehing[arve_nr]=101",
                                     "tehing[makstud]=t", "--apply", after={("yyriarvestus.tehingud", "3"): LISTED})
        self.assertEqual(code, 0, out + err)
        fields = client.posts[0]
        self.assertEqual([sent(fields, k)[-1] for k in ("read[0][summa_kmta]", "read[0][summa_kmga]", "tehing[tasuda]",
                                                         "tehing[makstud]", "action")], ["250.00", "310.00", "310.00", "t", "salvesta"])
        _, _, _, client = run("add-expense", "--like", "11", "read[0][summa_kmta]=100.00", "tehing[makstud]=f", "--apply")
        fields = client.posts[0]  # a new net amount: the unit price follows; an unticked box sends its hidden 'f'
        self.assertEqual([sent(fields, k)[-1] for k in ("read[0][hind]", "read[0][summa_kmga]", "tehing[tasuda]")],
                         ["25.000", "124.00", "124.00"])
        self.assertEqual(sent(fields, "tehing[makstud]"), ["f"])
        code, out, _, client = run("add-expense", "--like", "Heat", "read[0][kogus]=5", "tehing[tasuda]=248.00", "--apply")
        self.assertEqual((code, client.posts), (2, []))
        self.assertIn("problem: tehing[tasuda] is 248.00, the rows add up to 310.00", out)
        code, out, _, client = run("add-expense", "--like", "Heat", "tehing[arve_nr]=101", "--apply",
                                   pages={("yyriarvestus.tehingud", "3"): LISTED})
        self.assertEqual((code, client.posts), (2, []))
        self.assertIn("Heat Supplier 101 is already in 2026 november", out)
        code, out, _, client = run("add-expense", "--like", "Heat", "read[0][kogus]=5", "--apply",  # no number: by amount
                                   pages={("yyriarvestus.tehingud", "3"): LISTED})
        self.assertIn("Heat Supplier 310.00 is already in 2026 november", out)

    def test_add_expense_rounds_half_up_and_checks_vat_and_total(self):
        _, out, _, _ = run("add-expense", "--like", "Heat", "read[0][kogus]=0.5", "read[0][hind]=0.25")
        self.assertIn("0.5 x 0.25 = 0.13 + 24% = 0.16", out)  # 0.125 rounds up, as on an invoice
        _, out, _, _ = run("add-expense", "--like", "Heat", "read[0][kogus]=12345.678", "read[0][summa_kmta]=1500.00")
        self.assertNotIn("problem", out)  # quantity and net amount from the invoice: the derived price is not checked
        for fields, problem in ((("read[0][summa_kmga]=300.00",), "summa_kmta with VAT is 248.00"),
                                (("read[0][hind]=50", "read[0][summa_kmta]=201.00"), "kogus x hind is 200.00"),
                                (("tehing[tasuda]=248.06",), "tehing[tasuda] is 248.06")):
            code, out, _, _ = run("add-expense", "--like", "Heat", *fields)
            self.assertEqual(code, 2, fields)
            self.assertIn(problem, out)
        self.assertEqual(run("add-expense", "--like", "Heat", "tehing[tasuda]=248.05")[0], 0)  # invoice rounding
        code, _, err, _ = run("add-expense", "--like", "12")  # Korto prefilled another partner's invoice
        self.assertEqual(code, 2)
        self.assertIn("Korto did not fill the form from Waste Co", err)
        code, _, err, _ = run("add-expense", "--like", "31.10.2026")
        self.assertEqual(code, 2)
        self.assertIn("--like must match one earlier invoice: 11 Heat Supplier", err)


class Wave3(unittest.TestCase):
    def test_table_and_history_read_without_script_text(self):
        code, out, _, _ = run("table", "balances", "--period", "2026-10")
        self.assertEqual(code, 0)
        self.assertIn("1. Mari Maasikas | 10.00 | 12.00 | 22.00", out)  # the table with the expected column, not the largest
        code, out, _, _ = run("table", "payments", "--period", "2026-10")
        self.assertIn("24.10.2026 | 2 | Jaan Tamm | 20.00", out)
        code, _, err, _ = run("table", "payments", "--period", "2026-10",
                              pages={("yyriarvestus.laekumised", "2"): fixture("summary.html")})
        self.assertEqual((code, "no payments table with a 'Maksja' column" in err), (2, True))
        code, out, _, client = run("history", "1", "Küte", "--json")
        rows = json.loads(out[out.index("["):])
        self.assertEqual([(r["date"], r["reading"], r["forecast"]) for r in rows],
                         [("31.10.2026", "5.000", ""), ("30.09.2026", "4.700", "*")])  # the script's date is not a row
        self.assertIn(("yyriarvestus.naidud_ajalugu", "3"), client.requests)

    def test_set_price_posts_the_price_at_the_cost_types_precision(self):
        after = {("yyriarvestus.kululiigid_form", "3"): fixture("cost_type_form.html").replace('value="60.000"', 'value="61.235"')}
        code, out, err, client = run("set-price", "041", "61,235", "--apply", after=after)
        self.assertEqual(code, 0, err)
        self.assertEqual(sent(client.posts[0], "kululiik[fikseeritud_hind]"), ["61.235"])
        self.assertEqual(sent(client.posts[0], "kululiik[hinna_reegel]"), ["H"])  # the rest of the form travels as it is
        self.assertIn("60.000", out)
        code, _, err, client = run("set-price", "041", "61.2345", "--apply")
        self.assertEqual((code, client.posts, "more decimals than 041 keeps (3)" in err), (2, [], True))
        moved = {("yyriarvestus.kululiigid_form", "3"): after[("yyriarvestus.kululiigid_form", "3")].replace('value="Heating"', 'value="Hot"')}
        code, _, err, _ = run("set-price", "041", "61.235", "--apply", after=moved)
        self.assertEqual((code, "it also changed kululiik[nimetus]" in err), (1, True))
        for argv, message in ((("099", "1"), "0 cost types 099"), (("041", "-1"), "not a price"), (("041", "1e3"), "not a price"),
                              (("041", "nan"), "not a price")):
            code, _, err, client = run("set-price", *argv, "--apply")
            self.assertEqual((code, client.posts), (2, []), argv)
            self.assertIn(message, err)
        twice = {("yyriarvestus.kululiigid", "3"): fixture("cost_types.html").replace("902\">050", "902\">041")}
        code, _, err, client = run("set-price", "041", "1", "--apply", pages=twice)
        self.assertEqual((code, client.posts, "2 cost types 041" in err), (2, [], True))
        twice_listed = fixture("cost_types.html").replace("902\">050", "902\">041").replace("different prices", "fikseeritud hind (1.000 eurot)")
        self.assertEqual(korto.fixed_prices(twice_listed), {})  # a code used twice gets no baseline entry
        self.assertEqual(korto.fixed_prices(fixture("cost_types.html")), {"041": "901"})
        other = {("yyriarvestus.kululiigid_form", "3"): fixture("cost_type_form.html").replace(' value="H" checked', ' value="H"')
                 .replace(' value="S">', ' value="S" checked>')}
        code, _, err, _ = run("set-price", "041", "1", pages=other)
        self.assertIn("not priced by a fixed unit price", err)

    def test_set_residents_keeps_every_reading(self):
        after = {("yyriarvestus.naidud_muuda", "3"): fixture("reading_form.html").replace('name="elanike_arv" value="2"', 'name="elanike_arv" value="3"')}
        code, _, err, client = run("set-residents", "1", "03", "--apply", after=after)
        self.assertEqual(code, 0, err)
        fields = client.posts[0]
        self.assertEqual([sent(fields, k)[-1] for k in ("elanike_arv", "korter_muudetud", "action")], ["3", "t", "salvesta"])
        self.assertEqual([sent(fields, f"naidud[{i}][salvestada]")[-1] for i in range(3)], ["f", "t", "f"])
        self.assertEqual(run("set-residents", "1", "3", "--apply")[0], 1)  # Korto still shows 2
        harm = {("yyriarvestus.naidud_muuda", "3"): after[("yyriarvestus.naidud_muuda", "3")].replace('value="5.300"', 'value="5.000"')}
        code, _, err, _ = run("set-residents", "1", "3", "--apply", after=harm)
        self.assertEqual((code, "it also changed Street 1-1 Küte" in err), (1, True))

    def test_new_period_waits_for_the_open_one_and_sends_kortos_defaults(self):
        code, _, err, _ = run("new-period")
        self.assertEqual((code, "a period is open" in err), (2, True))
        closed = {("yyriarvestus.naidud", None): fixture("readings_2026_10.html").replace("(avatud)", "(jaotatud)"),
                  ("yyriarvestus.perioodid_vaata", "3"): fixture("period_dates.html")}
        opened = {("yyriarvestus.naidud", None): fixture("readings_2026_10.html").replace("(avatud)", "(jaotatud)")
                  .replace('<option value="3">', '<option value="4">2026 detsember (avatud)</option><option value="3">')}
        seen = {**closed, ("yyriarvestus.perioodid_vaata", "3"): fixture("period_view.html") + fixture("period_dates.html")}
        self.assertEqual(run("selftest", "--accept", pages=seen)[0], 0)  # no open period: the new-period form loads
        code, out, err, client = run("new-period", "--apply", pages=closed, after=opened)
        self.assertEqual(code, 0, err)
        self.assertIn("new period: 2026 detsember", out)
        self.assertEqual([sent(client.posts[0], k) for k in ("periood[kuu]", "periood[maksepaev]", "action")],
                         [["12"], ["14.01.2027"], ["salvesta"]])
        form = fixture("period_form.html")
        january = form.replace('value="2026"', 'value="2027"').replace('<option value="12" selected="selected">', '<option value="1" selected="selected">') \
            .replace('value="31.12.2026"', 'value="31.01.2027"').replace('value="14.01.2027"', 'value="14.02.2027"')
        for wrong in (january,
                      form.replace('value="31.12.2026"', 'value="30.12.2026"'), form.replace('value="14.01.2027"', 'value="15.01.2027"')):
            code, _, err, client = run("new-period", "--apply", pages={**closed, ("yyriarvestus.perioodid_form", "0"): wrong})
            self.assertEqual((code, client.posts, "Make it in the web UI" in err), (2, [], True))
        # another association's pattern: invoiced the day after the period, due on the 20th
        other = {**closed, ("yyriarvestus.perioodid_vaata", "3"): fixture("period_dates.html").replace("30.11.2026", "01.12.2026")
                 .replace("14.12.2026", "20.12.2026")}
        code, _, err, client = run("new-period", "--apply", pages=other, after=opened)
        self.assertEqual((code, "dated 01.01.2027 and due 20.01.2027" in err), (2, True))  # Korto's 31.12 / 14.01 differ
        theirs = {**other, ("yyriarvestus.perioodid_form", "0"): form.replace('value="31.12.2026"', 'value="01.01.2027"')
                  .replace('value="14.01.2027"', 'value="20.01.2027"')}
        self.assertEqual(run("new-period", "--apply", pages=theirs, after=opened)[0], 0)
        empty = {("yyriarvestus.naidud", None): '<select name="perioodi_id"></select>'}
        code, _, err, _ = run("new-period", pages=empty)
        self.assertEqual((code, "Korto lists no periods" in err), (2, True))


CONFIRMED = {("yyriarvestus.naidud", None): fixture("readings_2026_10.html").replace("(jaotatud)", "(kinnitatud)"),
             ("yyriarvestus.perioodid_vaata", "2"): fixture("period_confirmed.html")}


class Watch(unittest.TestCase):
    def test_apply_refuses_a_changed_stamp_a_changed_form_or_no_stamp(self):
        new_field = {("yyriarvestus.naidud_muuda", "3"): fixture("reading_form.html").replace(
            '<input type="hidden" name="screen"', '<input type="text" name="naidud[0][surprise]" value=""><input type="hidden" name="screen"')}
        for kwargs, message in (({"stamp": "20270101"}, "Korto changed (20261029 -> 20270101)"),
                                ({"pages": new_field}, "+ naidud[N][surprise]"),
                                ({"stamp": None}, "carry no version stamp")):
            code, out, err, client = run("set-reading", "1", "Külm vesi", "14.2", "--apply", **kwargs)
            self.assertEqual((code, client.posts), (2, []), kwargs)
            self.assertIn(message, err)
            code, out, _, _ = run("set-reading", "1", "Külm vesi", "14.2", **kwargs)  # the dry run says so in advance
            self.assertEqual(code, 0)
            self.assertIn(f"baseline: ", out)
            self.assertIn(message, out)

    def test_selftest_lists_differences_and_accepts_only_what_it_read(self):
        new_field = {("yyriarvestus.perioodid_lukusta", "3"): fixture("distribute.html").replace(
            '<input type="hidden" name="perioodi_id"', '<input type="text" name="surprise"><input type="hidden" name="perioodi_id"')}
        code, out, _, _ = run("selftest", pages=new_field)
        self.assertEqual(code, 1)
        self.assertIn("distribute: changed\n  + surprise", out)
        self.assertIn("readings:apartment: same", out)
        self.assertIn("not loadable in the current period states: new-period, close", out)
        self.assertIn("unclose: same", out)
        self.assertEqual(run("selftest")[0], 0)  # unchanged without --accept
        no_invoices = {("yyriarvestus.tehingud_form", "3"): fixture("expense_form.html").replace(
            '<option value="11">Heat Supplier (31.10.2026, nr. 100)</option><option value="12">Waste Co (31.10.2026, nr. 200)</option>', '')}
        code, out, _, _ = run("selftest", pages=no_invoices)
        self.assertEqual((code, "expense: nothing to read yet" in out), (0, True))  # skipped, said, not an error

    def test_a_get_form_is_never_posted(self):
        form = korto.forms('<form method="get"><input type="hidden" name="action" value=""><button type="submit" '
                           'onclick="this.form.elements[\'action\'].value = \'go\';">Go</button></form>')[0]
        with self.assertRaises(korto.UsageError):
            korto.press(form, "Go")

    def test_a_corrupt_baseline_refuses_and_tells(self):
        with open(BASELINE, encoding="utf-8") as f:
            good = f.read()
        try:
            for broken in ('[]', '{"7": {"readings:apartment": {"shape": {}}}}', "not json"):
                with open(BASELINE, "w", encoding="utf-8") as f:
                    f.write(broken)
                code, _, err, client = run("set-reading", "1", "Külm vesi", "14.2", "--apply")
                self.assertEqual((code, client.posts, "no baseline for the readings:apartment form" in err), (2, [], True), broken)
        finally:
            with open(BASELINE, "w", encoding="utf-8") as f:
                f.write(good)

    def test_the_fingerprint_ignores_data_but_not_order_or_buttons(self):
        shape = korto.find_form(fixture("reading_form.html"), "naidud[0][arvesti_id]").shape
        more_data = fixture("reading_form.html").replace('value="12.500"', 'value="99.000"')
        self.assertEqual(korto.find_form(more_data, "naidud[0][arvesti_id]").shape, shape)
        swapped = fixture("reading_form.html").replace('<input type="hidden" name="naidud[0][nait]" value="">', '', 1) \
            .replace('<input type="text" name="naidud[0][nait]" value="">',
                     '<input type="text" name="naidud[0][nait]" value=""><input type="hidden" name="naidud[0][nait]" value="">')
        self.assertNotEqual(korto.find_form(swapped, "naidud[0][arvesti_id]").shape, shape)  # which one PHP keeps changed
        for changed in (fixture("reading_form.html").replace("'kustuta_margitud'", "'kustuta_koik'"),
                        fixture("reading_form.html").replace('name="screen" value="yyriarvestus.naidud_muuda"', 'name="screen" value="x"'),
                        fixture("reading_form.html").replace('name="elanike_arv" value="2"', 'name="elanike_arv" value="2" disabled')):
            self.assertNotEqual(korto.find_form(changed, "naidud[0][arvesti_id]").shape, shape)


class Close(unittest.TestCase):
    def setUp(self):
        self.assertEqual(run("selftest", "--accept", pages=CONFIRMED)[0], 0)

    def test_dry_run_shows_payments_and_the_closing_pages_warnings(self):
        code, out, _, client = run("close", "--period", "2026-10", pages=CONFIRMED)
        self.assertEqual((code, client.posts, len(client.prefills)), (0, [], 1))  # the closing page opened, nothing pressed
        self.assertIn("Korto warns: 3 payments not exported", out)
        self.assertIn("2, newest 24.10.2026", out)

    def test_sulge_on_the_known_closing_page_and_the_state_is_read_back(self):
        closed = {("yyriarvestus.naidud", None): CONFIRMED[("yyriarvestus.naidud", None)].replace("(kinnitatud)", "(suletud)")}
        code, _, err, client = run("close", "--period", "2026-10", "--apply", pages=CONFIRMED, after=closed)
        self.assertEqual(code, 0, err)
        self.assertEqual([(sent(f, "screen"), sent(f, "action"), sent(f, "perioodi_id")) for f in client.posts],
                         [(["yyriarvestus.perioodid_sulge"], ["sulge"], ["2"])])
        code, _, err, _ = run("close", "--period", "2026-10", "--apply", pages=CONFIRMED)  # Korto still says confirmed
        self.assertEqual(code, 1)
        self.assertIn("the period is kinnitatud", err)

    def test_an_unknown_closing_page_is_described_never_pressed(self):
        known = fixture("closing.html")
        for page in (known.replace('<input type="hidden" name="perioodi_id"', '<input type="text" name="date" value="1">'
                                   '<input type="hidden" name="perioodi_id"'),
                     known.replace(">Tagasi<", ">Kustuta<"), known.replace('name="perioodi_id" value="2"', 'name="perioodi_id" value="3"')):
            code, _, err, client = run("close", "--period", "2026-10", "--apply", pages={**CONFIRMED, "closing": page})
            self.assertEqual((code, client.posts), (2, []))
            self.assertIn("not the one korto-cli knows", err)
        for page in (known.replace('value="yyriarvestus.perioodid_sulge"', 'value="yyriarvestus.perioodid_vaata"'),
                     known.replace("value = 'sulge'", "value = 'sulge_ja_kinnita'")):
            code, _, err, client = run("close", "--period", "2026-10", pages={**CONFIRMED, "closing": page})
            self.assertEqual((code, client.posts, "not the one korto-cli knows" in err), (2, [], True), page[-300:])

    def test_a_close_button_that_acts_is_never_pressed_even_in_a_dry_run(self):
        view = fixture("period_confirmed.html")
        for page in (view.replace("value = 'yyriarvestus.perioodid_sulge';", "value = 'yyriarvestus.perioodid_sulge'; "
                                  "this.form.elements['action'].value = 'sulge';"),
                     view.replace("value = 'yyriarvestus.perioodid_sulge';", "value = 'yyriarvestus.perioodid_sulge'; return confirm('Sure?');"),
                     view.replace("'yyriarvestus.perioodid_sulge'", "'yyriarvestus.perioodid_sulge_kohe'")):
            for argv in (("close", "--period", "2026-10"), ("selftest",)):
                code, out, err, client = run(*argv, pages={**CONFIRMED, ("yyriarvestus.perioodid_vaata", "2"): page})
                opened = [f for f in client.prefills if dict(f).get("screen") == "yyriarvestus.perioodid_sulge"]
                self.assertEqual((client.posts, opened), ([], []), argv)
                self.assertIn("no longer only opens its page", out + err)
        code, _, err, _ = run("close", "--period", "", pages=CONFIRMED)
        self.assertEqual((code, "name the period" in err), (2, True))

    def test_order_guards_and_a_required_period(self):
        base = CONFIRMED[("yyriarvestus.naidud", None)]
        for page, message in ((base.replace("2026 september (suletud)", "2026 september (kinnitatud)"), "close 2026 september first"),
                              (base.replace("2026 november (avatud)", "2026 november (kinnitatud)"), "too late"),
                              (fixture("readings_2026_10.html"), "takes a kinnitatud period")):
            code, _, err, _ = run("close", "--period", "2026-10", "--apply", pages={**CONFIRMED, ("yyriarvestus.naidud", None): page})
            self.assertEqual((code, message in err), (2, True), message)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            korto.main(["close"], client=FakeClient())


class InvoicePdf(unittest.TestCase):
    def test_the_rows_invoices_are_downloaded_as_the_naita_button_does(self):
        with tempfile.TemporaryDirectory() as folder:
            out = os.path.join(folder, "a.pdf")
            code, printed, err, client = run("invoice-pdf", "1", "--period", "2026-09", "--out", out)
            self.assertEqual(code, 0, err)
            with open(out, "rb") as f:
                self.assertEqual(f.read(), b"%PDF-1.7 invoice 801")
            self.assertEqual([(sent(f, "screen"), sent(f, "arve_id"), sent(f, "action"), sent(f, "valitud[]")) for f in client.prefills],
                             [(["yyriarvestus.arved_pdf"], ["801"], [""], [])])  # nothing ticked, nothing to send
            self.assertEqual(client.posts, [])
            code, printed, _, _ = run("invoice-pdf", "3", "--period", "2026-09", "--out", out)  # co-owners: one each
            self.assertEqual((code, sorted(os.listdir(folder))), (0, ["a-1.pdf", "a-2.pdf", "a.pdf"]))

    def test_no_invoice_or_no_pdf_fails_loudly(self):
        code, _, err, _ = run("invoice-pdf", "2", "--period", "2026-09")
        self.assertEqual((code, "no invoice for apartment 2" in err), (2, True))
        code, _, err, _ = run("invoice-pdf", "1", "--period", "2026-09", pages={"pdf": ("text/html", b"<html>login</html>")})
        self.assertEqual((code, "did not answer with a PDF" in err), (2, True))


NOVEMBER_PAID = ('<select name="perioodi_id"></select><table><tr><th></th><th>Kuupäev</th><th>Korter</th><th>Maksja</th><th>Summa</th></tr>'
                 '<tr><td></td><td>03.11.2026</td><td>1</td><td>Mari  Maasikas</td><td>10.00</td></tr></table>')


class ImportPayments(unittest.TestCase):
    def setUp(self):
        self.file = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
        self.file.write(b"bank;statement\n")
        self.file.close()

    def tearDown(self):
        os.unlink(self.file.name)

    def test_dry_run_uploads_unchanged_and_lists_why_each_row_is_or_is_not_saved(self):
        code, out, _, client = run("import-payments", self.file.name)
        self.assertEqual((code, client.posts), (0, []))
        self.assertEqual(dict(client.prefills[-1])["userfile"], os.path.basename(self.file.name))
        self.assertEqual(dict(client.prefills[-1])["action"], "upload")
        lines = {line.split()[0]: line for line in out.splitlines() if line[:1].isdigit()}
        self.assertIn("already in Korto", lines["1"])
        self.assertIn("yes", lines["2"])
        self.assertIn("no apartment", lines["3"])
        self.assertNotIn("yes", lines["4"])  # Korto did not tick it, and korto-cli never ticks on its own
        self.assertIn("all rows: 4, 55.00; to save: 1, 10.00; left for you in the web UI: 3", out)

    def test_apply_saves_only_what_korto_ticked_and_reads_it_back(self):
        after = {("yyriarvestus.laekumised", "3"): NOVEMBER_PAID}
        code, _, err, client = run("import-payments", self.file.name, "--apply", after=after)
        self.assertEqual(code, 0, err)
        fields = client.posts[0]
        self.assertEqual([sent(fields, f"laekumised[{i}][salvestada]")[-1] for i in range(4)], ["f", "t", "f", "f"])
        self.assertEqual(sent(fields, "action"), ["salvesta"])
        code, _, err, _ = run("import-payments", self.file.name, "--apply")  # Korto shows nothing new
        self.assertEqual((code, "the payments changed by {}" in err), (1, True))

    def test_rows_named_by_the_human_and_their_guards(self):
        later = {("yyriarvestus.laekumised", "3"): NOVEMBER_PAID.replace("03.11.2026", "05.11.2026")}
        code, _, err, client = run("import-payments", self.file.name, "--rows", "4", "--apply", after=later)
        self.assertEqual(code, 0, err)
        self.assertEqual([sent(client.posts[0], f"laekumised[{i}][salvestada]")[-1] for i in range(4)], ["f", "f", "f", "t"])
        for rows, reason in (("1", "already in Korto"), ("3", "no apartment")):
            code, _, err, client = run("import-payments", self.file.name, "--rows", rows, "--apply")
            self.assertEqual((code, client.posts, reason in err), (2, [], True), rows)
        nothing = {("yyriarvestus.laekumised_import_andmed", "3"): fixture("import_preview.html").replace(' value="t" checked></td>', ' value="t"></td>')}
        code, _, err, client = run("import-payments", self.file.name, "--apply", pages=nothing)
        self.assertEqual((code, client.posts, "no row to save" in err), (2, [], True))

    def test_an_unknown_preview_is_refused(self):
        odd = fixture("import_preview.html").replace('<input type="hidden" name="perioodi_id" value="3">\n',
                                                     '<input type="hidden" name="perioodi_id" value="3">')
        for page in (odd.replace('<button type="submit" onclick="this.form.elements[\'action\'].value = \'salvesta\';">',
                                 '<select name="replace"><option>all</option></select><button type="submit" '
                                 'onclick="this.form.elements[\'action\'].value = \'salvesta\';">'),
                     odd.replace("value = 'salvesta';", "value = 'salvesta_koik';")):
            code, _, err, client = run("import-payments", self.file.name, "--apply",
                                       pages={("yyriarvestus.laekumised_import_andmed", "3"): page})
            self.assertEqual((code, client.posts, "not the one korto-cli knows" in err), (2, [], True))


class Unclose(unittest.TestCase):
    def test_unclose_presses_tuhista_on_the_known_page(self):
        reopened = {("yyriarvestus.naidud", None): fixture("readings_2026_10.html").replace("2026 september (suletud)", "2026 september (kinnitatud)")}
        code, out, err, client = run("unclose", "--period", "2026-09")  # October (distributed) is not confirmed yet
        self.assertEqual((code, client.posts), (0, []), err)
        self.assertIn("2026 september: unclose", out)
        code, _, err, client = run("unclose", "--period", "2026-09", "--apply", after=reopened)
        self.assertEqual(code, 0, err)
        self.assertEqual([(sent(f, "screen"), sent(f, "action")) for f in client.posts],
                         [(["yyriarvestus.perioodid_tyhista_sulgemine"], ["tyhista"])])

    def test_unclose_refuses_when_the_next_period_is_confirmed(self):
        confirmed_next = {("yyriarvestus.naidud", None): fixture("readings_2026_10.html").replace("(jaotatud)", "(kinnitatud)")}
        code, _, err, client = run("unclose", "--period", "2026-09", "--apply", pages=confirmed_next)
        self.assertEqual((code, client.posts, "too late" in err), (2, [], True))
        code, _, err, _ = run("unclose", "--period", "2026-10")
        self.assertEqual((code, "takes a suletud period" in err), (2, True))


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

    def test_close_dry_run_through_the_real_client(self):
        pages = {"yyriarvestus.naidud": CONFIRMED[("yyriarvestus.naidud", None)], "yyriarvestus.laekumised": fixture("payments.html"),
                 "yyriarvestus.perioodid_vaata": fixture("period_confirmed.html"), "yyriarvestus.perioodid_sulge": fixture("closing.html")}
        posted = []

        def answer(request, timeout):
            if request.data:  # the closing page is reached by a POST of the period page's form
                posted.append(urllib.parse.parse_qs(request.data.decode()))
                screen = posted[-1]["screen"][0]
            else:
                screen = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)["screen"][0]
            return io.BytesIO((pages[screen] + '<script src="javascript/k.js?20261029"></script>').encode())
        out = io.StringIO()
        with mock.patch.object(korto._opener, "open", side_effect=answer), mock.patch("time.sleep"), contextlib.redirect_stdout(out):
            code = korto.main(["close", "--period", "2026-10"], client=korto.Client("s", "7"))
        self.assertEqual(code, 0)
        self.assertEqual([(q["screen"], q.get("action")) for q in posted], [(["yyriarvestus.perioodid_sulge"], None)])
        self.assertIn("Korto warns: 3 payments not exported", out.getvalue())

    def test_the_version_stamp_is_read_from_korto_pages(self):
        page = b'<select name="perioodi_id"></select><script src="javascript/k.js?20261029"></script>'
        with mock.patch.object(korto._opener, "open", side_effect=lambda *a, **k: io.BytesIO(page)), mock.patch("time.sleep"):
            client = korto.Client("s", "7")
            client.get("a")
        self.assertEqual(client.stamp, "20261029")

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
