import json
from pathlib import Path
import sqlite3
import subprocess
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
    launch = Mock()
    monkeypatch.setattr(native.subprocess, 'Popen', launch)
    native.open_default_browser()
    launch.assert_called_once_with(
        ['/usr/bin/xdg-open', native.URL], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, close_fds=True,
    )


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
    monkeypatch.setattr(native, 'check_session_access', lambda _: 'accessible')
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


@pytest.mark.parametrize('url,html,expected', [
    (native.URL, '<div class="poly-card poly-card--list">', 'accessible'),
    ('https://www.mercadolivre.com.br/gz/account-verification', '', 'requires_login'),
    (native.URL, '<html>Carregando</html>', 'unverified'),
])
def test_access_check_reports_access_and_verification(tmp_path, monkeypatch, url, html, expected):
    destination = tmp_path / 'state.json'
    destination.write_text(json.dumps({'cookies': []}))
    response = Mock()
    response.url = url
    response.read.return_value = html.encode()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(native, 'build_opener', lambda _: opener)
    assert native.check_session_access(destination) == expected


def test_network_error_does_not_claim_user_is_logged_out(tmp_path, monkeypatch):
    destination = tmp_path / 'state.json'
    destination.write_text(json.dumps({'cookies': []}))
    opener = Mock()
    opener.open.side_effect = OSError('offline')
    monkeypatch.setattr(native, 'build_opener', lambda _: opener)
    assert native.check_session_access(destination) == 'unverified'



def test_export_prompt_does_not_wait_for_browser_to_exit(tmp_path, monkeypatch):
    launcher = tmp_path / 'xdg-open'
    launcher.write_text('#!/bin/sh\nexec sleep 30\n')
    launcher.chmod(0o700)
    monkeypatch.setattr(native.sys, 'platform', 'linux')
    monkeypatch.setattr(native.shutil, 'which', lambda _: str(launcher))
    real_popen = subprocess.Popen
    processes = []

    def launch(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    def cancel_at_prompt(prompt):
        assert 'EXPORTAR' in prompt
        assert processes[0].poll() is None
        return ''

    monkeypatch.setattr(native.subprocess, 'Popen', launch)
    monkeypatch.setattr('builtins.input', cancel_at_prompt)
    try:
        start = time.monotonic()
        native.login()
        assert time.monotonic() - start < 2
    finally:
        for process in processes:
            process.terminate()
            process.wait(timeout=5)


def test_browser_launch_failure_is_actionable(monkeypatch):
    monkeypatch.setattr(native.sys, 'platform', 'linux')
    monkeypatch.setattr(native.shutil, 'which', lambda _: '/usr/bin/xdg-open')
    monkeypatch.setattr(native.subprocess, 'Popen', Mock(side_effect=OSError('failed')))
    with pytest.raises(ValueError, match='Abra manualmente'):
        native.open_default_browser()



def test_exclusive_firefox_lock_is_identified_without_overwriting_state(tmp_path):
    profile = make_profile(tmp_path)
    destination = tmp_path / 'state.json'
    destination.write_text('existing')
    with sqlite3.connect(profile / 'cookies.sqlite') as writer:
        writer.execute('BEGIN EXCLUSIVE')
        with pytest.raises(native.FirefoxDatabaseBusy, match='bloqueado'):
            native.export_firefox_session(profile, destination)
    assert destination.read_text() == 'existing'


def test_retry_after_browser_closes_does_not_reopen_browser(tmp_path, monkeypatch):
    profile = make_profile(tmp_path)
    destination = tmp_path / 'state.json'
    open_browser = Mock()
    monkeypatch.setattr(native, 'open_default_browser', open_browser)
    answers = iter(['EXPORTAR', ''])
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    export = Mock(side_effect=[native.FirefoxDatabaseBusy(), 2])
    monkeypatch.setattr(native, 'export_firefox_session', export)
    monkeypatch.setattr(native, 'check_session_access', lambda _: 'accessible')
    native.login(profile=profile, destination=destination)
    assert export.call_count == 2
    open_browser.assert_called_once()


def test_export_only_never_opens_browser(tmp_path, monkeypatch):
    profile = make_profile(tmp_path)
    open_browser = Mock()
    monkeypatch.setattr(native, 'open_default_browser', open_browser)
    monkeypatch.setattr('builtins.input', lambda _: 'EXPORTAR')
    monkeypatch.setattr(native, 'check_session_access', lambda _: 'accessible')
    native.login(profile=profile, destination=tmp_path/'state.json', export_only=True)
    open_browser.assert_not_called()


def test_cancel_busy_retry_preserves_state(tmp_path, monkeypatch):
    destination = tmp_path / 'state.json'
    destination.write_text('existing')
    monkeypatch.setattr(native, 'open_default_browser', Mock())
    answers = iter(['EXPORTAR', 'C'])
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    monkeypatch.setattr(native, 'export_firefox_session', Mock(side_effect=native.FirefoxDatabaseBusy()))
    native.login(profile=tmp_path, destination=destination)
    assert destination.read_text() == 'existing'
