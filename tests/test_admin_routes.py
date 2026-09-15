import base64
import importlib
import os
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from textwrap import dedent


class ConfigFieldsParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.fields = {}
        self.key = ''
        self.value = ''
        self.in_key = False
        self.in_value = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == 'code':
            self.in_key = True
            self.key = ''
        elif tag == 'dd':
            self.in_value = True
            self.value = ''

    def handle_data(self, data):
        if self.in_key:
            self.key += data
        if self.in_value:
            self.value += data

    def handle_endtag(self, tag):
        if tag == 'code':
            self.in_key = False
        elif tag == 'dd':
            self.in_value = False
            self.fields[self.key] = self.value.strip()


class AdminRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.old_cwd = os.getcwd()
        self.old_admin_password = os.environ.pop("MINIFLUX_AI_ADMIN_PASSWORD", None)
        self.tmpdir = tempfile.TemporaryDirectory()
        os.chdir(self.tmpdir.name)
        self._clear_app_modules()

    def tearDown(self):
        os.chdir(self.old_cwd)
        self.tmpdir.cleanup()
        if self.old_admin_password is not None:
            os.environ["MINIFLUX_AI_ADMIN_PASSWORD"] = self.old_admin_password
        self._clear_app_modules()

    def _clear_app_modules(self):
        for module_name in list(sys.modules):
            if (
                module_name == "myapp"
                or module_name.startswith("myapp.")
                or module_name == "common"
                or module_name.startswith("common.")
                or module_name == "core"
                or module_name.startswith("core.")
            ):
                del sys.modules[module_name]

    def import_app(self, config_content):
        Path("config.yml").write_text(config_content, encoding="utf-8")
        myapp = importlib.import_module("myapp")
        return myapp.app

    def config_with(self, content):
        return (
            "miniflux:\n"
            "  base_url: https://miniflux.example.test\n"
            "  api_key: miniflux-test-key\n"
            "  webhook_secret: webhook-test-secret\n"
            "llm:\n"
            "  base_url: https://llm.example.test/v1\n"
            "  api_key: llm-test-key\n"
            "  model: test-model\n"
            f"{content}"
        )

    def route_paths(self, app):
        return {rule.rule for rule in app.url_map.iter_rules()}

    def basic_auth_header(self, username, password):
        token = base64.b64encode(
            f"{username}:{password}".encode("utf-8")
        ).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    def import_admin_app(self):
        return self.import_app(
            self.config_with(
                "admin:\n"
                "  enabled: true\n"
                "  username: operator\n"
                "  password: test-admin-password\n"
            )
        )

    def test_admin_config_route_is_not_registered_by_default(self):
        app = self.import_app(self.config_with(""))

        self.assertNotIn("/admin/config", self.route_paths(app))
        response = app.test_client().get("/admin/config")
        self.assertEqual(response.status_code, 404)

    def test_admin_config_route_requires_basic_auth_when_admin_is_enabled(self):
        app = self.import_admin_app()

        self.assertIn("/admin/config", self.route_paths(app))
        response = app.test_client().get("/admin/config")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["WWW-Authenticate"], 'Basic realm="miniflux-ai admin"')

    def test_admin_config_route_rejects_wrong_basic_auth_credentials(self):
        app = self.import_admin_app()

        response = app.test_client().get(
            "/admin/config",
            headers=self.basic_auth_header("operator", "wrong-password"),
        )
        self.assertEqual(response.status_code, 401)

    def test_authenticated_user_can_view_grouped_config_overview(self):
        app = self.import_admin_app()

        response = app.test_client().get(
            "/admin/config",
            headers=self.basic_auth_header("operator", "test-admin-password"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")
        html = response.get_data(as_text=True)
        for heading in ("Miniflux", "LLM", "Agents", "AI News", "Feeds Status"):
            with self.subTest(heading=heading):
                self.assertIn(f">{heading}</h2>", html)
        self.assertIn("https://miniflux.example.test", html)
        self.assertIn("https://llm.example.test/v1", html)
        self.assertIn("test-model", html)
        self.assertIn("只读", html)
        self.assertIn("启动时加载", html)
        self.assertIn("保存配置后，需要重启", html)
        self.assertIn("才会完全生效", html)

    def test_config_overview_masks_secrets_without_sending_plaintext(self):
        app = self.import_admin_app()

        response = app.test_client().get(
            "/admin/config",
            headers=self.basic_auth_header("operator", "test-admin-password"),
        )
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for key in ("miniflux.api_key", "miniflux.webhook_secret", "llm.api_key"):
            with self.subTest(key=key):
                self.assertIn(key, html)
        self.assertEqual(html.count("********（已设置）"), 3)
        for secret in ("miniflux-test-key", "webhook-test-secret", "llm-test-key", "test-admin-password"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, html)

    def test_config_overview_displays_core_values_and_multiline_content(self):
        app = self.import_app(dedent("""\
            miniflux:
              base_url: https://miniflux.example.test
              api_key: miniflux-test-key
              schedule_interval: 12
            llm:
              provider: gemini
              base_url: https://llm.example.test/v1
              api_key: llm-test-key
              model: overview-model
              max_length: 18000
              timeout: 75
              max_workers: 3
              RPM: 45
              extra_params:
                temperature: 0
                thinking_config:
                  thinking_budget: 0
            agents:
              summary:
                title: AI 摘要
                prompt: |-
                  概括 ${content}
                  保留重点。
                style_block: true
                allow_list:
                  - https://first.example.test
                  - https://second.example.test
                deny_list:
                  - https://skip.example.test
              translate:
                title: AI 翻译
                prompt: 翻译正文。
                style_block: false
                allow_list: []
                deny_list: []
            ai_news:
              url: https://news.example.test
              schedule: ['07:30', '18:00']
              prompts:
                greeting: 早上好。
                summary: 今日要点。
                summary_block: 分类新闻。
            feeds_status:
              enabled: true
              url: https://status.example.test
              schedule: '08:15'
            admin:
              enabled: true
              username: operator
              password: test-admin-password
            """))

        response = app.test_client().get(
            "/admin/config",
            headers=self.basic_auth_header("operator", "test-admin-password"),
        )
        self.assertEqual(response.status_code, 200)
        fields = ConfigFieldsParser(response.get_data(as_text=True)).fields
        expected = {
            "miniflux.base_url": "https://miniflux.example.test",
            "miniflux.schedule_interval": "12",
            "llm.provider": "gemini",
            "llm.base_url": "https://llm.example.test/v1",
            "llm.model": "overview-model",
            "llm.max_length": "18000",
            "llm.timeout": "75",
            "llm.max_workers": "3",
            "llm.RPM": "45",
            "llm.extra_params": "temperature: 0\nthinking_config:\n  thinking_budget: 0",
            "agents.summary.title": "AI 摘要",
            "agents.summary.prompt": "概括 ${content}\n保留重点。",
            "agents.summary.style_block": "true",
            "agents.summary.allow_list": "https://first.example.test\nhttps://second.example.test",
            "agents.summary.deny_list": "https://skip.example.test",
            "agents.translate.title": "AI 翻译",
            "agents.translate.prompt": "翻译正文。",
            "agents.translate.style_block": "false",
            "agents.translate.allow_list": "[]",
            "agents.translate.deny_list": "[]",
            "ai_news.url": "https://news.example.test",
            "ai_news.schedule": "07:30\n18:00",
            "ai_news.prompts.greeting": "早上好。",
            "ai_news.prompts.summary": "今日要点。",
            "ai_news.prompts.summary_block": "分类新闻。",
            "feeds_status.enabled": "true",
            "feeds_status.url": "https://status.example.test",
            "feeds_status.schedule": "08:15",
        }
        for key, value in expected.items():
            with self.subTest(key=key):
                self.assertEqual(fields.get(key), value)

    def test_config_overview_distinguishes_unset_values_from_defaults(self):
        app = self.import_app(self.config_with(dedent("""\
            agents:
              summary:
              translate:
            ai_news:
              url: https://news.example.test
              prompts:
            admin:
              enabled: true
              username: operator
              password: test-admin-password
            """)).replace("  webhook_secret: webhook-test-secret\n", ""))

        response = app.test_client().get(
            "/admin/config",
            headers=self.basic_auth_header("operator", "test-admin-password"),
        )
        self.assertEqual(response.status_code, 200)
        fields = ConfigFieldsParser(response.get_data(as_text=True)).fields
        expected = {
            "miniflux.webhook_secret": "未设置",
            "miniflux.schedule_interval": "自动（未指定间隔）",
            "llm.provider": "openai",
            "llm.max_length": "未设置",
            "llm.timeout": "60",
            "llm.max_workers": "4",
            "llm.RPM": "1000",
            "llm.extra_params": "{}",
            "agents.summary.title": "未设置",
            "agents.summary.allow_list": "未设置",
            "agents.translate.prompt": "未设置",
            "ai_news.schedule": "未设置",
            "ai_news.prompts.greeting": "未设置",
            "feeds_status.enabled": "false",
            "feeds_status.url": "https://news.example.test",
            "feeds_status.schedule": "09:00",
        }
        for key, value in expected.items():
            with self.subTest(key=key):
                self.assertEqual(fields.get(key), value)

    def test_config_overview_escapes_configured_text(self):
        payload = '<script>alert("test")</script>'
        app = self.import_app(self.config_with(dedent(f"""\
            agents:
              summary:
                title: '{payload}'
                prompt: '{payload}'
                allow_list: ['{payload}']
            ai_news:
              prompts:
                greeting: '{payload}'
            admin:
              enabled: true
              username: operator
              password: test-admin-password
            """)))

        response = app.test_client().get(
            "/admin/config",
            headers=self.basic_auth_header("operator", "test-admin-password"),
        )
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn(payload, html)
        self.assertIn("&lt;script&gt;", html)
        fields = ConfigFieldsParser(html).fields
        for key in ("agents.summary.title", "agents.summary.prompt", "agents.summary.allow_list", "ai_news.prompts.greeting"):
            with self.subTest(key=key):
                self.assertEqual(fields[key], payload)

    def test_config_overview_is_read_only_and_does_not_reload_config(self):
        app = self.import_admin_app()
        client = app.test_client()
        headers = self.basic_auth_header("operator", "test-admin-password")
        path = Path("config.yml")
        original = path.read_text(encoding="utf-8")

        response = client.get("/admin/config", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<form", response.get_data(as_text=True))
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        response = client.post("/admin/config", headers=headers, data={"llm.model": "changed-model"})
        self.assertEqual(response.status_code, 405)
        self.assertEqual(path.read_text(encoding="utf-8"), original)

        path.write_text(original.replace("test-model", "changed-on-disk-model"), encoding="utf-8")
        response = client.get("/admin/config", headers=headers)
        self.assertEqual(response.status_code, 200)
        fields = ConfigFieldsParser(response.get_data(as_text=True)).fields
        self.assertEqual(fields["llm.model"], "test-model")

    def test_config_overview_uses_locally_served_styles_without_scripts(self):
        app = self.import_admin_app()
        client = app.test_client()

        response = client.get(
            "/admin/config",
            headers=self.basic_auth_header("operator", "test-admin-password"),
        )
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('href="/static/admin.css"', html)
        self.assertNotIn("<script", html)
        response = client.get("/static/admin.css")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/css")
        response.close()

    def test_admin_config_route_is_not_registered_for_string_false(self):
        app = self.import_app(self.config_with("admin:\n  enabled: 'false'\n"))

        self.assertNotIn("/admin/config", self.route_paths(app))


if __name__ == "__main__":
    unittest.main()
