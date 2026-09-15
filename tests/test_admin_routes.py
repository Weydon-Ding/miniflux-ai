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
        self.controls = {}
        self.control_name = None
        self.control_tag = None
        self.editable_value = False
        self.selected = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('input', 'textarea', 'select'):
            name = attrs.get('name')
            self.controls[name] = (tag, attrs)
            self.fields[name] = attrs.get('value', '')
            self.editable_value = True
            if tag != 'input':
                self.control_name = name
                self.control_tag = tag
        if tag == 'option':
            self.selected = 'selected' in attrs
            if self.selected:
                self.fields[self.control_name] = attrs.get('value', '')
        if tag == 'code':
            self.in_key = True
            self.key = ''
        elif tag == 'dd':
            self.in_value = True
            self.value = ''
            self.editable_value = False

    def handle_data(self, data):
        if self.control_tag == 'textarea':
            self.fields[self.control_name] += data
        if self.in_key:
            self.key += data
        if self.in_value:
            self.value += data

    def handle_endtag(self, tag):
        if tag == 'textarea':
            # HTML browsers ignore the first LF immediately after <textarea>.
            self.fields[self.control_name] = self.fields[self.control_name].removeprefix('\n')
        if tag in ('textarea', 'select'):
            self.control_name = None
            self.control_tag = None
        if tag == 'code':
            self.in_key = False
        elif tag == 'dd':
            self.in_value = False
            if not self.editable_value:
                self.fields[self.key] = self.value.strip()


class ConfigFormParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.values = {}
        self.name = None
        self.in_textarea = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and 'name' in attrs:
            self.values[attrs['name']] = attrs.get('value', '')
        elif tag in ('textarea', 'select'):
            self.name = attrs.get('name')
            self.in_textarea = tag == 'textarea'
            self.values[self.name] = ''
        elif tag == 'option' and 'selected' in attrs:
            self.values[self.name] = attrs.get('value', '')

    def handle_data(self, data):
        if self.in_textarea:
            self.values[self.name] += data

    def handle_endtag(self, tag):
        if tag == 'textarea':
            # HTML browsers ignore the first LF immediately after <textarea>.
            self.values[self.name] = self.values[self.name].removeprefix('\n')
        if tag in ('textarea', 'select'):
            self.name = None
            self.in_textarea = False


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
                "agents:\n"
                "  summary:\n"
                "    title: 摘要\n"
                "    prompt: 概括正文。\n"
                "  translate:\n"
                "    title: 翻译\n"
                "    prompt: 翻译正文。\n"
                "admin:\n"
                "  enabled: true\n"
                "  username: operator\n"
                "  password: test-admin-password\n"
            )
        )

    def saving_client(self):
        app = self.import_app(self.config_with(dedent('''\
            # Preserve the read-only settings.
            agents:
              summary:
                title: Summary
                prompt: Summarize the article.
              translate:
                title: Translation
                prompt: Translate the article.
            custom:
              enabled: true
            admin:
              enabled: true
              username: operator
              password: test-admin-password
            ''')))
        return app.test_client(), self.basic_auth_header('operator', 'test-admin-password')

    def form_data(self, client, headers):
        response = client.get('/admin/config', headers=headers)
        self.assertEqual(response.status_code, 200)
        parser = ConfigFieldsParser(response.get_data(as_text=True))
        return {key: parser.fields[key] for key in parser.controls}

    def assert_form_values(self, html, expected):
        values = ConfigFormParser(html).values
        self.assertTrue(values.pop('csrf_token', ''))
        self.assertEqual(values, {key: value for key, value in expected.items() if key != 'csrf_token'})

    def test_user_can_save_core_settings_with_backup_and_restart_notice(self):
        from yaml import safe_load

        client, headers = self.saving_client()
        original = Path('config.yml').read_bytes()
        data = self.form_data(client, headers)
        data.update({
            'miniflux.base_url': 'https://new-miniflux.example.test',
            'miniflux.schedule_interval': '15',
            'llm.provider': 'gemini',
            'llm.base_url': 'https://gemini.example.test',
            'llm.model': 'updated-model',
            'llm.max_length': '12000', 'llm.timeout': '90',
            'llm.max_workers': '2', 'llm.RPM': '30',
            'llm.extra_params': 'temperature: 0.5\nthinking_config:\n  thinking_budget: 0',
        })
        response = client.post('/admin/config', headers=headers, data=data)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(Path('config.yml.bak').read_bytes(), original)
        saved = safe_load(Path('config.yml').read_text(encoding='utf-8'))
        self.assertEqual(saved['miniflux'], {
            'base_url': 'https://new-miniflux.example.test', 'schedule_interval': 15,
            'api_key': 'miniflux-test-key', 'webhook_secret': 'webhook-test-secret',
        })
        self.assertEqual(saved['llm'], {
            'provider': 'gemini', 'base_url': 'https://gemini.example.test',
            'api_key': 'llm-test-key', 'model': 'updated-model', 'max_length': 12000,
            'timeout': 90, 'max_workers': 2, 'RPM': 30,
            'extra_params': {'temperature': 0.5, 'thinking_config': {'thinking_budget': 0}},
        })
        for key in ('agents', 'admin', 'custom'):
            self.assertEqual(saved[key], safe_load(original)[key])
        self.assertIn('# Preserve the read-only settings.', Path('config.yml').read_text(encoding='utf-8'))
        self.assertEqual(sys.modules['myapp'].config.llm_model, 'test-model')
        response = client.get(response.headers['Location'], headers=headers)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('配置已保存', html)
        self.assertIn('config.yml.bak', html)
        self.assertIn('需要重启', html)
        self.assertEqual(ConfigFieldsParser(html).fields['llm.model'], 'updated-model')
        client.get('/admin/config', headers=headers)
        self.assertEqual(Path('config.yml.bak').read_bytes(), original)

    def test_invalid_form_keeps_edits_without_writing_or_exposing_secrets(self):
        client, headers = self.saving_client()
        original = Path('config.yml').read_bytes()
        Path('config.yml.bak').write_bytes(b'existing backup')
        for field, value in (
            ('miniflux.base_url', ' '), ('llm.base_url', ''), ('llm.model', ''),
            ('llm.provider', 'unsupported'),
            ('miniflux.schedule_interval', '0'), ('llm.max_length', '-1'),
            ('llm.timeout', '1.5'), ('llm.max_workers', 'abc'), ('llm.RPM', '0'),
            ('llm.extra_params', '[unclosed'), ('llm.extra_params', '- a list'),
        ):
            with self.subTest(field=field, value=value):
                data = self.form_data(client, headers)
                data.update({
                    'llm.model': '<script>edited model</script>',
                    'miniflux.api_key': 'new-miniflux-secret',
                    'miniflux.webhook_secret': 'new-webhook-secret',
                    'llm.api_key': 'new-llm-secret', field: value,
                })
                response = client.post('/admin/config', headers=headers, data=data)
                self.assertEqual(response.status_code, 400)
                html = response.get_data(as_text=True)
                self.assertIn('未保存', html)
                self.assertIn(f'id="{field}-error"', html)
                self.assertNotIn('<script>', html)
                parsed = ConfigFieldsParser(html)
                fields = parsed.fields
                if value == 'abc':
                    self.assertEqual(parsed.controls[field][1]['type'], 'text')
                self.assertEqual(fields[field], value)
                self.assertEqual(fields['llm.model'], data['llm.model'])
                for secret_field in ('miniflux.api_key', 'miniflux.webhook_secret', 'llm.api_key'):
                    self.assertNotIn(data[secret_field], html)
                    self.assertEqual(fields[secret_field], '********')
                self.assertEqual(Path('config.yml').read_bytes(), original)
                self.assertEqual(Path('config.yml.bak').read_bytes(), b'existing backup')

    def test_saving_requires_authentication_and_valid_form_token(self):
        client, headers = self.saving_client()
        original = Path('config.yml').read_bytes()
        for auth in ({}, self.basic_auth_header('operator', 'wrong-password')):
            response = client.post('/admin/config', headers=auth, data={'llm.model': 'changed'})
            self.assertEqual(response.status_code, 401)
        data = self.form_data(client, headers)
        self.assertTrue(data.get('csrf_token'))
        for token in (None, '', 'forged-token'):
            with self.subTest(token=token):
                submitted = dict(data, **{'llm.model': 'changed'})
                if token is None:
                    submitted.pop('csrf_token')
                else:
                    submitted['csrf_token'] = token
                response = client.post('/admin/config', headers=headers, data=submitted)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(Path('config.yml').read_bytes(), original)
                self.assertFalse(Path('config.yml.bak').exists())

    def test_post_rejects_readonly_unknown_and_duplicate_fields(self):
        from werkzeug.datastructures import MultiDict

        client, headers = self.saving_client()
        original = Path('config.yml').read_bytes()
        data = self.form_data(client, headers)
        for key, value in (
            ('agents.summary.prompt', 'unexpected edit'), ('admin.password', 'replacement'),
            ('unknown', 'value'), ('llm.model', 'duplicate'), ('csrf_token', data['csrf_token']),
        ):
            with self.subTest(key=key):
                submitted = MultiDict(data)
                submitted.add(key, value)
                response = client.post('/admin/config', headers=headers, data=submitted)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(Path('config.yml').read_bytes(), original)
                self.assertFalse(Path('config.yml.bak').exists())

    def test_storage_errors_are_safe_and_do_not_overwrite_config(self):
        client, headers = self.saving_client()
        original = Path('config.yml').read_bytes()
        data = self.form_data(client, headers)
        data.update({'llm.model': 'unsaved-model', 'llm.api_key': 'unsaved-secret'})
        Path('config.yml.bak/config.yml').mkdir(parents=True)
        with self.assertNoLogs(client.application.logger, level='ERROR'):
            response = client.post('/admin/config', headers=headers, data=data)
        self.assertEqual(response.status_code, 500)
        html = response.get_data(as_text=True)
        self.assertIn('备份或写入失败', html)
        self.assertNotIn('unsaved-secret', html)
        self.assertEqual(ConfigFieldsParser(html).fields['llm.model'], 'unsaved-model')
        self.assertEqual(Path('config.yml').read_bytes(), original)
        self.assertEqual(list(Path('.').glob('.config.yml.*.tmp')), [])

        Path('config.yml').write_text('miniflux: [sensitive-broken-yaml', encoding='utf-8')
        for method in ('get', 'post'):
            with self.subTest(method=method), self.assertNoLogs(client.application.logger, level='ERROR'):
                response = getattr(client, method)('/admin/config', headers=headers, data=data)
                self.assertEqual(response.status_code, 500)
                html = response.get_data(as_text=True)
                self.assertIn('读取配置失败', html)
                self.assertNotIn('sensitive-broken-yaml', html)
                self.assertNotIn('unsaved-secret', html)

    def test_secret_rotation_and_blank_preservation_never_return_plaintext(self):
        from yaml import safe_load

        client, headers = self.saving_client()
        secrets = {'miniflux.api_key': 'rotated-miniflux',
                   'miniflux.webhook_secret': 'rotated-webhook', 'llm.api_key': 'rotated-llm'}
        data = self.form_data(client, headers)
        data.update(secrets)
        response = client.post('/admin/config', headers=headers, data=data, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        for secret in secrets.values():
            self.assertNotIn(secret, response.get_data(as_text=True))
        for sentinel in ('', '********'):
            with self.subTest(sentinel=sentinel):
                data = self.form_data(client, headers)
                data.update({key: sentinel for key in secrets})
                data['llm.model'] = 'keep-secrets-model'
                response = client.post('/admin/config', headers=headers, data=data)
                self.assertEqual(response.status_code, 303)
                saved = safe_load(Path('config.yml').read_text(encoding='utf-8'))
                for field, secret in secrets.items():
                    section, key = field.split('.')
                    self.assertEqual(saved[section][key], secret)

    def test_missing_required_secrets_block_save(self):
        client, headers = self.saving_client()
        path = Path('config.yml')
        path.write_text(path.read_text(encoding='utf-8').replace('miniflux-test-key', '').replace('llm-test-key', ''), encoding='utf-8')
        original = path.read_bytes()
        data = self.form_data(client, headers)
        response = client.post('/admin/config', headers=headers, data=data)
        self.assertEqual(response.status_code, 400)
        html = response.get_data(as_text=True)
        self.assertIn('id="miniflux.api_key-error"', html)
        self.assertIn('id="llm.api_key-error"', html)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(Path('config.yml.bak').exists())

    def test_expired_form_token_is_rejected_without_writing(self):
        from unittest.mock import patch
        import time

        client, headers = self.saving_client()
        original = Path('config.yml').read_bytes()
        with patch('time.time', return_value=time.time() - 7200):
            data = self.form_data(client, headers)
        response = client.post('/admin/config', headers=headers, data=data)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Path('config.yml').read_bytes(), original)
        self.assertFalse(Path('config.yml.bak').exists())

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

    def test_config_overview_explains_secret_retention_restart_and_sections(self):
        app = self.import_admin_app()

        response = app.test_client().get(
            "/admin/config",
            headers=self.basic_auth_header("operator", "test-admin-password"),
        )

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("密钥保留规则", html)
        for key in ("miniflux.api_key", "miniflux.webhook_secret", "llm.api_key"):
            with self.subTest(key=key):
                self.assertIn(f"<code>{key}</code>", html)
        self.assertIn("留空或保持", html)
        self.assertIn("输入新值才会替换", html)
        self.assertIn("重启前当前进程仍使用启动时加载的配置", html)
        for summary in (
            "连接 Miniflux",
            "配置 provider、模型、速率限制",
            "固定 summary / translate",
            "配置每日生成时间和新闻提示词",
            "配置订阅健康检查 feed",
        ):
            with self.subTest(summary=summary):
                self.assertIn(summary, html)

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
        form = ConfigFormParser(response.get_data(as_text=True)).values
        editable = {key: value for key, value in expected.items() if not key.startswith('agents.')}
        editable.update({'miniflux.api_key': '********', 'miniflux.webhook_secret': '', 'llm.api_key': '********'})
        self.assertEqual({key: value for key, value in form.items() if key != 'csrf_token'}, editable)
        for key, value in expected.items():
            if key not in editable:
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
            "miniflux.webhook_secret": "",
            "miniflux.schedule_interval": "",
            "llm.provider": "openai",
            "llm.max_length": "",
            "llm.timeout": "60",
            "llm.max_workers": "4",
            "llm.RPM": "1000",
            "llm.extra_params": "",
            "agents.summary.title": "未设置",
            "agents.summary.allow_list": "未设置",
            "agents.translate.prompt": "未设置",
            "ai_news.schedule": "",
            "ai_news.prompts.greeting": "",
            "feeds_status.enabled": "false",
            "feeds_status.url": "https://news.example.test",
            "feeds_status.schedule": "09:00",
        }
        fields.update(ConfigFormParser(response.get_data(as_text=True)).values)
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
        fields.update(ConfigFormParser(html).values)
        for key in ("agents.summary.title", "agents.summary.prompt", "agents.summary.allow_list", "ai_news.prompts.greeting"):
            with self.subTest(key=key):
                self.assertEqual(fields[key], payload)

    def test_editable_groups_read_disk_while_agents_keep_startup_values(self):
        app = self.import_admin_app()
        client = app.test_client()
        headers = self.basic_auth_header("operator", "test-admin-password")
        path = Path("config.yml")
        original = path.read_text(encoding="utf-8")

        response = client.get("/admin/config", headers=headers)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('<form method="post"', html)
        controls = ConfigFieldsParser(html).controls
        self.assertEqual(set(controls) - {'csrf_token'}, {
            'miniflux.base_url', 'miniflux.api_key', 'miniflux.webhook_secret',
            'miniflux.schedule_interval', 'llm.provider', 'llm.base_url', 'llm.api_key',
            'llm.model', 'llm.max_length', 'llm.timeout', 'llm.max_workers', 'llm.RPM',
            'llm.extra_params', 'ai_news.url', 'ai_news.schedule',
            'ai_news.prompts.greeting', 'ai_news.prompts.summary', 'ai_news.prompts.summary_block',
            'feeds_status.enabled', 'feeds_status.url', 'feeds_status.schedule',
        })
        self.assertEqual(controls['llm.provider'][0], 'select')
        self.assertEqual(controls['llm.extra_params'][0], 'textarea')
        self.assertEqual(controls['llm.api_key'][1]['type'], 'password')
        self.assertEqual(controls['ai_news.schedule'][0], 'textarea')
        self.assertEqual(controls['feeds_status.enabled'][0], 'select')
        self.assertEqual(html.count('<button type="submit">'), 1)
        self.assertIn('保存 Miniflux、LLM、AI News 和 Feeds Status', html)
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertFalse(Path('config.yml.bak').exists())

        path.write_text(
            original.replace("test-model", "changed-on-disk-model").replace('title: 摘要', 'title: 已修改')
            + 'ai_news:\n  url: https://changed.example.test\n', encoding="utf-8",
        )
        response = client.get("/admin/config", headers=headers)
        self.assertEqual(response.status_code, 200)
        fields = ConfigFieldsParser(response.get_data(as_text=True)).fields
        self.assertEqual(fields["llm.model"], "changed-on-disk-model")
        self.assertEqual(fields['ai_news.url'], 'https://changed.example.test')
        self.assertEqual(fields['agents.summary.title'], '摘要')
        self.assertEqual(sys.modules['myapp'].config.llm_model, 'test-model')
        self.assertFalse(Path('config.yml.bak').exists())

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

    def test_user_can_save_news_and_status_settings_with_backup_and_restart_notice(self):
        from yaml import safe_load

        app = self.import_admin_app()
        client = app.test_client()
        headers = self.basic_auth_header('operator', 'test-admin-password')
        original = Path('config.yml').read_bytes()
        page = client.get('/admin/config', headers=headers)
        form = ConfigFormParser(page.get_data(as_text=True)).values
        self.assertTrue(form.get('csrf_token'))
        edits = {
            'ai_news.url': 'https://news.example.test',
            'ai_news.schedule': '00:00\n 07:30\n\n23:59',
            'ai_news.prompts.greeting': '你好 ${date}\n开始今日新闻。',
            'ai_news.prompts.summary': '保留 ${content} 中的重点。',
            'ai_news.prompts.summary_block': '分类列出 ${content}。',
            'feeds_status.enabled': 'true',
            'feeds_status.url': 'https://status.example.test',
            'feeds_status.schedule': '09:15',
        }
        form.update(edits)

        response = client.post('/admin/config', headers=headers, data=form)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(Path('config.yml.bak').read_bytes(), original)
        saved = safe_load(Path('config.yml').read_text(encoding='utf-8'))
        self.assertEqual(saved['ai_news'], {
            'url': 'https://news.example.test', 'schedule': ['00:00', '07:30', '23:59'],
            'prompts': {
                'greeting': '你好 ${date}\n开始今日新闻。',
                'summary': '保留 ${content} 中的重点。',
                'summary_block': '分类列出 ${content}。',
            },
        })
        self.assertEqual(saved['feeds_status'], {
            'enabled': True, 'url': 'https://status.example.test', 'schedule': '09:15',
        })
        for section in ('miniflux', 'llm', 'agents', 'admin'):
            for key, value in safe_load(original)[section].items():
                self.assertEqual(saved[section][key], value)
        self.assertIsNone(importlib.import_module('myapp').config.ai_news_schedule)
        page = client.get(response.headers['Location'], headers=headers)
        self.assertEqual(page.status_code, 200)
        self.assertIn('配置已保存', page.get_data(as_text=True))
        self.assertIn('需要重启', page.get_data(as_text=True))
        self.assertEqual(page.headers['Cache-Control'], 'no-store')
        reloaded = ConfigFormParser(page.get_data(as_text=True)).values
        self.assertEqual(reloaded['ai_news.schedule'], '00:00\n07:30\n23:59')
        self.assertEqual(Path('config.yml.bak').read_bytes(), original)

    def test_invalid_times_keep_all_edits_without_changing_config_or_backup(self):
        app = self.import_admin_app()
        client = app.test_client()
        headers = self.basic_auth_header('operator', 'test-admin-password')
        page = client.get('/admin/config', headers=headers)
        form = ConfigFormParser(page.get_data(as_text=True)).values
        original = Path('config.yml').read_bytes()
        Path('config.yml.bak').write_bytes(b'previous backup')
        for field in ('ai_news.schedule', 'feeds_status.schedule'):
            for value in ('7:30', '24:00', '12:60', '07:30\ninvalid', '０７:３０'):
                with self.subTest(field=field, value=value):
                    edits = dict(form, **{
                        'ai_news.url': 'https://edited.example.test',
                        'ai_news.prompts.greeting': '\n</textarea><script>alert("test")</script>\n新输入',
                        'ai_news.prompts.summary': '待保存摘要',
                        'ai_news.prompts.summary_block': '待保存分类',
                        field: value,
                    })

                    response = client.post('/admin/config', headers=headers, data=edits)

                    self.assertEqual(response.status_code, 400)
                    html = response.get_data(as_text=True)
                    self.assertIn('HH:MM', html)
                    self.assertIn(f'id="{field}-error"', html)
                    self.assertIn('aria-invalid="true"', html)
                    self.assertNotIn('<script>', html)
                    self.assert_form_values(html, edits)
                    self.assertEqual(Path('config.yml').read_bytes(), original)
                    self.assertEqual(Path('config.yml.bak').read_bytes(), b'previous backup')
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
                    for secret in ('miniflux-test-key', 'llm-test-key', 'test-admin-password'):
                        self.assertNotIn(secret, html)

    def test_save_rejects_duplicate_or_out_of_scope_fields(self):
        from werkzeug.datastructures import MultiDict

        app = self.import_admin_app()
        client = app.test_client()
        headers = self.basic_auth_header('operator', 'test-admin-password')
        page = client.get('/admin/config', headers=headers)
        form = ConfigFormParser(page.get_data(as_text=True)).values
        original = Path('config.yml').read_bytes()
        extra = dict(form, **{'agents.summary.title': 'injected-title'})
        duplicate = MultiDict(form)
        duplicate.add('feeds_status.enabled', 'true')
        for data in (extra, duplicate):
            with self.subTest(data=data):
                response = client.post('/admin/config', headers=headers, data=data)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(Path('config.yml').read_bytes(), original)
                self.assertFalse(Path('config.yml.bak').exists())
                self.assertNotIn('injected-title', response.get_data(as_text=True))

    def test_partial_save_updates_both_feature_groups_and_preserves_omitted_values(self):
        from yaml import safe_load

        app = self.import_admin_app()
        path = Path('config.yml')
        path.write_text(path.read_text(encoding='utf-8') + dedent('''\
            ai_news:
              url: https://news.example.test
              prompts:
                greeting: hello
                summary: summary
                summary_block: block
            feeds_status:
              enabled: true
              url: https://status.example.test
              schedule: '09:00'
            '''), encoding='utf-8')
        client = app.test_client()
        headers = self.basic_auth_header('operator', 'test-admin-password')
        form = self.form_data(client, headers)
        original = path.read_bytes()

        response = client.post('/admin/config', headers=headers, data={
            'csrf_token': form['csrf_token'],
            'miniflux.base_url': 'https://updated-miniflux.example.test',
            'llm.model': 'updated-model',
            'ai_news.schedule': '07:30\n18:00',
            'feeds_status.schedule': '10:00',
        })

        self.assertEqual(response.status_code, 303)
        self.assertEqual(Path('config.yml.bak').read_bytes(), original)
        saved = safe_load(path.read_text(encoding='utf-8'))
        self.assertEqual(saved['miniflux']['base_url'], 'https://updated-miniflux.example.test')
        self.assertEqual(saved['llm']['model'], 'updated-model')
        self.assertEqual(saved['ai_news']['schedule'], ['07:30', '18:00'])
        self.assertEqual(saved['feeds_status']['schedule'], '10:00')
        for section, key in (
            ('miniflux', 'api_key'), ('miniflux', 'webhook_secret'), ('llm', 'api_key'),
            ('ai_news', 'url'), ('ai_news', 'prompts'),
            ('feeds_status', 'enabled'), ('feeds_status', 'url'),
        ):
            self.assertEqual(saved[section][key], safe_load(original)[section][key])
        for section in ('agents', 'admin'):
            self.assertEqual(saved[section], safe_load(original)[section])
        self.assertEqual(sys.modules['myapp'].config.llm_model, 'test-model')
        self.assertIsNone(sys.modules['myapp'].config.ai_news_schedule)

    def test_save_requires_authentication_and_csrf_before_reading_config(self):
        app = self.import_admin_app()
        client = app.test_client()
        headers = self.basic_auth_header('operator', 'test-admin-password')
        page = client.get('/admin/config', headers=headers)
        form = ConfigFormParser(page.get_data(as_text=True)).values
        Path('config.yml').unlink()
        for auth, token, expected in (
            ({}, form['csrf_token'], 401),
            (self.basic_auth_header('operator', 'wrong'), form['csrf_token'], 401),
            (headers, '', 403), (headers, 'invalid-token', 403), (headers, '非 ASCII', 403),
        ):
            with self.subTest(expected=expected, token=token):
                response = client.post('/admin/config', headers=auth, data=dict(form, csrf_token=token))
                self.assertEqual(response.status_code, expected)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertNotIn(form['csrf_token'], response.get_data(as_text=True))
                self.assertFalse(Path('config.yml').exists())
                self.assertFalse(Path('config.yml.bak').exists())

    def test_config_load_errors_do_not_expose_yaml_in_response_or_logs(self):
        app = self.import_admin_app()
        client = app.test_client()
        headers = self.basic_auth_header('operator', 'test-admin-password')
        form = ConfigFormParser(client.get('/admin/config', headers=headers).get_data(as_text=True)).values
        broken = 'secret: [private-configuration-marker\n'
        Path('config.yml').write_text(broken, encoding='utf-8')

        with self.assertNoLogs(app.logger, level='ERROR'):
            for method in ('get', 'post'):
                with self.subTest(method=method):
                    response = getattr(client, method)('/admin/config', headers=headers, data=form)
                    self.assertEqual(response.status_code, 500)
                    html = response.get_data(as_text=True)
                    self.assertIn('读取配置失败', html)
                    self.assertNotIn('private-configuration-marker', html)
                    self.assertNotIn('Traceback', html)
                    self.assertEqual(Path('config.yml').read_text(encoding='utf-8'), broken)
                    self.assertFalse(Path('config.yml.bak').exists())

    def test_backup_failure_keeps_user_input_and_original_config(self):
        app = self.import_admin_app()
        client = app.test_client()
        headers = self.basic_auth_header('operator', 'test-admin-password')
        form = ConfigFormParser(client.get('/admin/config', headers=headers).get_data(as_text=True)).values
        form['ai_news.url'] = 'https://unsaved.example.test'
        original = Path('config.yml').read_bytes()
        Path('config.yml.bak').write_bytes(b'previous backup')
        from unittest.mock import patch

        with patch('shutil.copy2', side_effect=OSError('private-backup-failure')):
            with self.assertNoLogs(app.logger, level='ERROR'):
                response = client.post('/admin/config', headers=headers, data=form)

        self.assertEqual(response.status_code, 500)
        html = response.get_data(as_text=True)
        self.assertIn('备份或写入失败', html)
        self.assertNotIn('private-backup-failure', html)
        self.assert_form_values(html, form)
        self.assertEqual(Path('config.yml').read_bytes(), original)
        self.assertEqual(Path('config.yml.bak').read_bytes(), b'previous backup')
        self.assertFalse(list(Path('.').glob('.config.yml.*.tmp')))

    def test_user_can_disable_status_and_clear_news_schedule(self):
        from yaml import safe_load

        app = self.import_admin_app()
        path = Path('config.yml')
        path.write_text(path.read_text(encoding='utf-8') + dedent('''\
            ai_news:
              schedule: ['07:30']
              prompts:
                greeting: hello
                summary: summary
                summary_block: block
            feeds_status:
              enabled: true
              url: https://status.example.test
              schedule: '09:00'
            '''), encoding='utf-8')
        client = app.test_client()
        headers = self.basic_auth_header('operator', 'test-admin-password')
        form = ConfigFormParser(client.get('/admin/config', headers=headers).get_data(as_text=True)).values
        self.assertEqual(form['feeds_status.enabled'], 'true')
        form.update({'feeds_status.enabled': 'false', 'ai_news.schedule': '',
                     'ai_news.prompts.greeting': '', 'ai_news.prompts.summary': '',
                     'ai_news.prompts.summary_block': ''})

        response = client.post('/admin/config', headers=headers, data=form)

        self.assertEqual(response.status_code, 303)
        saved = safe_load(path.read_text(encoding='utf-8'))
        self.assertIs(saved['feeds_status']['enabled'], False)
        self.assertEqual(saved['ai_news']['schedule'], [])
        self.assertEqual(saved['ai_news']['prompts']['greeting'], '')

    def test_required_prompt_errors_are_shown_next_to_fields(self):
        app = self.import_admin_app()
        client = app.test_client()
        headers = self.basic_auth_header('operator', 'test-admin-password')
        form = ConfigFormParser(client.get('/admin/config', headers=headers).get_data(as_text=True)).values
        form['ai_news.schedule'] = '07:30'
        original = Path('config.yml').read_bytes()

        response = client.post('/admin/config', headers=headers, data=form)

        self.assertEqual(response.status_code, 400)
        html = response.get_data(as_text=True)
        for prompt in ('greeting', 'summary', 'summary_block'):
            self.assertIn(f'id="ai_news.prompts.{prompt}-error"', html)
        self.assertIn('必填', html)
        self.assert_form_values(html, form)
        self.assertEqual(Path('config.yml').read_bytes(), original)
        self.assertFalse(Path('config.yml.bak').exists())

    def test_enabled_status_rejects_cleared_url_instead_of_saving_null(self):
        app = self.import_admin_app()
        path = Path('config.yml')
        path.write_text(path.read_text(encoding='utf-8') + dedent('''\
            ai_news:
              url: https://news.example.test
            feeds_status:
              enabled: true
              schedule: '09:00'
            '''), encoding='utf-8')
        client = app.test_client()
        headers = self.basic_auth_header('operator', 'test-admin-password')
        form = ConfigFormParser(client.get('/admin/config', headers=headers).get_data(as_text=True)).values
        self.assertEqual(form['feeds_status.url'], 'https://news.example.test')
        form['feeds_status.url'] = ''
        original = path.read_bytes()

        response = client.post('/admin/config', headers=headers, data=form)

        self.assertEqual(response.status_code, 400)
        self.assertIn('id="feeds_status.url-error"', response.get_data(as_text=True))
        self.assert_form_values(response.get_data(as_text=True), form)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(Path('config.yml.bak').exists())

    def test_admin_config_route_is_not_registered_for_string_false(self):
        app = self.import_app(self.config_with("admin:\n  enabled: 'false'\n"))

        self.assertNotIn("/admin/config", self.route_paths(app))


if __name__ == "__main__":
    unittest.main()
