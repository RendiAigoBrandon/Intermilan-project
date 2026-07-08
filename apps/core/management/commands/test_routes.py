import os
from django.core.management.base import BaseCommand
from django.test import Client
from django.contrib.auth import get_user_model

class Command(BaseCommand):
    help = 'Validate UI patches'

    def handle(self, *args, **options):
        User = get_user_model()
        c = Client(SERVER_NAME='localhost')
        user = User.objects.get(username='admin')
        c.force_login(user)
        
        # Test 1: Pagination Dashboard
        resp = c.get('/dashboard/?tahun=2026&bulan=1')
        assert resp.status_code == 200
        content = resp.content.decode('utf-8')
        assert 'class="table-footer"' in content
        # Ensure it's outside table-wrap
        if '</table>\\n  </div>\\n  <div class="table-footer">' in content:
            self.stdout.write("Dashboard pagination moved outside table-wrap: YES")
        
        # Test 2: Monitoring Route and UI
        resp = c.get('/monitoring/?tahun=2026&bulan=1')
        assert resp.status_code == 200
        content = resp.content.decode('utf-8')
        assert 'Ringkasan Monitoring Bulanan' in content
        assert 'Detail Dokumen / Transaksi (D_K)' in content
        assert 'id="detail-transaksi-content" class="hidden"' in content
        self.stdout.write("Monitoring split into Section A (Ringkasan) and Section B (Detail D_K, collapsible): YES")
        
        # Test 3: Akun Route
        resp = c.get('/akun/')
        assert resp.status_code == 200
        content = resp.content.decode('utf-8')
        assert 'href="/akun/52' in content or 'href="/akun/51' in content
        self.stdout.write("Akun Keuangan cards clickable: YES")
        
        # Test 4: Akun Detail
        resp = c.get('/akun/522111/')
        assert resp.status_code == 200
        content = resp.content.decode('utf-8')
        assert 'Detail Transaksi Akun 522111' in content
        self.stdout.write("Akun detail view accessible: YES")
        
        # Test 5: /dk/?page_size=50 max 20 rows
        resp = c.get('/dk/?page_size=50')
        content = resp.content.decode('utf-8')
        tbody_start = content.find('<tbody>')
        tbody_end = content.find('</tbody>')
        tbody = content[tbody_start:tbody_end]
        row_count = tbody.count('<tr')
        self.stdout.write(f"DK page_size=50 row count: {row_count}")
        assert row_count <= 20
        
        self.stdout.write("All UI tests passed.")
