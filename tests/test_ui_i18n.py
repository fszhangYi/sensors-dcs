from sensors_dcs.ui_i18n import assert_parity, inject_i18n_json, catalog_json
from sensors_dcs.viz import PREVIEW_HTML
from sensors_dcs.login_page import LOGIN_HTML
import json
import re


def test_catalog_parity() -> None:
    assert_parity()


def test_inject_preview_and_login() -> None:
    p = inject_i18n_json(PREVIEW_HTML)
    assert "__DCS_I18N_JSON__" not in p
    assert "sensors-dcs.locale" in p
    assert 'data-i18n="tab.collect"' in p
    assert 'id="tabBtnHome"' in p
    assert 'id="tabBtnPost"' in p
    assert "tab-home" in p
    assert "tab-post" in p
    assert "JSON.parse('__DCS_I18N" not in p
    assert "const DCS_I18N =" in p
    # Default landing tab is home
    assert 'id="tab-home" role="tabpanel"' in p
    assert 'id="tabBtnHome"' in p and 'data-tab="home"' in p
    assert "switchTab('home')" in p or 'switchTab("home")' in p
    assert 'id="bootBanner"' in p
    assert 'data-i18n="infer.page_flow"' in p
    assert 'id="infPageFlow"' in p
    assert "/assets/flow_editor.js" in p
    assert "/assets/flow_runtime.js" in p
    assert "/assets/flow.css" in p


def test_inject_is_valid_js_object_literal() -> None:
    for html in (PREVIEW_HTML, LOGIN_HTML):
        out = inject_i18n_json(html)
        assert "JSON.parse('__DCS_I18N" not in out
        m = re.search(r"const DCS_I18N = (\{.*\});[\s\n]*const LS_LOCALE", out, re.S)
        if not m:
            m = re.search(r"const DCS_I18N = (\{.*\});", out, re.S)
        assert m, "DCS_I18N assignment missing"
        raw = m.group(1).replace("\\/", "/")
        data = json.loads(raw)
        assert set(data["zh"]) == set(data["en"])
        hint = data["en"]["pp.episode_hint"]
        assert "data_new" in hint
        assert "\\" in hint or "data_new" in hint
        assert "boot.collect_locked" in data["zh"]
    assert "data_new" in catalog_json()
