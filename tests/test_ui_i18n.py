from sensors_dcs.ui_i18n import assert_parity, inject_i18n_json, catalog_json
from sensors_dcs.viz import PREVIEW_HTML, ERROR_HTML
from sensors_dcs.login_page import LOGIN_HTML
import json
import re


def test_catalog_parity() -> None:
    assert_parity()


def test_inject_preview_and_error() -> None:
    p = inject_i18n_json(PREVIEW_HTML)
    e = inject_i18n_json(ERROR_HTML)
    assert "__DCS_I18N_JSON__" not in p
    assert "__DCS_I18N_JSON__" not in e
    assert "sensors-dcs.locale" in p
    assert 'data-i18n="tab.collect"' in p
    assert "JSON.parse('__DCS_I18N" not in p
    assert "const DCS_I18N =" in p


def test_inject_is_valid_js_object_literal() -> None:
    for html in (PREVIEW_HTML, ERROR_HTML, LOGIN_HTML):
        out = inject_i18n_json(html)
        assert "JSON.parse('__DCS_I18N" not in out
        m = re.search(r"const DCS_I18N = (\{);", out)
        # greedy match of balanced-ish JSON: from first { after assignment until }; before next statement
        m = re.search(r"const DCS_I18N = (\{.*?\n?\});[\s\n]*const LS_LOCALE", out, re.S)
        if not m:
            m = re.search(r"const DCS_I18N = (\{.*\});", out, re.S)
        assert m, "DCS_I18N assignment missing"
        raw = m.group(1).replace("\\/", "/")
        data = json.loads(raw)
        assert set(data["zh"]) == set(data["en"])
        hint = data["en"]["pp.episode_hint"]
        assert "data_new" in hint
        # backslashes survived (Windows path form)
        assert "\\" in hint or "data_new" in hint
    assert "data_new" in catalog_json()
