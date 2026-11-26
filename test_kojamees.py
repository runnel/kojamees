"""Offline tests. Fixtures are hand-written HTML with invented names and numbers."""
import contextlib
import datetime as dt
import email.message
import http.client
import http.cookiejar
import io
import json
import os
import pathlib
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest import mock

import kojamees

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
        self.params = []
        self.stamp = stamp

    def get(self, screen, **params):
        key = (screen, params.get("perioodi_id"))
        self.requests.append(key)
        self.params.append((screen, params))
        html = self.after[key] if self.posts and key in self.after else self.html.get(key) or fixture(self.pages[key])
        return kojamees.ensure_logged_in(html)

    def upload(self, fields, name, filename, data):
        urllib.parse.urlencode(fields)
        self.prefills.append(list(fields) + [(name, filename)])  # Korto keeps the preview and saves nothing
        return self.html.get("upload answer", "")

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
        code = kojamees.main(list(argv), client=client)
    return code, out.getvalue(), err.getvalue(), client


BASELINE = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name


LOCK = tempfile.NamedTemporaryFile(suffix=".lock", delete=False).name


def setUpModule():  # every --apply in these tests passes the baseline guard, as a user's would after selftest
    kojamees.LOCK_FILE = LOCK  # never the home folder's
    os.environ["KORTO_BASELINE"] = BASELINE
    os.unlink(BASELINE)
    code, out, err, _ = run("selftest", "--accept")
    assert code == 0, out + err


def tearDownModule():
    os.environ.pop("KORTO_BASELINE", None)
    for path in (BASELINE, LOCK):
        if os.path.exists(path):
            os.unlink(path)


def sent(fields, name):
    """Every value posted for a field; Korto (PHP) uses the last."""
    return [v for n, v in fields if n == name]


class Parsing(unittest.TestCase):
    def test_periods(self):
        found = kojamees.periods(fixture("readings_2026_10.html"))
        self.assertEqual([p["name"] for p in found], ["2026 november", "2026 oktoober", "2026 september"])
        self.assertEqual(found[0], {"id": "3", "name": "2026 november", "state": "avatud"})

    def test_period_end(self):
        self.assertEqual(kojamees.period_end("2026 oktoober"), dt.date(2026, 10, 31))
        self.assertEqual(kojamees.period_end("2028 veebruar"), dt.date(2028, 2, 29))
        with self.assertRaises(kojamees.UsageError):
            kojamees.period_end("2026 I kvartal")

    def test_readings_in_open_period_layout(self):  # the October fixture has the leading checkbox column
        data = kojamees.readings(fixture("readings_2026_10.html"))
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
            with self.assertRaises(kojamees.UsageError):
                kojamees.readings(page)

    def test_tables_survive_nested_tables_unclosed_cells_and_line_breaks(self):
        t = kojamees.tables('<table id="a"><tr><td>x<table><tr><td>in</td></tr></table>after<input name="n">'
                         '<td>open<br>cell</table><p>footer</p>')
        row = t["a"][0]
        self.assertEqual((row[0]["text"], row[0]["inputs"]), ("x after", [("n", "")]))
        self.assertEqual(row[1]["text"], "open cell")

    def test_missing_invoices(self):
        self.assertEqual(kojamees.missing_invoices(fixture("expenses_missing.html")), ["Prügivedu", "Elekter"])
        self.assertEqual(kojamees.missing_invoices(fixture("expenses_one_missing.html")), ["Prügivedu"])
        self.assertEqual(kojamees.missing_invoices(fixture("summary.html")), [])

    def test_login_page_is_detected(self):
        with self.assertRaises(kojamees.SessionExpired):
            kojamees.ensure_logged_in(fixture("login.html"))


