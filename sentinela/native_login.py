"""Login no navegador do usuário; nenhuma instalação ou controle de navegador."""
import argparse
import configparser
from contextlib import closing
import json
import http.cookiejar
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import webbrowser
import re
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPCookieProcessor, Request

URL = 'https://www.mercadolivre.com.br/ofertas'


class FirefoxDatabaseBusy(ValueError):
    """O Firefox mantém o banco bloqueado; requer fechamento pelo usuário."""



def open_default_browser():
    if sys.platform.startswith('linux'):
        opener = shutil.which('xdg-open')
        if not opener:
            raise ValueError('xdg-open não está disponível. Abra manualmente: ' + URL)
        try:
            # Alguns lançadores permanecem ativos enquanto o navegador está aberto.
            # Não aguardar nem compartilhar o terminal com esse processo.
            subprocess.Popen(
                [opener, URL], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
            )
        except OSError:
            raise ValueError('Não foi possível abrir o navegador padrão. Abra manualmente: ' + URL) from None
    elif not webbrowser.open(URL):
        raise ValueError('Não foi possível abrir o navegador padrão. Abra manualmente: ' + URL)


def firefox_profiles(home=None):
    home = Path.home() if home is None else Path(home)
    roots = [home / '.mozilla/firefox', home / '.config/mozilla/firefox',
             home / '.var/app/org.mozilla.firefox/.mozilla/firefox',
             home / 'snap/firefox/common/.mozilla/firefox']
    profiles = []
    for root in roots:
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(root / 'profiles.ini')
        for section in parser.sections():
            if not section.startswith('Profile') or not parser.has_option(section, 'Path'):
                continue
            path = Path(parser.get(section, 'Path')).expanduser()
            if parser.get(section, 'IsRelative', fallback='1') == '1':
                path = root / path
            if (path / 'cookies.sqlite').is_file() and path not in profiles:
                profiles.append(path)
    return profiles


def export_firefox_session(profile, destination):
    database = Path(profile).expanduser() / 'cookies.sqlite'
    if not database.is_file():
        raise ValueError('Perfil sem cookies.sqlite. Consulte about:profiles no Firefox para localizar o diretório raiz.')
    try:
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as connection:
            # v16 migrou expiry de segundos para milissegundos no Firefox.
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            rows = connection.execute("""
                SELECT name, value, host, path, expiry, isSecure, isHttpOnly, sameSite
                FROM moz_cookies
                WHERE (host IN ('mercadolivre.com.br', '.mercadolivre.com.br')
                       OR host LIKE '%.mercadolivre.com.br') AND originAttributes = ''
            """).fetchall()
    except sqlite3.Error as exc:
        code = getattr(exc, 'sqlite_errorcode', 0) & 0xff
        if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise FirefoxDatabaseBusy('O Firefox está mantendo o banco de cookies bloqueado.') from None
        error_name = getattr(exc, 'sqlite_errorname', 'SQLITE_ERROR')
        raise ValueError(f'Não foi possível ler os cookies do perfil {database.parent} ({error_name}). Confira o diretório raiz em about:profiles e as permissões de leitura.') from None
    cookies = []
    for name, value, host, path, expiry, secure, http_only, same_site in rows:
        expiry = expiry / 1000 if version >= 16 else expiry
        if expiry and expiry <= time.time():
            continue
        cookie = dict(name=name, value=value, domain=host, path=path,
                      expires=expiry or -1, secure=bool(secure), httpOnly=bool(http_only))
        if same_site in (0, 1, 2):
            cookie['sameSite'] = {0: 'None', 1: 'Lax', 2: 'Strict'}[same_site]
        cookies.append(cookie)
    if not cookies:
        raise ValueError('Nenhum cookie válido do Mercado Livre neste perfil. Faça login numa janela normal, sem modo privado ou contêiner, e tente novamente.')
    destination = Path(destination).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='.ml-state-', dir=destination.parent)
    try:
        with os.fdopen(descriptor, 'w') as output:
            json.dump({'cookies': cookies, 'origins': []}, output)
            output.write('\n')
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return len(cookies)


def check_session_access(destination):
    """Verifica a vitrine com os cookies exportados; não controla a janela aberta."""
    state = json.loads(Path(destination).expanduser().read_text())
    jar = http.cookiejar.CookieJar()
    for cookie in state['cookies']:
        domain = cookie['domain']
        if not (domain.lstrip('.') == 'mercadolivre.com.br' or domain.endswith('.mercadolivre.com.br')):
            continue
        expiry = cookie.get('expires', -1)
        jar.set_cookie(http.cookiejar.Cookie(
            version=0, name=cookie['name'], value=cookie['value'],
            port=None, port_specified=False, domain=domain,
            domain_specified=domain.startswith('.'), domain_initial_dot=domain.startswith('.'),
            path=cookie.get('path', '/'), path_specified=True,
            secure=cookie.get('secure', True), expires=None if expiry <= 0 else int(expiry),
            discard=expiry <= 0, comment=None, comment_url=None, rest={},
        ))
    opener = build_opener(HTTPCookieProcessor(jar))
    try:
        with opener.open(Request(URL, headers={'User-Agent': 'Sentinela/0.1'}), timeout=15) as response:
            url = urlsplit(response.url)
            if url.hostname not in {'www.mercadolivre.com.br', 'mercadolivre.com.br'} or any(
                part in url.path for part in ('account-verification', '/login', '/registration', '/captcha')
            ):
                return 'requires_login'
            html = response.read(2_000_000).decode('utf-8', errors='replace')
    except (OSError, URLError):
        return 'unverified'
    if re.search(r'class=[\"\'][^\"\']*\bpoly-card\b', html):
        return 'accessible'
    return 'unverified'


