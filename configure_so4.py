"""Create/use a separate LOCAL MySQL database; never connect to Aiven/Neo4j."""
from getpass import getpass
from pathlib import Path
import os
import re
import secrets
import pymysql
from sqlalchemy.engine import URL
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent


def main():
    existing = ROOT / '.env.personalization'
    if existing.exists():
        values = dotenv_values(existing, interpolate=False)
        os.environ.update({k: v for k, v in values.items() if k.startswith('SO4_') and v is not None})
        print('Using existing private SO4 settings. No existing records will be deleted.')
    else:
        print('Separate local SO4 setup. This does not use the hosted website database.')
        host = input('Local MySQL host [127.0.0.1]: ').strip() or '127.0.0.1'
        if host not in {'127.0.0.1', 'localhost', '::1'}:
            raise ValueError('This setup is for local MySQL only.')
        port = int(input('Local MySQL port [3306]: ').strip() or '3306')
        user = input('MySQL username [root]: ').strip() or 'root'
        password = getpass('Your existing local MySQL password (hidden): ')
        name = input('Separate database name [dlsu_so4_personalization]: ').strip() or 'dlsu_so4_personalization'
        if not re.fullmatch(r'dlsu_so4_[a-zA-Z0-9_]{1,45}', name):
            raise ValueError('Use a separate name beginning dlsu_so4_. No existing website database is accepted.')
        conn = pymysql.connect(host=host, port=port, user=user, password=password, autocommit=True)
        try:
            with conn.cursor() as cur:
                cur.execute('CREATE DATABASE IF NOT EXISTS `' + name + '` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
                cur.execute('SHOW TABLES FROM `' + name + '`')
                if any(not row[0].startswith('so4_') for row in cur.fetchall()):
                    raise ValueError('That database contains unrelated tables. Choose a new dlsu_so4_ database.')
        finally: conn.close()
        values = {'SO4_DATABASE_URL': URL.create('mysql+pymysql', username=user, password=password, host=host,
                    port=port, database=name, query={'charset': 'utf8mb4'}).render_as_string(hide_password=False),
                  'SO4_SESSION_KEY': secrets.token_hex(32), 'SO4_DEFAULT_UNIT_LIMIT': '21', 'SO4_PORT': '5002'}
        # JSON double quoting is compatible with python-dotenv and keeps special chars literal.
        import json
        existing.write_text(''.join(k + '=' + json.dumps(v) + '\n' for k, v in values.items()), encoding='utf-8')
        os.environ.update(values)
    from personalization.app import create_app, db, seed_catalog
    app = create_app()
    with app.app_context(): db.create_all(); seed_catalog()
    print('Local MySQL setup finished. Open start_so4.bat, then http://127.0.0.1:5002/.')
    print('No accounts or records from the deployed website were imported.')


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print('Setup stopped (' + type(exc).__name__ + '). Check your local MySQL service, username/password and separate database name.')
        raise SystemExit(1)