class Checks(unittest.TestCase):
    def setUp(self):
        self.current = kojamees.readings(fixture("readings_2026_10.html"))
        self.previous = kojamees.readings(fixture("readings_2026_09.html"))
        self.findings = kojamees.check(self.current, self.previous, dt.date(2026, 10, 31),
                                    kojamees.missing_invoices(fixture("expenses_missing.html")), {"4:Soe vesi"})

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
        findings = kojamees.check(current, self.previous, dt.date(2026, 10, 31))
        self.assertIn("building heat", [f["meter"] for f in findings])

    def test_building_heat_uses_the_largest_positive_meter(self):
        current = dict(self.current, apartments=[dict(self.current["apartments"][1], quantity=3.0, date="31.10.2026")])
        two_meters = dict(current, building=[{"unit": "MWh", "quantity": 2.0}, {"unit": "MWh", "quantity": 4.0}])
        negative = dict(current, building=[{"unit": "MWh", "quantity": -0.2}])
        for case in (two_meters, negative):
            messages = [f["message"] for f in kojamees.check(case, self.previous, dt.date(2026, 10, 31))]
            self.assertFalse(any("whole building" in m for m in messages), messages)

    def test_previous_period_is_matched_by_meter_id(self):
        renamed = {"building": [], "apartments": [dict(r, meter="Renamed") for r in self.previous["apartments"]]}
        findings = kojamees.check(self.current, renamed, dt.date(2026, 10, 31))
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
        form = kojamees.find_form(fixture("reading_form.html"), "naidud[0][arvesti_id]")
        fields = kojamees.payload(form)
        self.assertEqual(sent(fields, "naidud[1][nait]"), ["5.300", "5.300"])
        self.assertEqual(sent(fields, "naidud[0][prognoositud]"), ["f"])        # unticked checkbox: hidden default only
        self.assertEqual(sent(fields, "naidud[1][prognoositud]"), ["f", "t"])
        self.assertEqual(sent(fields, "note"), [])                             # disabled
        self.assertEqual(kojamees.value(form, "naidud[1][kuupaev]"), "30.11.2026")
        self.assertEqual(kojamees.value(kojamees.find_form(fixture("readings_2026_10.html"), "kuude_arv"), "kuude_arv"), "6")

    def test_press_runs_the_buttons_own_assignments(self):
        form = kojamees.find_form(fixture("reading_form.html"), "naidud[0][arvesti_id]")
        fields = kojamees.press(form, "Salvesta")  # the page's own button, not the dialog's of the same name
        self.assertEqual(sent(fields, "action"), ["salvesta"])
        self.assertEqual(sent(fields, "usage[amount]"), [""])  # an unopened dialog still sits in the form
        form = kojamees.find_form(fixture("readings_2026_10.html"), "kuude_arv")
        fields = kojamees.press(form, "Prognoosi")  # a dialog button: the other dialogs are disabled
        self.assertEqual((sent(fields, "action"), sent(fields, "note[text]")), (["prognoosi"], []))

    def test_unknown_fields_buttons_and_options_fail_loudly(self):
        form = kojamees.find_form(fixture("readings_2026_10.html"), "kuude_arv")
        odd = kojamees.forms('<form><input type="hidden" name="action"><button disabled onclick="this.form.elements'
                          '[\'action\'].value = \'go\';">Go</button><button onclick="submitIt()">Odd</button></form>')[0]
        for call in (lambda: kojamees.fill(form, "nope", "1"), lambda: kojamees.fill(form, "kuude_arv", "13"),
                     lambda: kojamees.fill(form, "kuude_arv", True), lambda: kojamees.press(form, "Nope"),
                     lambda: kojamees.find_form("<form></form>", "kuude_arv"), lambda: kojamees.press(odd, "Go"),
                     lambda: kojamees.press(odd, "Odd")):
            with self.assertRaises(kojamees.UsageError):
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
        self.assertEqual(kojamees.fixed_prices(twice_listed), {})  # a code used twice gets no baseline entry
        self.assertEqual(kojamees.fixed_prices(fixture("cost_types.html")), {"041": "901"})
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
        form = kojamees.forms('<form method="get"><input type="hidden" name="action" value=""><button type="submit" '
                           'onclick="this.form.elements[\'action\'].value = \'go\';">Go</button></form>')[0]
        with self.assertRaises(kojamees.UsageError):
            kojamees.press(form, "Go")

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
        shape = kojamees.find_form(fixture("reading_form.html"), "naidud[0][arvesti_id]").shape
        more_data = fixture("reading_form.html").replace('value="12.500"', 'value="99.000"')
        self.assertEqual(kojamees.find_form(more_data, "naidud[0][arvesti_id]").shape, shape)
        swapped = fixture("reading_form.html").replace('<input type="hidden" name="naidud[0][nait]" value="">', '', 1) \
            .replace('<input type="text" name="naidud[0][nait]" value="">',
                     '<input type="text" name="naidud[0][nait]" value=""><input type="hidden" name="naidud[0][nait]" value="">')
        self.assertNotEqual(kojamees.find_form(swapped, "naidud[0][arvesti_id]").shape, shape)  # which one PHP keeps changed
        for changed in (fixture("reading_form.html").replace("'kustuta_margitud'", "'kustuta_koik'"),
                        fixture("reading_form.html").replace('name="screen" value="yyriarvestus.naidud_muuda"', 'name="screen" value="x"'),
                        fixture("reading_form.html").replace('name="elanike_arv" value="2"', 'name="elanike_arv" value="2" disabled')):
            self.assertNotEqual(kojamees.find_form(changed, "naidud[0][arvesti_id]").shape, shape)


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
            self.assertIn("not the one Kojamees knows", err)
        for page in (known.replace('value="yyriarvestus.perioodid_sulge"', 'value="yyriarvestus.perioodid_vaata"'),
                     known.replace("value = 'sulge'", "value = 'sulge_ja_kinnita'")):
            code, _, err, client = run("close", "--period", "2026-10", pages={**CONFIRMED, "closing": page})
            self.assertEqual((code, client.posts, "not the one Kojamees knows" in err), (2, [], True), page[-300:])

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
            kojamees.main(["close"], client=FakeClient())


