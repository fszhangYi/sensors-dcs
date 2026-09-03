from sensors_dcs.ui_i18n import assert_parity, inject_i18n_json
from sensors_dcs.viz import PREVIEW_HTML, ERROR_HTML


def test_catalog_parity() -> None:
    assert_parity()


def test_inject_preview_and_error() -> None:
    p = inject_i18n_json(PREVIEW_HTML)
    e = inject_i18n_json(ERROR_HTML)
    assert "__DCS_I18N_JSON__" not in p
    assert "__DCS_I18N_JSON__" not in e
    assert "sensors-dcs.locale" in p
    assert "data-i18n=\"tab.collect\"" in p
