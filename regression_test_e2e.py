import requests
import re
import sys
import time

def run_tests():
    session = requests.Session()
    login_url = 'https://intermilan.statik.my.id/accounts/login/'
    
    # Allow time for deploy
    print("Waiting 5s for any deploy to finish...")
    time.sleep(5)
    
    response = session.get(login_url)

    # Extract CSRF token
    csrf_match = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', response.text)
    if not csrf_match:
        print('Failed to get CSRF token from login page. Is it up?', response.status_code)
        sys.exit(1)
    csrf_token = csrf_match.group(1)

    # Login as admin
    login_data = {
        'csrfmiddlewaretoken': csrf_token,
        'username': 'admin',
        'password': 'adminbps2026'
    }
    response = session.post(login_url, data=login_data, headers={'Referer': login_url})
    
    # The default redirect after login is often / or we stay on login if fail
    if 'admin' not in response.text and response.status_code == 200 and 'login' in response.url:
        print('Login failed. Output excerpt:', response.text[:200])
        sys.exit(1)
    print('Login successful as admin')

    # Sync Passwords
    sync_url = 'https://intermilan.statik.my.id/maintenance/sync-passwords/'
    response = session.get(sync_url)
    
    csrf_match = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', response.text)
    if not csrf_match:
        print('Failed to get CSRF token on sync page. Response:', response.text[:200])
        sys.exit(1)
    csrf_token = csrf_match.group(1)
    
    response = session.post(sync_url, data={'csrfmiddlewaretoken': csrf_token}, headers={'Referer': sync_url})
    if 'SINKRONISASI BERHASIL' in response.text:
        print('Sync Passwords SUCCESS')
    else:
        print('Sync Passwords FAILED. Response excerpt:', response.text[:500])

    # Seed Master Akun
    seed_url = 'https://intermilan.statik.my.id/maintenance/seed-master-akun/'
    response = session.get(seed_url)
    csrf_match = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', response.text)
    if not csrf_match:
        print('Failed to get CSRF token on seed page.')
        sys.exit(1)
    csrf_token = csrf_match.group(1)
    
    response = session.post(seed_url, data={'csrfmiddlewaretoken': csrf_token}, headers={'Referer': seed_url})
    if 'IMPORT BERHASIL' in response.text:
        print('Seed Master Akun SUCCESS')
    else:
        print('Seed Master Akun FAILED. Response excerpt:', response.text[:500])

    # Logout admin
    session.get('https://intermilan.statik.my.id/accounts/logout/')
    
    print('Testing login for all users...')
    users = [
        ('KK_1300', 'adminbps1300'),
        ('KK_1301', 'adminbps301'),
        ('KK_1302', 'adminbps1302'),
        ('KK_1303', 'adminbps1303'),
        ('KK_1304', 'adminbps1304'),
        ('KK_1305', 'adminbps1305'),
        ('KK_1306', 'adminbps306'),
        ('KK_1307', 'adminbps1307'),
        ('KK_1308', 'adminbps1308'),
        ('KK_1309', 'adminbps1309'),
        ('KK_1310', 'adminbps1310'),
        ('KK_1311', 'adminbps1311'),
        ('KK_1312', 'adminbps1312'),
    ]
    
    for username, password in users:
        session = requests.Session()
        res = session.get(login_url)
        csrf = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', res.text).group(1)
        res = session.post(login_url, data={'csrfmiddlewaretoken': csrf, 'username': username, 'password': password}, headers={'Referer': login_url})
        if username in res.text or res.url.endswith('/'):
            print(f'Login as {username} SUCCESS')
        else:
            print(f'Login as {username} FAILED')
        session.get('https://intermilan.statik.my.id/accounts/logout/')

if __name__ == '__main__':
    run_tests()