class InvoicePdf(unittest.TestCase):
    def setUp(self):  # every file the tests write goes here, never into the repository
        self.folder = tempfile.mkdtemp()

    def tearDown(self):
        for name in os.listdir(self.folder):
            os.unlink(os.path.join(self.folder, name))
        os.rmdir(self.folder)

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

    def test_a_form_that_would_send_or_an_existing_file_is_refused(self):
        ticked = {("yyriarvestus.arved", "1"): fixture("invoices.html").replace('value="801">', 'value="801" checked>')}
        code, _, err, client = run("invoice-pdf", "1", "--period", "2026-09", "--out", os.path.join(self.folder, "t.pdf"), pages=ticked)
        self.assertEqual((code, client.prefills, "would send more than a download" in err), (2, [], True))
        with tempfile.NamedTemporaryFile(suffix=".pdf") as existing:
            code, _, err, client = run("invoice-pdf", "1", "--period", "2026-09", "--out", existing.name)
            self.assertEqual((code, client.prefills, "does not overwrite" in err), (2, [], True))

    def test_no_invoice_or_no_pdf_fails_loudly(self):
        code, _, err, _ = run("invoice-pdf", "2", "--period", "2026-09", "--out", os.path.join(self.folder, "n.pdf"))
        self.assertEqual((code, "no invoice for apartment 2" in err), (2, True))
        code, _, err, _ = run("invoice-pdf", "1", "--period", "2026-09", "--out", os.path.join(self.folder, "h.pdf"),
                              pages={"pdf": ("application/pdf", b"<html>login</html>")})  # the type alone is not enough
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
        self.assertNotIn("yes", lines["4"])  # Korto did not tick it, and Kojamees never ticks on its own
        self.assertIn("all rows: 4, 55.00; to save: 1, 10.00; left for you in the web UI: 3", out)
        lists = [(p["perioodi_id"], p.get("alates"), p.get("kuni")) for screen, p in client.params if screen == "yyriarvestus.laekumised"]
        self.assertEqual(lists, [(pid, "01.10.2026", "31.12.2026") for pid in "321"])  # never Korto's month filter
        moved = {("yyriarvestus.laekumised", "2"): fixture("summary.html"), ("yyriarvestus.laekumised", "1"): fixture("payments.html")}
        code, out, _, _ = run("import-payments", self.file.name, pages=moved)  # imported early, into the period then open
        self.assertIn("already in Korto", next(l for l in out.splitlines() if l.startswith("1 ")))

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

    def test_every_reason_not_to_save_a_row(self):
        preview = fixture("import_preview.html")
        cases = ((preview.replace('name="laekumised[1][summa]" value="10.00"', 'name="laekumised[1][summa]" value="11.00"'), "differs from the bank's"),
                 (preview.replace('name="laekumised[1][summa]" value="10.00"', 'name="laekumised[1][summa]" value="-10.00"'), "not a positive amount"),
                 (preview.replace('value="03.11.2026"', 'value="30.09.2026"'), "dated before 2026 oktoober"),
                 (preview.replace('value="03.11.2026"', 'value="2026-11-03"'), "a date Kojamees cannot read"),
                 (preview.replace('value="03.11.2026"', 'value="05.01.2027"'), "dated after 31.12.2026"))
        for page, reason in cases:
            code, out, _, client = run("import-payments", self.file.name, pages={("yyriarvestus.laekumised_import_andmed", "3"): page})
            line = next(l for l in out.splitlines() if l.startswith("2 "))
            self.assertIn(reason, line)
        october_end = preview.replace('value="03.11.2026"', 'value="30.10.2026"')  # the previous period's last days are fine
        _, out, _, _ = run("import-payments", self.file.name, pages={("yyriarvestus.laekumised_import_andmed", "3"): october_end})
        self.assertIn("yes", next(l for l in out.splitlines() if l.startswith("2 ")))

    def test_the_upload_is_guarded_and_its_answer_read(self):
        changed = {("yyriarvestus.laekumised_import_fail", "3"): fixture("import_upload.html").replace("value = 'upload'", "value = 'upload_and_save'")}
        code, _, err, client = run("import-payments", self.file.name, pages=changed)
        self.assertEqual((code, client.prefills, "import form is not the one Kojamees knows" in err), (2, [], True))
        code, _, err, client = run("import-payments", self.file.name, stamp="20270101")  # Korto shipped since the baseline
        self.assertEqual((code, client.prefills, "Korto changed (20261029 -> 20270101)" in err), (2, [], True))
        relabelled = {("yyriarvestus.laekumised_import_fail", "3"): fixture("import_upload.html").replace(">Laadi fail<", ">Upload<")}
        code, _, err, client = run("import-payments", self.file.name, pages=relabelled)
        self.assertEqual((code, client.prefills, "no Laadi fail button" in err), (2, [], True))
        code, _, err, client = run("import-payments", self.file.name, pages={"upload answer": "<h1>Wrong file format</h1>"})
        self.assertEqual((code, client.posts, "Korto did not take the file: Wrong file format" in err), (2, [], True))
        code, _, err, _ = run("import-payments", self.file.name, "--rows", "2,9", "--apply")
        self.assertEqual((code, "no row 9 in Korto's preview" in err), (2, True))
        many = {("yyriarvestus.laekumised", "2"): fixture("payments.html").replace("</table>",
                "".join(f'<tr><td></td><td>01.10.2026</td><td>1</td><td>P{i}</td><td>1.00</td></tr>' for i in range(60)) + "</table>")}
        code, _, err, _ = run("import-payments", self.file.name, pages=many)
        self.assertEqual((code, "has not seen whether that list is paged" in err), (2, True))

    def test_a_payment_that_disappears_is_not_success(self):
        gone = fixture("payments.html").replace('<tr><td></td><td>05.10.2026</td><td>1</td><td>Mari Maasikas</td><td>10.00</td></tr>', '')
        self.assertNotEqual(gone, fixture("payments.html"))
        october = {("yyriarvestus.laekumised", "2"): gone, ("yyriarvestus.laekumised", "3"): NOVEMBER_PAID}  # one added, one gone
        code, _, err, _ = run("import-payments", self.file.name, "--apply", after=october)
        self.assertEqual(code, 1, err)

    def test_an_unknown_preview_is_refused(self):
        odd = fixture("import_preview.html").replace('<input type="hidden" name="perioodi_id" value="3">\n',
                                                     '<input type="hidden" name="perioodi_id" value="3">')
        for page in (odd.replace('<button type="submit" onclick="this.form.elements[\'action\'].value = \'salvesta\';">',
                                 '<select name="replace"><option>all</option></select><button type="submit" '
                                 'onclick="this.form.elements[\'action\'].value = \'salvesta\';">'),
                     odd.replace("value = 'salvesta';", "value = 'salvesta_koik';"),
                     odd.replace('<input type="hidden" name="perioodi_id" value="3">', '<input type="hidden" name="perioodi_id" value="3">'
                                 '<input type="hidden" name="replace_existing" value="t">'),
                     odd.replace('type="text" name="laekumised[1][summa]"', 'type="hidden" name="laekumised[1][summa]"'),
                     odd.replace('name="screen" value="yyriarvestus.laekumised_import_andmed"', 'name="screen" value="x"'),
                     odd.replace('<input type="hidden" name="perioodi_id" value="3"><button', '<input type="hidden" name="perioodi_id" value="2"><button')):
            code, _, err, client = run("import-payments", self.file.name, "--apply",
                                       pages={("yyriarvestus.laekumised_import_andmed", "3"): page})
            self.assertEqual((code, client.posts, "not the one Kojamees knows" in err), (2, [], True))


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
            with mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(kojamees._opener, "open", opener), \
                 mock.patch("time.sleep"), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = kojamees.main(["--env", f.name, "periods"])
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
        self.assertTrue(request.get_header("User-agent").startswith("kojamees/"))
        self.assertIn("yhistu_id=7", request.full_url)

    def test_redirect_means_expired_and_is_not_followed(self):
        redirect = urllib.error.HTTPError("https://pro.korto.ee/", 302, "Found", {}, None)
        code, output = self.main_with_env("KORTO_PHPSESSID=secret2\nKORTO_UNION_ID=7\n", mock.Mock(side_effect=redirect))
        self.assertEqual(code, 3)
        self.assertNotIn("secret2", output)
        self.assertTrue(any(isinstance(h, kojamees._NoRedirect) for h in kojamees._opener.handlers))

    def test_malformed_cookie_and_missing_env_file_are_usage_errors(self):
        opener = mock.Mock()
        code, output = self.main_with_env("KORTO_PHPSESSID=bad value\nKORTO_UNION_ID=7\n", opener)
        self.assertEqual(code, 2)
        self.assertIn("KORTO_PHPSESSID", output)
        self.assertNotIn("bad value", output)
        opener.assert_not_called()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            kojamees.main(["--env", "/nonexistent/korto.env", "periods"])
        self.assertEqual(caught.exception.code, 2)

    def test_a_redirect_after_a_post_is_a_save_but_one_to_the_login_page_is_expiry(self):
        def redirect(to):
            return mock.Mock(side_effect=urllib.error.HTTPError(kojamees.BASE, 302, "Found", {"Location": to}, None))
        client = kojamees.Client("s", "7")
        with mock.patch("time.sleep"):
            with mock.patch.object(kojamees._opener, "open", redirect("index.php?screen=yyriarvestus.naidud_muuda")):
                self.assertEqual(client.post([("action", "salvesta")]), "")
                with self.assertRaises(kojamees.UsageError):
                    client.get("yyriarvestus.perioodid")
            with mock.patch.object(kojamees._opener, "open", redirect("https://pro.korto.ee/index.php?screen=sisenemine.sisenemine")):
                with self.assertRaises(kojamees.SessionExpired):
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
        with mock.patch.object(kojamees._opener, "open", side_effect=answer), mock.patch("time.sleep"), contextlib.redirect_stdout(out):
            code = kojamees.main(["close", "--period", "2026-10"], client=kojamees.Client("s", "7"))
        self.assertEqual(code, 0)
        self.assertEqual([(q["screen"], q.get("action")) for q in posted], [(["yyriarvestus.perioodid_sulge"], None)])
        self.assertIn("Korto warns: 3 payments not exported", out.getvalue())

    def test_the_version_stamp_is_read_from_korto_pages(self):
        page = b'<select name="perioodi_id"></select><script src="javascript/k.js?20261029"></script>'
        with mock.patch.object(kojamees._opener, "open", side_effect=lambda *a, **k: io.BytesIO(page)), mock.patch("time.sleep"):
            client = kojamees.Client("s", "7")
            client.get("a")
        self.assertEqual(client.stamp, "20261029")

    def test_requests_are_at_least_a_second_apart(self):
        page = fixture("readings_2026_10.html").encode()
        with mock.patch.object(kojamees._opener, "open", side_effect=lambda *a, **k: io.BytesIO(page)), \
             mock.patch("time.sleep") as sleep:
            client = kojamees.Client("s", "7")
            client.get("a")
            client.get("b")
        self.assertGreater(sleep.call_args_list[1][0][0], 0.9)


