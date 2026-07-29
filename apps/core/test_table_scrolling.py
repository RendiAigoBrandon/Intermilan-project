import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse


class TableScrollStyleTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.base_dir = Path(settings.BASE_DIR)
        cls.components_css = (cls.base_dir / "static/css/components.css").read_text(
            encoding="utf-8"
        )

    def test_shared_table_wrapper_keeps_horizontal_scroll_without_vertical_trap(self):
        wrapper_rule = re.search(
            r"\.table-wrap, \.excel-grid-wrap, \.wide-table-wrap, \.table-responsive \{(?P<body>.*?)\}",
            self.components_css,
            re.DOTALL,
        )
        self.assertIsNotNone(wrapper_rule)
        declarations = wrapper_rule.group("body")
        self.assertIn("overflow-x: auto;", declarations)
        self.assertIn("overflow-y: hidden;", declarations)
        self.assertIn("overscroll-behavior-y: auto;", declarations)
        self.assertNotIn("overscroll-behavior: contain;", declarations)

        unconstrained_rule = re.search(
            r"\.table-wrap\.large,\s*\.wide-table-wrap\.large,\s*\.dashboard-table-wrap \{(?P<body>.*?)\}",
            self.components_css,
            re.DOTALL,
        )
        self.assertIsNotNone(unconstrained_rule)
        self.assertIn("height: auto;", unconstrained_rule.group("body"))
        self.assertIn("max-height: none;", unconstrained_rule.group("body"))

    def test_table_templates_do_not_add_inline_vertical_scroll_regions(self):
        for template_path in (self.base_dir / "templates").rglob("*.html"):
            source = template_path.read_text(encoding="utf-8", errors="replace")
            if "<table" not in source:
                continue
            with self.subTest(template=str(template_path.relative_to(self.base_dir))):
                self.assertNotRegex(
                    source,
                    r"style=[\"'][^\"']*overflow-y\s*:\s*(?:auto|scroll)",
                )

    def test_frontend_has_no_wheel_event_hijacking(self):
        source_paths = list((self.base_dir / "static/js").rglob("*.js"))
        source_paths.extend((self.base_dir / "templates").rglob("*.html"))
        for source_path in source_paths:
            source = source_path.read_text(encoding="utf-8", errors="replace")
            with self.subTest(source=str(source_path.relative_to(self.base_dir))):
                self.assertNotRegex(
                    source,
                    r"addEventListener\(\s*[\"'](?:wheel|mousewheel)[\"']",
                )

    def test_preview_and_representative_tables_use_shared_wrappers(self):
        expected_markup = {
            "templates/paket_spm/preview.html": 'class="table-wrap"',
            "templates/dk/list.html": "wide-table-wrap",
            "templates/documents/archive.html": "wide-table-wrap",
            "templates/core/audit_data.html": 'class="table-wrap"',
            "templates/reports/index.html": 'class="table-wrap"',
            "templates/paket_spm/drafts.html": "table-responsive",
        }
        for relative_path, marker in expected_markup.items():
            with self.subTest(template=relative_path):
                source = (self.base_dir / relative_path).read_text(encoding="utf-8")
                self.assertIn(marker, source)


class TablePageRenderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            username="table-scroll-admin",
            password="password",
            email="",
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_table_pages_render_with_shared_wrapper(self):
        destinations = (
            "core:dashboard",
            "core:monitoring",
            "core:audit_data",
            "core:master_akun",
            "dk:transaction_list",
            "documents:archive",
            "documents:checklist",
            "paket_spm:list",
            "sp2d:list",
            "reports:index",
        )
        for destination in destinations:
            with self.subTest(destination=destination):
                response = self.client.get(reverse(destination))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "static/css/components.css", html=False)
                if "<table" in response.content.decode("utf-8"):
                    self.assertRegex(
                        response.content.decode("utf-8"),
                        r'class="[^"]*(?:table-wrap|table-responsive)[^"]*"',
                    )