def login(*, profile=None, destination=None, export_only=False):
    if not export_only:
        print('Abrindo Mercado Livre no navegador padrão do sistema. Nenhum navegador será instalado.', flush=True)
        open_default_browser()
    else:
        print('Exportação da sessão existente, sem abrir o navegador.')
    print('Se os produtos já aparecem e sua conta está logada, não precisa fazer login novamente.')
    print('Caso contrário, conclua o login na janela aberta. Depois volte a este terminal.')
    print('O lançador abre seu navegador, mas não controla nem lê a janela; verifica o acesso após a exportação.')
    print('A exportação abaixo suporta Firefox instalado no sistema, inclusive Flatpak e Snap.')
    answer = input('Para exportar SOMENTE os cookies do Mercado Livre do Firefox, digite EXPORTAR (Enter cancela): ')
    if answer.strip() != 'EXPORTAR':
        print('Exportação cancelada. Nenhuma sessão foi lida ou alterada.')
        return
    if profile is None:
        profiles = firefox_profiles()
        if len(profiles) == 1:
            profile = profiles[0]
        elif profiles:
            for number, path in enumerate(profiles, 1):
                print(f'{number}: {path}')
            try:
                number = int(input('Número do perfil usado no login: '))
                if not 1 <= number <= len(profiles):
                    raise ValueError
                profile = profiles[number - 1]
            except ValueError:
                raise ValueError('Perfil inválido. Sessão não exportada.') from None
        else:
            raise ValueError('Perfil Firefox não encontrado. Execute com --profile DIRETORIO_RAIZ (veja about:profiles no Firefox).')
    destination = destination or os.getenv('ML_STORAGE_STATE_FILE', '.secrets/ml_state.json')
    for attempt in range(3):
        try:
            count = export_firefox_session(profile, destination)
            break
        except FirefoxDatabaseBusy:
            if attempt == 2:
                raise ValueError('O banco continua bloqueado. Feche todas as janelas do Firefox e execute bash scripts/login_mercadolivre.sh --export-only.') from None
            print('O Firefox está bloqueando o banco de cookies (SQLITE_BUSY/SQLITE_LOCKED).')
            print('Feche todas as janelas do Firefox normalmente para gravar e liberar a sessão. Não é preciso sair da conta.')
            answer = input('Depois de fechar o Firefox, pressione Enter para tentar novamente (C cancela): ')
            if answer.strip().lower() == 'c':
                print('Exportação cancelada. A sessão anterior foi preservada.')
                return
    print(f'{count} cookies do Mercado Livre exportados para {destination} (permissão 600).')
    print('Verificando o acesso à vitrine com a sessão exportada...', flush=True)
    status = check_session_access(destination)
    if status == 'accessible':
        print('Acesso à vitrine confirmado com a sessão exportada. Não é necessário outro login.')
    elif status == 'requires_login':
        print('A sessão foi exportada, mas o acesso fora da janela recebeu uma verificação de conta. Sua janela pode continuar logada; confira se escolheu o mesmo perfil. O coletor ainda pode exigir verificação.')
    else:
        print('Sessão exportada; não foi possível confirmar o acesso por HTTP. Isso não significa que você esteja deslogado. Valide a coleta em dry-run.')
    print('Não compartilhe o arquivo de sessão no chat ou Git.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export-only', action='store_true', help='exporta uma sessão existente sem abrir o navegador')
    parser.add_argument('--profile', help='diretório raiz do perfil Firefox usado no login')
    parser.add_argument('--state-file', help='destino do JSON; padrão .secrets/ml_state.json')
    args = parser.parse_args()
    # O lançador não depende de dotenv nem instala bibliotecas. Só lê esta opção.
    try:
        destination = args.state_file or os.getenv('ML_STORAGE_STATE_FILE')
        if destination is None and Path('.env').is_file():
            for line in Path('.env').read_text().splitlines():
                key, separator, value = line.strip().partition('=')
                if separator and key.strip() == 'ML_STORAGE_STATE_FILE':
                    destination = value.strip().strip('\"\'')
        login(profile=args.profile, destination=destination, export_only=args.export_only)
    except (ValueError, OSError, EOFError, KeyboardInterrupt, configparser.Error) as exc:
        print(f'Login não concluído: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