def valid_code(first_ten="3800101000"):
    """A synthetic personal code with a correct check digit."""
    digits = [int(c) for c in first_ten]
    for weights in ((1, 2, 3, 4, 5, 6, 7, 8, 9, 1), (3, 4, 5, 6, 7, 8, 9, 1, 2, 3)):
        check = sum(d * w for d, w in zip(digits, weights)) % 11
        if check < 10:
            return first_ten + str(check)
    return first_ten + "0"


class Answer(io.BytesIO):
    """An HTTP answer: a body, and headers such as Set-Cookie."""

    def __init__(self, body, cookies=()):
        super().__init__(body.encode() if isinstance(body, str) else body)
        self.headers = email.message.Message()
        for line in cookies:
            self.headers["Set-Cookie"] = line


READINGS = fixture("readings_2026_10.html")


class EnvFile(unittest.TestCase):
    def write(self, data):
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "secrets.env")
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_only_the_first_line_of_each_key_changes_and_every_other_byte_stays(self):
        before = (b"\xef\xbb\xbfOTHER_TOKEN=keep me\r\n# KORTO_AUTH_TOKEN=commented\r\nexport KORTO_AUTH_TOKEN=exported\r\n"
                  b"KORTO_AUTH_TOKEN = old\r\nKORTO_AUTH_TOKEN=second\r\nLAST=no newline")
        path = self.write(before)
        kojamees.save_env(path, {"KORTO_AUTH_TOKEN": "new", "KORTO_LOGIN": "smart-id"})
        with open(path, "rb") as f:
            after = f.read()
        self.assertEqual(after, before.replace(b"KORTO_AUTH_TOKEN = old\r\n", b"KORTO_AUTH_TOKEN=new\r\n")
                         + b"\r\nKORTO_LOGIN=smart-id\r\n")
        env = kojamees.load_env(path)
        self.assertEqual((env["KORTO_AUTH_TOKEN"], env["OTHER_TOKEN"], env["LAST"]), ("new", "keep me", "no newline"))
        if os.name != "nt":
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_a_missing_file_is_created_and_a_symlinks_target_is_written(self):
        folder = tempfile.mkdtemp()
        target = os.path.join(folder, "real.env")
        kojamees.save_env(target, {"KORTO_UNION_ID": "7"})
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"KORTO_UNION_ID=7\n")
        if os.name != "nt":
            link = os.path.join(folder, "link.env")
            os.symlink(target, link)
            kojamees.save_env(link, {"KORTO_UNION_ID": "8"})
            self.assertTrue(os.path.islink(link))
            self.assertEqual(kojamees.load_env(target)["KORTO_UNION_ID"], "8")

    def test_personal_code_check_digit(self):
        good = valid_code()
        self.assertTrue(kojamees.personal_code_ok(good))
        wrong = good[:10] + str((int(good[10]) + 1) % 10)
        self.assertFalse(kojamees.personal_code_ok(wrong))
        self.assertFalse(kojamees.personal_code_ok(good[:10]))
        self.assertFalse(kojamees.personal_code_ok("9" + good[1:]))


