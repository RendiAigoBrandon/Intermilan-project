import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'intermilan_project.settings.production')
django.setup()

from django.db import connection

print('AUDIT ALL MODELS - BEFORE CLEANING')
print('=' * 60)

with connection.cursor() as cursor:
    cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name")
    tables = [row[0] for row in cursor.fetchall()]

    # Count each table
    counts = {}
    for t in tables:
        cursor.execute(f'SELECT COUNT(*) FROM {t}')
        counts[t] = cursor.fetchone()[0]

    # Categorize
    dk = {t: counts[t] for t in tables if t.startswith('dk_')}
    sp2d = {t: counts[t] for t in tables if t.startswith('sp2d_')}
    drpp = {t: counts[t] for t in tables if t.startswith('drpp_')}
    docs = {t: counts[t] for t in tables if t.startswith('documents_')}
    paket = {t: counts[t] for t in tables if t.startswith('paket_')}
    core = {t: counts[t] for t in tables if t.startswith('core_')}
    auth = {t: counts[t] for t in tables if t.startswith('auth_')}
    acc = {t: counts[t] for t in tables if t.startswith('accounts_')}
    dj_session = {t: counts[t] for t in tables if t.startswith('django_session')}

    print('DK TABLES:')
    for t, c in dk.items():
        print(f'  {t}: {c}')

    print()
    print('SP2D TABLES:')
    for t, c in sp2d.items():
        print(f'  {t}: {c}')

    print()
    print('DRPP TABLES:')
    for t, c in drpp.items():
        print(f'  {t}: {c}')

    print()
    print('DOCUMENTS TABLES:')
    for t, c in docs.items():
        print(f'  {t}: {c}')

    print()
    print('PAKET_SPM TABLES:')
    for t, c in paket.items():
        print(f'  {t}: {c}')

    print()
    print('CORE TABLES:')
    for t, c in core.items():
        print(f'  {t}: {c}')

    print()
    print('AUTH TABLES:')
    for t, c in auth.items():
        print(f'  {t}: {c}')

    print()
    print('ACCOUNTS TABLES:')
    for t, c in acc.items():
        print(f'  {t}: {c}')

    print()
    print('DJANGO_SESSION:')
    for t, c in dj_session.items():
        print(f'  {t}: {c}')
