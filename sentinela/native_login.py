"""Login no navegador do usuário; nenhuma instalação ou controle de navegador."""
import argparse
import configparser
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import webbrowser

URL = 'https://www.mercadolivre.com.br/ofertas'


def open_default_browser():
    if sys.platform.startswith('linux'):
        if not shutil.which('xdg-open'):
            raise ValueError('xdg-open não está disponível. Abra manualmente: ' + URL)
        try:
            subprocess.run(['xdg-open', URL], check=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
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
    except sqlite3.Error:
        raise ValueError('Não foi possível ler os cookies do Firefox. Aguarde a gravação do login ou feche o Firefox e tente novamente.') from None
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


def login(*, profile=None, destination=None):
    print('Abrindo Mercado Livre no navegador padrão do sistema. Nenhum navegador será instalado.', flush=True)
    open_default_browser()
    print('Conclua o login na janela normal do navegador e aguarde os produtos aparecerem.')
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
    count = export_firefox_session(profile, destination)
    print(f'{count} cookies do Mercado Livre exportados para {destination} (permissão 600).')
    print('A exportação não confirma acesso: valide a coleta em dry-run. Não compartilhe este arquivo no chat ou Git.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
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
        login(profile=args.profile, destination=destination)
    except (ValueError, OSError, EOFError, KeyboardInterrupt, configparser.Error) as exc:
        print(f'Login não concluído: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