class TokenSession(unittest.TestCase):
    def test_a_run_starts_with_the_token_alone_then_keeps_the_session_korto_hands_it(self):
        sent = []

        def answer(request, timeout):
            sent.append(request.get_header("Cookie"))
            return Answer(READINGS, ["PHPSESSID=fresh1; path=/"] if len(sent) == 1 else [])
        client = kojamees.Client(None, "7", "tok.en-1_x")
        with mock.patch.object(kojamees._opener, "open", side_effect=answer), mock.patch("time.sleep"):
            client.get("yyriarvestus.naidud")
            client.get("yyriarvestus.naidud")
            client.post([("action", "x")])
        self.assertEqual(sent, ["auth_token=tok.en-1_x", "auth_token=tok.en-1_x; PHPSESSID=fresh1",
                                "auth_token=tok.en-1_x; PHPSESSID=fresh1"])

    def test_deletions_bad_values_and_login_pages_never_set_the_session(self):
        client = kojamees.Client(None, "7", "tok")
        for cookies in (["PHPSESSID=deleted; expires=Thu, 01-Jan-1970 00:00:01 GMT"], ["PHPSESSID=deleted"], ["PHPSESSID=abc; Max-Age=0"],
                        ["PHPSESSID=bad value"], ["PHPSESSID=; path=/"], ["PHPSESSID=x; expires=not a date"]):
            with mock.patch.object(kojamees._opener, "open", return_value=Answer(READINGS, cookies)), mock.patch("time.sleep"):
                client.get("yyriarvestus.naidud")
            self.assertIsNone(client.session_id, cookies)
        with mock.patch.object(kojamees._opener, "open", return_value=Answer(fixture("login.html"), ["PHPSESSID=anon"])), \
                mock.patch("time.sleep"), self.assertRaises(kojamees.SessionExpired) as caught:
            client.get("yyriarvestus.naidud")
        self.assertIsNone(client.session_id)
        self.assertEqual(str(caught.exception), kojamees.ENDED)

    def test_no_form_is_posted_before_korto_opened_a_page(self):
        opener = mock.Mock()
        with mock.patch.object(kojamees._opener, "open", opener), self.assertRaises(kojamees.UsageError):
            kojamees.Client(None, "7", "tok").post([("action", "salvesta")])
        opener.assert_not_called()

    def env_run(self, text, *argv, tty=False, answers=None, out_tty=None):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8") as f:
            f.write(text)
        out, err, sent = (Console() if (tty if out_tty is None else out_tty) else io.StringIO()), io.StringIO(), []

        def answer(request, timeout):
            sent.append(request.get_header("Cookie"))
            return Answer(READINGS, ["PHPSESSID=fresh"])
        try:
            with mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(kojamees._opener, "open", side_effect=answer), \
                    mock.patch("time.sleep"), mock.patch("sys.stdin.isatty", return_value=tty), \
                    mock.patch("builtins.input", side_effect=answers or AssertionError("no question")) as asked, \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = kojamees.main(["--env", f.name, *argv])
                self.asked = asked.call_count
        finally:
            os.unlink(f.name)
        return code, out.getvalue() + err.getvalue(), sent

    def test_the_token_wins_over_a_copied_session_and_is_never_printed(self):
        code, output, sent = self.env_run("KORTO_PHPSESSID=stale\nKORTO_AUTH_TOKEN=secrettoken\nKORTO_AUTH_UNTIL=2999-01-01\n"
                                          "KORTO_UNION_ID=7\n", "periods")
        self.assertEqual((code, sent[0]), (0, "auth_token=secrettoken"))
        self.assertNotIn("stale", "".join(sent))
        self.assertNotIn("secrettoken", output)

    def test_a_token_past_its_day_ends_the_login_before_any_request(self):
        code, output, sent = self.env_run("KORTO_AUTH_TOKEN=tok\nKORTO_AUTH_UNTIL=2020-01-01\nKORTO_UNION_ID=7\n", "periods")
        self.assertEqual((code, sent), (3, []))
        self.assertIn(kojamees.ENDED, output)

    def test_an_ended_login_asks_in_a_terminal_but_never_after_a_save(self):
        env = "KORTO_AUTH_TOKEN=tok\nKORTO_UNION_ID=7\n"

        def ended(client, args, save=False):
            client.saving = save
            raise kojamees.SessionExpired(kojamees.ENDED)
        with mock.patch.dict(kojamees.COMMANDS, {"periods": ended}):
            self.assertEqual(self.env_run(env, "periods", tty=True, answers=["n"])[0], 3)
            self.assertEqual(self.asked, 1)
            self.assertEqual(self.env_run(env, "periods", tty=False)[0], 3)
            self.assertEqual(self.env_run(env, "periods", tty=True, out_tty=False)[0], 3)  # `> out.json`: input() would raise
        with mock.patch.dict(kojamees.COMMANDS, {"periods": lambda client, args: ended(client, args, save=True)}):
            self.assertEqual(self.env_run(env, "periods", tty=True)[0], 3)  # input() would raise


class Console(io.StringIO):
    def isatty(self):
        return True


def cookie(name, value, expires=None):
    return http.cookiejar.Cookie(0, name, value, None, False, "pro.korto.ee", False, False, "/", True, True, expires,
                                 False, None, None, {})


