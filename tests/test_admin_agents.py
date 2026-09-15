import base64
import importlib
import os
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

from werkzeug.datastructures import MultiDict


class AgentFormParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.fields = {}
        self.names = []
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('input', 'textarea', 'select') and 'name' in attrs:
            name = attrs['name']
            self.names.append(name)
            self.fields[name] = attrs.get('value', '')
            if tag in ('textarea', 'select'):
                self.current = name
        elif tag == 'option' and self.current and 'selected' in attrs:
            self.fields[self.current] = attrs['value']

    def handle_data(self, data):
        if self.current and not self.current.endswith('style_block'):
            self.fields[self.current] += data

    def handle_endtag(self, tag):
        if tag in ('textarea', 'select'):
            self.current = None


class AdminAgentsTestCase(unittest.TestCase):
    def setUp(self):
        self.old_cwd = os.getcwd()
        self.old_password = os.environ.pop('MINIFLUX_AI_ADMIN_PASSWORD', None)
        self.tmpdir = tempfile.TemporaryDirectory()
        os.chdir(self.tmpdir.name)
        self.clear_app_modules()
        self.path = Path('config.yml')
        self.path.write_text(dedent('''\
            # Keep this configuration comment
            miniflux:
              base_url: https://miniflux.example.test
              api_key: miniflux-secret
              webhook_secret: webhook-secret
            llm:
              base_url: https://llm.example.test/v1
              api_key: llm-secret
              model: test-model
            agents:
              summary:
                title: AI 摘要
                prompt: |-
                  概括 ${content}
                  保留重点。
                style_block: true
                allow_list: ['https://first.example/*', 'https://second.example/*']
                deny_list: ['*skip*']
              translate:
                title: AI 翻译
                prompt: 翻译 ${content}
                style_block: false
                allow_list: []
                deny_list: []
              custom_agent:
                title: Keep custom
                prompt: Do not change
                custom_option: keep
            custom_section:
              value: preserve
            admin:
              enabled: true
              username: operator
              password: admin-secret
            '''), encoding='utf-8')
        token = base64.b64encode(b'operator:admin-secret').decode('ascii')
        self.headers = {'Authorization': f'Basic {token}'}
        self.url = '/admin/config/agents'

    def tearDown(self):
        os.chdir(self.old_cwd)
        self.tmpdir.cleanup()
        if self.old_password is not None:
            os.environ['MINIFLUX_AI_ADMIN_PASSWORD'] = self.old_password
        self.clear_app_modules()

    def clear_app_modules(self):
        for name in list(sys.modules):
            if any(name == root or name.startswith(root + '.') for root in ('myapp', 'common', 'core')):
                del sys.modules[name]

    def client(self):
        return importlib.import_module('myapp').app.test_client()

    def test_agent_editor_requires_authentication_for_get_and_post(self):
        client = self.client()
        original = self.path.read_bytes()
        for method in ('get', 'post'):
            with self.subTest(method=method):
                response = getattr(client, method)(self.url)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers['WWW-Authenticate'], 'Basic realm="miniflux-ai admin"')
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(Path('config.yml.bak').exists())

    def test_agent_editor_renders_only_fixed_fields_from_disk(self):
        client = self.client()
        original = self.path.read_text(encoding='utf-8')
        self.path.write_text(original.replace('AI 摘要', '磁盘上的摘要'), encoding='utf-8')
        response = client.get(self.url, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get('Cache-Control'), 'no-store')
        html = response.get_data(as_text=True)
        form = AgentFormParser(html)
        expected = {
            'agents.summary.title': '磁盘上的摘要',
            'agents.summary.prompt': '概括 ${content}\n保留重点。',
            'agents.summary.style_block': 'true',
            'agents.summary.allow_list': 'https://first.example/*\nhttps://second.example/*',
            'agents.summary.deny_list': '*skip*',
            'agents.translate.title': 'AI 翻译',
            'agents.translate.prompt': '翻译 ${content}',
            'agents.translate.style_block': 'false',
            'agents.translate.allow_list': '',
            'agents.translate.deny_list': '',
        }
        self.assertEqual(set(form.names), set(expected) | {'csrf_token'})
        self.assertEqual(len(form.names), 11)
        self.assertTrue(form.fields['csrf_token'])
        for field, value in expected.items():
            with self.subTest(field=field):
                self.assertEqual(form.fields[field], value)
        self.assertIn('一行一个 pattern', html)
        self.assertIn('需要重启', html)
        self.assertIn('method="post"', html)
        for secret in ('miniflux-secret', 'llm-secret', 'webhook-secret', 'admin-secret', 'custom_agent'):
            self.assertNotIn(secret, html)
        self.assertNotIn('<script', html)
        self.assertFalse(Path('config.yml.bak').exists())

    def test_post_rejects_missing_or_invalid_csrf_without_writing(self):
        client = self.client()
        original = self.path.read_bytes()
        for token in (None, '', 'forged-token', '非 ASCII token'):
            with self.subTest(token=token):
                data = {'agents.summary.title': 'Changed'}
                if token is not None:
                    data['csrf_token'] = token
                response = client.post(self.url, headers=self.headers, data=data)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.headers.get('Cache-Control'), 'no-store')
                self.assertEqual(self.path.read_bytes(), original)
                self.assertFalse(Path('config.yml.bak').exists())

    def test_save_updates_both_agents_and_preserves_backup_and_runtime(self):
        client = self.client()
        original = self.path.read_bytes()
        form = AgentFormParser(client.get(self.url, headers=self.headers).get_data(as_text=True)).fields
        form.update({
            'agents.summary.title': '新摘要',
            'agents.summary.prompt': '  第一行 ${content}\n第二行\n',
            'agents.summary.style_block': 'false',
            'agents.summary.allow_list': '  https://news.example/*\r\n\r\n*tech* \n',
            'agents.summary.deny_list': ' *ads*\n*sponsor* ',
            'agents.translate.title': '新翻译',
            'agents.translate.prompt': '翻译为中文\n${content}',
            'agents.translate.style_block': 'true',
            'agents.translate.allow_list': '*english*\n*world*',
            'agents.translate.deny_list': '\n  \n',
        })
        response = client.post(self.url, headers=self.headers, data=form)
        self.assertEqual(response.status_code, 200)
        self.assertIn('已保存', response.get_data(as_text=True))
        self.assertIn('需要重启', response.get_data(as_text=True))
        editor_module = importlib.import_module('common.config_editor')
        editor = editor_module.ConfigEditor(self.path)
        saved = editor.load()
        self.assertEqual(dict(saved['agents']['summary']), {
            'title': '新摘要', 'prompt': '  第一行 ${content}\n第二行\n', 'style_block': False,
            'allow_list': ['https://news.example/*', '*tech*'], 'deny_list': ['*ads*', '*sponsor*'],
        })
        self.assertEqual(dict(saved['agents']['translate']), {
            'title': '新翻译', 'prompt': '翻译为中文\n${content}', 'style_block': True,
            'allow_list': ['*english*', '*world*'], 'deny_list': [],
        })
        self.assertEqual(set(saved['agents']), {'summary', 'translate', 'custom_agent'})
        self.assertEqual(dict(saved['agents']['custom_agent']), {
            'title': 'Keep custom', 'prompt': 'Do not change', 'custom_option': 'keep',
        })
        self.assertEqual(saved['custom_section']['value'], 'preserve')
        self.assertEqual(saved['miniflux']['api_key'], 'miniflux-secret')
        self.assertEqual(saved['miniflux']['webhook_secret'], 'webhook-secret')
        self.assertEqual(saved['llm']['api_key'], 'llm-secret')
        self.assertEqual(saved['admin']['password'], 'admin-secret')
        self.assertIn('# Keep this configuration comment', self.path.read_text(encoding='utf-8'))
        self.assertEqual(Path('config.yml.bak').read_bytes(), original)
        self.assertEqual(importlib.import_module('myapp').config.agents['summary']['title'], 'AI 摘要')
        reloaded = AgentFormParser(client.get(self.url, headers=self.headers).get_data(as_text=True)).fields
        self.assertEqual(reloaded['agents.summary.title'], '新摘要')
        self.assertEqual(reloaded['agents.summary.allow_list'], 'https://news.example/*\n*tech*')

    def test_post_rejects_fields_outside_fixed_agents_and_duplicate_keys(self):
        client = self.client()
        original = self.path.read_bytes()
        form = AgentFormParser(client.get(self.url, headers=self.headers).get_data(as_text=True)).fields
        for field in ('agents.custom_agent.title', 'agents.new_agent.title', 'agents.summary',
                      'agents.translate.delete', 'llm.model', 'admin.password', 'agent_name'):
            with self.subTest(field=field):
                response = client.post(self.url, headers=self.headers, data={**form, field: 'unexpected-secret'})
                self.assertEqual(response.status_code, 400)
                self.assertNotIn('unexpected-secret', response.get_data(as_text=True))
                self.assertEqual(self.path.read_bytes(), original)
                self.assertFalse(Path('config.yml.bak').exists())
        for field in ('agents.summary.title', 'csrf_token'):
            with self.subTest(duplicate=field):
                data = MultiDict(form)
                data.add(field, form[field])
                response = client.post(self.url, headers=self.headers, data=data)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.path.read_bytes(), original)
                self.assertFalse(Path('config.yml.bak').exists())

    def test_validation_errors_preserve_submitted_text_without_writing(self):
        client = self.client()
        original = self.path.read_bytes()
        form = AgentFormParser(client.get(self.url, headers=self.headers).get_data(as_text=True)).fields
        for field, invalid in (
            ('agents.summary.title', '  '), ('agents.summary.prompt', ''),
            ('agents.translate.title', ''), ('agents.translate.prompt', '\n'),
            ('agents.summary.style_block', 'not-a-bool'), ('agents.translate.style_block', 'invalid'),
        ):
            with self.subTest(field=field):
                data = {**form, 'agents.summary.allow_list': ' *keep*\n\n  *spaces* ', field: invalid}
                response = client.post(self.url, headers=self.headers, data=data)
                self.assertEqual(response.status_code, 400)
                html = response.get_data(as_text=True)
                self.assertIn('保存失败', html)
                self.assertIn(f'{field}-error', html)
                self.assertEqual(AgentFormParser(html).fields, data)
                self.assertEqual(self.path.read_bytes(), original)
                self.assertFalse(Path('config.yml.bak').exists())

    def test_storage_failures_keep_submitted_values_and_hide_exception_details(self):
        client = self.client()
        original = self.path.read_bytes()
        form = AgentFormParser(client.get(self.url, headers=self.headers).get_data(as_text=True)).fields
        form['agents.summary.prompt'] = '未保存的内容\n  保留空白 '
        for boundary in ('shutil.copy2', 'os.replace'):
            with self.subTest(boundary=boundary):
                with patch(boundary, side_effect=OSError('sensitive-filesystem-detail')):
                    response = client.post(self.url, headers=self.headers, data=form)
                self.assertEqual(response.status_code, 500)
                html = response.get_data(as_text=True)
                self.assertIn('保存失败', html)
                self.assertEqual(AgentFormParser(html).fields, form)
                self.assertNotIn('sensitive-filesystem-detail', html)
                self.assertNotIn('admin-secret', html)
                self.assertEqual(self.path.read_bytes(), original)

    def test_unreadable_config_returns_safe_error_and_preserves_posted_values(self):
        client = self.client()
        form = AgentFormParser(client.get(self.url, headers=self.headers).get_data(as_text=True)).fields
        self.path.write_text('invalid: [yaml-secret', encoding='utf-8')
        for method in ('get', 'post'):
            with self.subTest(method=method):
                kwargs = {'data': form} if method == 'post' else {}
                response = getattr(client, method)(self.url, headers=self.headers, **kwargs)
                self.assertEqual(response.status_code, 500)
                html = response.get_data(as_text=True)
                self.assertIn('无法读取配置', html)
                self.assertNotIn('yaml-secret', html)
                self.assertEqual(response.headers.get('Cache-Control'), 'no-store')
                if method == 'post':
                    self.assertEqual(AgentFormParser(html).fields, form)
                self.assertEqual(self.path.read_text(encoding='utf-8'), 'invalid: [yaml-secret')
                self.assertFalse(Path('config.yml.bak').exists())

    def test_overview_links_to_agent_editor_and_remains_read_only(self):
        client = self.client()
        response = client.get('/admin/config', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('href="/admin/config/agents"', html)
        self.assertNotIn('<form', html)
        self.assertEqual(client.post('/admin/config', headers=self.headers).status_code, 405)

    def test_agent_routes_are_disabled_unless_explicitly_enabled(self):
        original = self.path.read_text(encoding='utf-8')
        for enabled in ('false', "'false'"):
            with self.subTest(enabled=enabled):
                self.clear_app_modules()
                self.path.write_text(original.replace('enabled: true', f'enabled: {enabled}'), encoding='utf-8')
                client = self.client()
                for method in ('get', 'post'):
                    response = getattr(client, method)(self.url, headers=self.headers)
                    self.assertEqual(response.status_code, 404)

    def test_wrong_credentials_cannot_read_or_write_agent_configuration(self):
        client = self.client()
        original = self.path.read_bytes()
        form = AgentFormParser(client.get(self.url, headers=self.headers).get_data(as_text=True)).fields
        for credentials in (b'operator:wrong', b'wrong:admin-secret'):
            token = base64.b64encode(credentials).decode('ascii')
            for method in ('get', 'post'):
                with self.subTest(credentials=credentials, method=method):
                    response = getattr(client, method)(
                        self.url, headers={'Authorization': f'Basic {token}'}, data=form,
                    )
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(self.path.read_bytes(), original)
                    self.assertFalse(Path('config.yml.bak').exists())

    def test_editor_escapes_html_in_saved_and_invalid_submitted_values(self):
        client = self.client()
        payload = '</textarea><script>alert("x")</script><input name="injected">'
        editor = importlib.import_module('common.config_editor').ConfigEditor(self.path)
        editor.save({'agents.summary.prompt': payload, 'agents.translate.title': payload})
        response = client.get(self.url, headers=self.headers)
        html = response.get_data(as_text=True)
        self.assertNotIn('<script>', html)
        self.assertNotIn('name="injected"', html)
        form = AgentFormParser(html).fields
        self.assertEqual(form['agents.summary.prompt'], payload)
        self.assertEqual(form['agents.translate.title'], payload)
        form.update({'agents.summary.title': '', 'agents.translate.deny_list': payload})
        response = client.post(self.url, headers=self.headers, data=form)
        self.assertEqual(response.status_code, 400)
        html = response.get_data(as_text=True)
        self.assertNotIn('<script>', html)
        self.assertNotIn('name="injected"', html)
        self.assertEqual(AgentFormParser(html).fields, form)

    def test_partial_agent_update_preserves_omitted_fields(self):
        client = self.client()
        form = AgentFormParser(client.get(self.url, headers=self.headers).get_data(as_text=True)).fields
        response = client.post(self.url, headers=self.headers, data={
            'csrf_token': form['csrf_token'], 'agents.summary.title': '仅更新标题',
        })
        self.assertEqual(response.status_code, 200)
        expected = {**form, 'agents.summary.title': '仅更新标题'}
        self.assertEqual(AgentFormParser(response.get_data(as_text=True)).fields, expected)


if __name__ == '__main__':
    unittest.main()
