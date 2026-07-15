"""The opt-in install ping's fire/skip decision (`_should_ping`).

Pure decision logic — no network. Verifies it fires on first opt-in and once per
version thereafter (so updates are counted) while respecting opt-out/unset.
"""

from bpm_tagger.install_ping import _should_ping

URL = "https://example.goatcounter.com/count"


def _cfg(**over):
    base = {"install_ping_consent": True, "install_ping_url": URL,
            "install_ping_version": ""}
    base.update(over)
    return base


def test_fires_on_first_optin():
    assert _should_ping(_cfg(), "2.6.2") is True


def test_fires_after_update_to_new_version():
    assert _should_ping(_cfg(install_ping_version="2.6.1"), "2.6.2") is True


def test_skips_when_version_already_pinged():
    assert _should_ping(_cfg(install_ping_version="2.6.2"), "2.6.2") is False


def test_skips_when_opted_out():
    assert _should_ping(_cfg(install_ping_consent=False), "2.6.2") is False


def test_skips_when_not_yet_asked():
    assert _should_ping(_cfg(install_ping_consent=None), "2.6.2") is False


def test_skips_when_no_url():
    assert _should_ping(_cfg(install_ping_url=""), "2.6.2") is False