class Login(unittest.TestCase):
    def login(self, env_text, *argv, tty=False, typed=(), answers=(), confirm_after=2, token="jwt.tok-en_1", page=READINGS,
              front=fixture("front_page.html")):
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "korto.env")
        with open(path, "w", encoding="utf-8") as f:
            f.write(env_text)
        jar, asked = http.cookiejar.CookieJar(), []

        class Api:
            def open(self, request, timeout):
                body = json.loads(request.data)
                asked.append((request.get_header("Origin"), body["query"].split("(")[0].split("{")[0].strip(), body["variables"]))
                if "smartIDLogin" in body["query"]:
                    return Answer(json.dumps({"data": {"smartIDLogin": "4321"}}))
                done = sum("Status" in q for _, q, _ in asked) >= confirm_after
                if done and token:
                    jar.set_cookie(cookie("auth_token", token, int(dt.datetime(2027, 11, 26, 9, tzinfo=dt.timezone.utc).timestamp())))
                return Answer(json.dumps({"data": {"smartIDStatus": done}}))
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(kojamees, "_login_session", return_value=(Api(), jar)), \
                mock.patch.object(kojamees._opener, "open", side_effect=lambda request, timeout: Answer(
                    front if "avaleht" in request.full_url else page)), mock.patch("time.sleep"), \
                mock.patch("sys.stdin.isatty", return_value=tty), mock.patch("builtins.input", side_effect=list(answers)), \
                mock.patch("getpass.getpass", side_effect=list(typed)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = kojamees.main(["--env", path, "login", *argv])
        with open(path, encoding="utf-8") as f:
            saved = f.read()
        return code, out.getvalue() + err.getvalue(), saved, asked

    def test_smart_id_shows_the_code_polls_checks_the_pages_and_keeps_only_the_token(self):
        code, output, saved, asked = self.login(f"OTHER=1\nKORTO_UNION_ID=7\nKORTO_ID={valid_code()}\nKORTO_PHPSESSID=old\n")
        self.assertEqual(code, 0, output)
        self.assertIn("Smart-ID: check that your phone shows 4321, then enter PIN1.", output)
        self.assertEqual([q for _, q, _ in asked], ["mutation smartIDLogin", "mutation smartIDStatus", "mutation smartIDStatus"])
        self.assertEqual({o for o, _, _ in asked}, {"https://korto.ee"})
        self.assertIn("KORTO_AUTH_TOKEN=jwt.tok-en_1\nKORTO_AUTH_UNTIL=2027-11-26\nKORTO_LOGIN=smart-id\n", saved)
        self.assertTrue(saved.startswith("OTHER=1\nKORTO_UNION_ID=7\n"))
        self.assertNotIn("jwt.tok-en_1", output)
        self.assertIn("KORTO_PHPSESSID in", output)  # the 0.4 line is no longer used

    def test_without_a_terminal_it_never_asks_and_needs_the_code_in_the_file(self):
        code, output, saved, asked = self.login("KORTO_UNION_ID=7\n")
        self.assertEqual((code, asked), (2, []))
        self.assertIn("KORTO_ID", output)

    def test_a_typed_code_is_checked_first_and_remembered_by_default(self):
        bad = valid_code()[:10] + str((int(valid_code()[10]) + 1) % 10)
        code, output, saved, asked = self.login("KORTO_UNION_ID=7\n", tty=True, typed=[bad])
        self.assertEqual((code, asked), (2, []))
        self.assertIn("check digit", output)
        code, output, saved, asked = self.login("KORTO_UNION_ID=7\n", tty=True, typed=[valid_code()], answers=[""])
        self.assertEqual(code, 0, output)
        self.assertIn(f"KORTO_ID={valid_code()}\n", saved)
        self.assertNotIn(valid_code(), output)

    def test_the_association_comes_from_kortos_own_selector(self):
        code, output, saved, _ = self.login(f"KORTO_ID={valid_code()}\n", tty=True, answers=["2"])
        self.assertEqual(code, 0, output)
        self.assertIn("1. Street 1 Association (7)", output)
        self.assertIn("KORTO_UNION_ID=9\n", saved)
        code, output, saved, _ = self.login(f"KORTO_ID={valid_code()}\n", tty=True, answers=["3"])
        self.assertEqual((code, "KORTO_AUTH_TOKEN" in saved), (2, False))
        one = fixture("front_page.html").replace('<option value="9">Other Street 3 Association</option>', "")
        code, output, saved, _ = self.login(f"KORTO_ID={valid_code()}\n", front=one)  # no terminal needed for one
        self.assertEqual(code, 0, output)
        self.assertIn("Association: Street 1 Association (7)", output)
        self.assertIn("KORTO_UNION_ID=7\n", saved)
        for front, answers in ((fixture("front_page.html"), []), (fixture("login.html"), [])):
            code, output, saved, _ = self.login(f"KORTO_ID={valid_code()}\n", front=front, answers=answers)
            self.assertEqual(code, 2, output)
            self.assertNotIn("KORTO_AUTH_TOKEN", saved)
            self.assertIn("KORTO_UNION_ID", output)

    def test_nothing_is_saved_when_the_token_does_not_open_the_pages_or_never_comes(self):
        for kwargs in ({"page": fixture("login.html")}, {"token": None, "confirm_after": 1}):
            code, output, saved, _ = self.login(f"KORTO_UNION_ID=7\nKORTO_ID={valid_code()}\n", **kwargs)
            self.assertEqual(code, 2, output)
            self.assertNotIn("KORTO_AUTH_TOKEN", saved)
            self.assertIn("Nothing was saved", output)

    def test_an_answer_that_is_not_the_api_is_a_usage_error(self):
        class Html:
            def open(self, request, timeout):
                return Answer("<html>502 Bad Gateway</html>")
        with self.assertRaises(kojamees.UsageError):
            kojamees._graphql(Html(), "mutation smartIDStatus { smartIDStatus }")

        class Null:
            def open(self, request, timeout):
                return Answer("null")
        with self.assertRaises(kojamees.UsageError):
            kojamees._graphql(Null(), "mutation smartIDStatus { smartIDStatus }")

    def test_a_session_in_the_environment_would_hide_the_file(self):
        with mock.patch.dict("os.environ", {"KORTO_AUTH_TOKEN": "x"}), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(kojamees.run_login({}, "/tmp/x.env", "smart-id"), 2)
        self.assertIn("wins over", err.getvalue())


def call(rid, name, **arguments):
    return {"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": {"name": name, "arguments": arguments}}


def text(answer):
    return answer["result"]["content"][0]["text"]


CLAUDE_CODE = {"protocolVersion": "2025-06-18", "clientInfo": {"name": "claude-code", "version": "2"}, "capabilities": {"elicitation": {}}}
RESIDENTS_AFTER = {("yyriarvestus.naidud_muuda", "3"): fixture("reading_form.html").replace('name="elanike_arv" value="2"', 'name="elanike_arv" value="3"')}


class Mcp(unittest.TestCase):
    def serve(self, *messages, client=None, peer=None, env_path=None):
        """The server over these messages: its answers by id, and every line it wrote (each must be JSON)."""
        first = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
                 "params": peer or {"protocolVersion": "2025-06-18", "clientInfo": {"name": "test", "version": "1"}, "capabilities": {}}}
        stdin = io.BytesIO(b"".join(json.dumps(m, ensure_ascii=False).encode("utf-8") + b"\n" for m in [first, *messages]))
        stdout = io.BytesIO()
        self.client = client or FakeClient()
        with contextlib.redirect_stderr(io.StringIO()), mock.patch("secrets.token_urlsafe", side_effect=[f"token{i}" for i in range(1, 9)]):
            kojamees.McpServer(env_path, stdin, stdout, client=self.client).serve()
        written = [json.loads(line) for line in stdout.getvalue().splitlines()]
        return {m["id"]: m for m in written if "result" in m or "error" in m}, written

    def test_tools_match_the_snapshot_and_never_offer_login_apply_accept_or_out(self):
        answers, _ = self.serve({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = answers[1]["result"]["tools"]
        self.assertEqual(tools, json.loads(fixture("mcp_tools.json")))
        self.assertFalse({"login", "mcp"} & {t["name"] for t in tools})
        self.assertFalse({"apply", "accept", "out"} & {p for t in tools for p in t["inputSchema"]["properties"]})
        self.assertEqual(answers[0]["result"]["serverInfo"]["name"], "kojamees")

    def test_every_tool_round_trips_one_valid_call(self):
        samples = {"periods": {"json": True}, "readings": {"period": "2026-10", "json": True},
                   "check": {"period": "2026-10", "ignore": ["4:Soe vesi", "2:Küte"]}, "summary": {}, "table": {"name": "payments"},
                   "history": {"apartment": "2", "meter": "Külm vesi"}, "invoice-pdf": {"apartment": "2", "period": "2026-10"},
                   "selftest": {}, "set-reading": {"apartment": "building", "meter": "Küte", "value": "50.25", "forecast": True,
                                                   "date": "30.11.2026"},
                   "delete-reading": {"apartment": "2", "meter": "Küte"}, "forecast": {"months": 6}, "distribute": {"date": "30.11.2026"},
                   "reopen": {}, "add-expense": {"like": "heat", "fields": ["tehing[arve_nr]=101", "read[0][kogus]=5.123"]},
                   "set-residents": {"apartment": "2", "count": "3"}, "set-price": {"code": "041", "price": "-0.50"}, "new-period": {},
                   "close": {"period": "2026-10"}, "unclose": {"period": "2026-10"},
                   "import-payments": {"file": "/tmp/statement.csv", "rows": [2, 5]}}
        server = kojamees.McpServer(None, io.BytesIO(), io.BytesIO(), client=object())
        self.assertEqual(set(samples), set(server.tools))
        for name, arguments in samples.items():
            server.parsed(name, server.argv(name, dict(arguments)), arguments, apply=False)

    def test_a_value_that_reads_as_a_flag_or_has_the_wrong_type_is_refused(self):
        for rid, (name, arguments) in enumerate([("add-expense", {"like": "heat", "fields": ["--apply"]}),
                                                 ("check", {"ignore": ["--period"]}),
                                                 ("set-reading", {"apartment": "1", "meter": "Küte", "value": "--forecast"}),
                                                 ("readings", {"period": 2026}), ("import-payments", {"file": "/tmp/a.csv", "rows": ["2"]}),
                                                 ("invoice-pdf", {"apartment": "1", "out": "/etc/passwd"})], start=1):
            answers, _ = self.serve(call(rid, name, **arguments))
            self.assertTrue(answers[rid]["result"]["isError"], (name, arguments))
            self.assertEqual((self.client.posts, self.client.requests), ([], []))

    def test_both_layers_against_a_value_read_as_a_flag(self):
        server = kojamees.McpServer(None, io.BytesIO(), io.BytesIO(), client=object())
        with self.assertRaises(kojamees.UsageError):  # the dash rule, before argparse sees it
            server.argv("add-expense", {"like": "heat", "fields": ["--apply"]})
        with self.assertRaises(kojamees.UsageError):  # the parse-back check, had a flag slipped through
            server.parsed("check", ["check", "--ignore", "--json"], {"ignore": ["--json"]}, apply=False)

    def test_a_stray_print_never_reaches_the_protocol(self):
        raw = io.BytesIO()
        console = io.TextIOWrapper(raw, encoding="utf-8", write_through=True)  # kept, so raw stays open
        stdin = io.BytesIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode() + b"\n")
        with mock.patch("sys.stdout", console), \
                contextlib.redirect_stderr(io.StringIO()), \
                mock.patch.object(kojamees.McpServer, "handle", side_effect=lambda *a: print("stray") or {}):
            kojamees.McpServer(None, stdin, client=object()).serve()
        self.assertEqual(raw.getvalue(), b'{"jsonrpc":"2.0","id":1,"result":{}}\n')

    def test_a_write_applies_only_with_the_token_of_its_own_dry_run(self):
        answers, _ = self.serve(call(1, "set-residents", apartment="1", count="3"),
                                call(2, "set-residents", apartment="1", count="4", confirm="token1"),  # other arguments
                                call(3, "set-residents", apartment="1", count="3", confirm="token1"),  # a used token
                                call(4, "set-residents", apartment="1", count="3"),
                                call(5, "set-residents", apartment="1", count="3", confirm="token2"),
                                call(6, "set-residents", apartment="1", count="3", confirm="token2"),  # once
                                client=FakeClient(after=RESIDENTS_AFTER))
        self.assertIn('confirm "token1"', text(answers[1]))
        self.assertFalse(answers[1]["result"]["isError"])
        self.assertTrue(all(answers[i]["result"]["isError"] for i in (2, 3, 6)))
        self.assertIn("done, and read back from Korto", text(answers[5]))
        self.assertEqual(len(self.client.posts), 1)

    def test_an_old_token_or_a_changed_page_is_refused_with_a_new_dry_run(self):
        with mock.patch.object(kojamees, "TOKEN_LIFE", -1):
            answers, _ = self.serve(call(1, "set-residents", apartment="1", count="3"),
                                    call(2, "set-residents", apartment="1", count="3", confirm="token1"),
                                    client=FakeClient(after=RESIDENTS_AFTER))
        self.assertIn("Not applied: that confirm token is unknown, used, expired", text(answers[2]))
        self.assertEqual(self.client.posts, [])
        client = FakeClient(after=RESIDENTS_AFTER)
        stdin = io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in [call(1, "set-residents", apartment="1", count="3")]))
        stdout = io.BytesIO()
        with contextlib.redirect_stderr(io.StringIO()), mock.patch("secrets.token_urlsafe", side_effect=["t1", "t2"]):
            server = kojamees.McpServer(None, stdin, stdout, client=client)
            server.serve()
            client.html[("yyriarvestus.naidud_muuda", "3")] = fixture("reading_form.html").replace(
                'name="elanike_arv" value="2"', 'name="elanike_arv" value="4"')  # someone changed it meanwhile
            server.stdin = io.BytesIO(json.dumps(call(2, "set-residents", apartment="1", count="3", confirm="t1")).encode() + b"\n")
            server.serve()
        answer = json.loads(stdout.getvalue().splitlines()[-1])
        self.assertTrue(answer["result"]["isError"])
        self.assertIn("changed since the dry run", text(answer))
        self.assertIn('confirm "t2"', text(answer))
        self.assertEqual(client.posts, [])

    def elicit(self, *answers_after, peer=CLAUDE_CODE):
        return self.serve(call(1, "set-residents", apartment="1", count="3"),
                          call(2, "set-residents", apartment="1", count="3", confirm="token1"), *answers_after,
                          client=FakeClient(after=RESIDENTS_AFTER), peer=peer)

    def test_claude_code_asks_the_person_directly_and_only_accept_posts(self):
        answers, written = self.elicit({"jsonrpc": "2.0", "id": 99, "method": "ping"},
                                       {"jsonrpc": "2.0", "id": 98, "method": "tools/list"},
                                       {"jsonrpc": "2.0", "id": "kojamees-ask-1", "result": {"action": "accept"}})
        question = next(m for m in written if m.get("method") == "elicitation/create")
        self.assertIn("residents  2    3", question["params"]["message"])
        self.assertIn("Apply this change in Korto?", question["params"]["message"])
        self.assertEqual((answers[99]["result"], answers[98]["error"]["code"]), ({}, -32000))
        self.assertFalse(answers[2]["result"]["isError"], text(answers[2]))
        self.assertEqual(len(self.client.posts), 1)
        accept = {"jsonrpc": "2.0", "id": "kojamees-ask-1", "result": {"action": "accept"}}
        for after, why in (([{"jsonrpc": "2.0", "id": "kojamees-ask-1", "result": {"action": "decline"}}], "answered decline"),
                           ([{"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 2}}, accept], "cancelled")):
            answers, _ = self.elicit(*after)
            self.assertIn(f"Not applied: the {'person ' if why != 'cancelled' else 'call was '}{why}", text(answers[2]))
            self.assertEqual(self.client.posts, [])
        answers, written = self.elicit(peer={**CLAUDE_CODE, "clientInfo": {"name": "other"}})
        self.assertFalse(any(m.get("method") == "elicitation/create" for m in written))
        self.assertEqual(len(self.client.posts), 1)  # other clients: the token and the client's own approval

    def test_an_answer_after_the_wait_posts_nothing(self):
        with mock.patch.object(kojamees, "ELICIT_WAIT", -1):
            answers, _ = self.elicit({"jsonrpc": "2.0", "id": "kojamees-ask-1", "result": {"action": "accept"}})
        self.assertIn("after two minutes", text(answers[2]))
        self.assertEqual(self.client.posts, [])

    def test_files_stay_where_they_belong(self):
        folder = tempfile.mkdtemp()
        env_path = os.path.join(folder, "korto.env")
        with open(env_path, "w", encoding="utf-8") as f:
            f.write("KORTO_UNION_ID=7\n")
        link = os.path.join(folder, "statement.csv")
        if os.name != "nt":
            os.symlink(env_path, link)
        for rid, path in enumerate(["statement.csv", os.path.join(folder, "notes.txt")] + ([link] if os.name != "nt" else []), start=1):
            answers, _ = self.serve(call(rid, "import-payments", file=path), env_path=env_path)
            self.assertIn("full path of the bank's .csv or .xml", text(answers[rid]))
            self.assertEqual(self.client.prefills, [])  # nothing uploaded
        pdfs = os.path.join(folder, "pdfs")
        here = os.getcwd()
        with mock.patch.dict("os.environ", {"KORTO_PDF_DIR": pdfs}):
            answers, _ = self.serve(call(1, "invoice-pdf", apartment="1", period="2026-09"))
        self.assertFalse(answers[1]["result"]["isError"], text(answers[1]))
        self.assertEqual((os.listdir(pdfs), os.getcwd()), (["arve-2026-september-1.pdf"], here))

    def test_any_failure_is_answered_and_the_server_lives_on(self):
        class Broken(FakeClient):
            def get(self, screen, **params):
                raise KeyError("a column Korto renamed")
        answers, _ = self.serve(call(1, "periods"), {"jsonrpc": "2.0", "id": 2, "method": "ping"}, client=Broken())
        self.assertIn("Kojamees failed: KeyError", text(answers[1]))
        self.assertTrue(answers[1]["result"]["isError"])
        self.assertEqual(answers[2]["result"], {})

        class BrokenAfterPost(FakeClient):
            def post(self, fields, raw=False):
                if dict(fields).get("action") == "salvesta":
                    raise http.client.IncompleteRead(b"")
                return super().post(fields, raw)
        answers, _ = self.serve(call(1, "set-residents", apartment="1", count="3"),
                                call(2, "set-residents", apartment="1", count="3", confirm="token1"), client=BrokenAfterPost())
        self.assertIn("may or may not be in Korto", text(answers[2]))

        class BrokenReadBack(FakeClient):  # the save went out; reading it back hits a page the parser does not know
            def get(self, screen, **params):
                if self.posts:
                    raise KeyError("a column Korto renamed")
                return super().get(screen, **params)
        answers, _ = self.serve(call(1, "set-residents", apartment="1", count="3"),
                                call(2, "set-residents", apartment="1", count="3", confirm="token1"), client=BrokenReadBack())
        self.assertEqual(len(self.client.posts), 1)
        self.assertIn("may or may not be in Korto", text(answers[2]))
        with mock.patch.object(kojamees.McpServer, "handle", side_effect=[RuntimeError("bug"), {}]):
            answers, _ = self.serve({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.assertEqual((answers[0]["error"]["code"], answers[1]["result"]), (-32603, {}))

    def test_a_later_failure_never_claims_an_earlier_save_and_a_bad_pdf_folder_is_a_result(self):
        folder = tempfile.mkdtemp()
        env_path = os.path.join(folder, "korto.env")
        with open(env_path, "wb") as f:
            f.write(b"# T\xf5nu\nKORTO_UNION_ID=7\n")  # Latin-1, not UTF-8
        client = FakeClient()
        client.saving = True  # left over from an earlier apply
        server = kojamees.McpServer(env_path, io.BytesIO(), io.BytesIO())
        server.client = client
        with contextlib.redirect_stderr(io.StringIO()):
            code, out, err = server.run(["periods"])
        self.assertEqual(code, 2)
        self.assertNotIn("may or may not", err)
        blocked = os.path.join(folder, "file")
        open(blocked, "w").close()
        with mock.patch.dict("os.environ", {"KORTO_PDF_DIR": os.path.join(blocked, "pdfs")}):
            answers, _ = self.serve(call(1, "invoice-pdf", apartment="1", period="2026-09"))
        self.assertIn("cannot use the PDF folder", text(answers[1]))

    def test_the_token_is_checked_again_right_before_the_post(self):
        with mock.patch.object(kojamees.time, "monotonic", side_effect=[0, 100, 1000]):
            answers, _ = self.serve(call(1, "set-residents", apartment="1", count="3"),
                                    call(2, "set-residents", apartment="1", count="3", confirm="token1"),
                                    client=FakeClient(after=RESIDENTS_AFTER))
        self.assertIn("token expired while Korto was read", text(answers[2]))
        self.assertEqual(self.client.posts, [])

    def test_a_login_made_after_the_server_started_is_seen_and_a_relative_path_works(self):
        folder = tempfile.mkdtemp()
        here = os.getcwd()
        os.chdir(folder)
        try:
            server = kojamees.McpServer("korto.env", io.BytesIO(), io.BytesIO(), missing_ok=True)
            with mock.patch.dict("os.environ", {}, clear=True):
                self.assertEqual(server.env().get("KORTO_AUTH_TOKEN"), None)  # no file yet: the environment only
                with open("korto.env", "w", encoding="utf-8") as f:
                    f.write("KORTO_AUTH_TOKEN=tok\nKORTO_UNION_ID=7\n")
                os.chdir(tempfile.mkdtemp())  # as the PDF folder does
                self.assertEqual(server.env().get("KORTO_AUTH_TOKEN"), "tok")
                self.assertEqual(server.current_client().token, "tok")
        finally:
            os.chdir(here)

    def test_utf8_in_and_an_ended_login_answered_not_asked(self):
        answers, _ = self.serve(call(1, "history", apartment="1", meter="Külm vesi"))
        self.assertIn("Külm vesi", text(answers[1]))

        class Ended(FakeClient):
            def get(self, screen, **params):
                raise kojamees.SessionExpired(kojamees.ENDED)
        with mock.patch("builtins.input", side_effect=AssertionError("no question")), mock.patch("sys.stdin.isatty", return_value=True):
            answers, _ = self.serve(call(2, "periods"), client=Ended())
        self.assertIn("kojamees login", text(answers[2]))
        self.assertTrue(answers[2]["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
