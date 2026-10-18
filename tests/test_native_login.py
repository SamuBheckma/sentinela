import json
from pathlib import Path
import sqlite3
import time
from unittest.mock import Mock

import pytest

from sentinela import native_login as native


def make_profile(tmp_path, version=15):
    profile = tmp_path / 'profile'
    profile.mkdir()
    with sqlite3.connect(profile / 'cookies.sqlite') as connection:
        connection.execute('CREATE TABLE moz_cookies (name, value, host, path, expiry, isSecure, isHttpOnly, sameSite, originAttributes)')
        connection.execute(f'PRAGMA user_version={version}')
        expires = (int(time.time()) + 3600) * (1000 if version >= 16 else 1)
        rows = [
            ('session', 'ml-secret', '.mercadolivre.com.br', '/', expires, 1, 1, 1, ''),
            ('subdomain', 'ml-other', 'www.mercadolivre.com.br', '/', expires, 1, 0, 256, ''),
            ('google', 'google-secret', '.google.com', '/', expires, 1, 1, 0, ''),
            ('fake', 'fake-secret', 'mercadolivre.com.br.evil.org', '/', expires, 1, 1, 0, ''),
            ('container', 'container-secret', '.mercadolivre.com.br', '/', expires, 1, 1, 0, '^userContextId=1'),
            ('old', 'expired-secret', '.mercadolivre.com.br', '/', 1, 1, 1, 2, ''),
        ]
        connection.executemany('INSERT INTO moz_cookies VALUES (?,?,?,?,?,?,?,?,?)', rows)
    return profile


@pytest.mark.parametrize('version', [15, 16, 17])
def test_export_is_scoped_readonly_and_private(tmp_path, version):
    profile = make_profile(tmp_path, version)
    database = profile / 'cookies.sqlite'
    before = database.read_bytes()
    destination = tmp_path / '.secrets/state.json'
    assert native.export_firefox_session(profile, destination) == 2
    state = json.loads(destination.read_text())
    assert state['origins'] == []
    assert {c['name'] for c in state['cookies']} == {'session', 'subdomain'}
    assert state['cookies'][0]['sameSite'] == 'Lax'
    assert 'sameSite' not in state['cookies'][1]
    assert time.time() < state['cookies'][0]['expires'] < time.time() + 3601
    assert destination.stat().st_mode & 0o777 == 0o600
    assert database.read_bytes() == before
    assert not list(destination.parent.glob('.ml-state-*'))


def test_linux_delegates_only_url_to_default_browser(monkeypatch):
    monkeypatch.setattr(native.sys, 'platform', 'linux')
    monkeypatch.setattr(native.shutil, 'which', lambda _: '/usr/bin/xdg-open')
    run = Mock()
    monkeypatch.setattr(native.subprocess, 'run', run)
    native.open_default_browser()
    run.assert_called_once_with(['xdg-open', native.URL], check=True, timeout=30)


def test_cancel_does_not_read_profile_or_overwrite_state(tmp_path, monkeypatch):
    destination = tmp_path / 'state.json'
    destination.write_text('existing')
    monkeypatch.setattr(native, 'open_default_browser', Mock())
    monkeypatch.setattr('builtins.input', lambda _: '')
    export = Mock()
    monkeypatch.setattr(native, 'export_firefox_session', export)
    monkeypatch.setattr(native, 'firefox_profiles', Mock(side_effect=AssertionError('no profile access')))
    native.login(destination=destination)
    export.assert_not_called()
    assert destination.read_text() == 'existing'


def test_explicit_export_uses_selected_profile(tmp_path, monkeypatch, capsys):
    profile = make_profile(tmp_path)
    destination = tmp_path / 'state.json'
    monkeypatch.setattr(native, 'open_default_browser', Mock())
    monkeypatch.setattr('builtins.input', lambda _: 'EXPORTAR')
    native.login(profile=profile, destination=destination)
    assert json.loads(destination.read_text())['cookies']
    output = capsys.readouterr().out
    assert 'ml-secret' not in output
    assert 'google-secret' not in output


def test_profile_discovery_from_flatpak_ini(tmp_path):
    root = tmp_path / '.var/app/org.mozilla.firefox/.mozilla/firefox'
    profile = root / 'personal'
    profile.mkdir(parents=True)
    (profile / 'cookies.sqlite').touch()
    (root / 'profiles.ini').write_text('[Profile0]\nPath=personal\nIsRelative=1\n')
    assert native.firefox_profiles(tmp_path) == [profile]


def test_missing_database_preserves_existing_state(tmp_path):
    destination = tmp_path / 'state.json'
    destination.write_text('existing')
    with pytest.raises(ValueError, match='sem cookies.sqlite'):
        native.export_firefox_session(tmp_path / 'missing', destination)
    assert destination.read_text() == 'existing'
    assert not (tmp_path / 'missing').exists()


def test_no_cookies_preserves_existing_state(tmp_path):
    profile = make_profile(tmp_path)
    with sqlite3.connect(profile / 'cookies.sqlite') as connection:
        connection.execute("DELETE FROM moz_cookies WHERE host LIKE '%mercadolivre%'")
    destination = tmp_path / 'state.json'
    destination.write_text('existing')
    with pytest.raises(ValueError, match='Nenhum cookie válido'):
        native.export_firefox_session(profile, destination)
    assert destination.read_text() == 'existing'
