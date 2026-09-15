from hmac import compare_digest

from flask import Response, render_template, request
from yaml import safe_dump


_ADMIN_REALM = 'miniflux-ai admin'
AGENT_FIELDS = (
    ('title', '标题'),
    ('prompt', '提示词'),
    ('style_block', '引用块样式'),
    ('allow_list', '允许列表'),
    ('deny_list', '排除列表'),
)


def _unauthorized_response():
    return Response(
        'Authentication required',
        401,
        {'WWW-Authenticate': f'Basic realm="{_ADMIN_REALM}"'},
        mimetype='text/plain',
    )


def _is_authorized(config):
    auth = request.authorization
    if not auth or not config.admin_password:
        return False

    username = auth.username or ''
    password = auth.password or ''
    expected_username = config.admin_username or ''

    return compare_digest(username, expected_username) and compare_digest(password, config.admin_password)


def _masked_secret(value):
    return '********（已设置）' if value else '未设置'


def _display_value(value):
    if value is None or value == '':
        return '未设置'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, list):
        return '\n'.join(str(item) for item in value) if value else '[]'
    if isinstance(value, dict):
        return safe_dump(value, allow_unicode=True, sort_keys=False).strip()
    return str(value)


def _config_sections(config):
    agents = config.agents or {}
    agent_fields = []
    for name in ('summary', 'translate'):
        agent = agents.get(name) or {}
        for key, label in AGENT_FIELDS:
            agent_fields.append((f'{name} · {label}', f'agents.{name}.{key}', agent.get(key)))

    prompts = config.ai_news_prompts or {}
    sections = [
        {'id': 'miniflux', 'title': 'Miniflux', 'fields': [
            ('服务地址', 'miniflux.base_url', config.miniflux_base_url),
            ('API key', 'miniflux.api_key', _masked_secret(config.miniflux_api_key)),
            ('Webhook secret', 'miniflux.webhook_secret', _masked_secret(config.miniflux_webhook_secret)),
            ('轮询间隔（分钟）', 'miniflux.schedule_interval', config.miniflux_schedule_interval or '自动（未指定间隔）'),
        ]},
        {'id': 'llm', 'title': 'LLM', 'fields': [
            ('服务商', 'llm.provider', config.llm_provider),
            ('服务地址', 'llm.base_url', config.llm_base_url),
            ('API key', 'llm.api_key', _masked_secret(config.llm_api_key)),
            ('模型', 'llm.model', config.llm_model),
            ('内容长度上限', 'llm.max_length', config.llm_max_length),
            ('超时（秒）', 'llm.timeout', config.llm_timeout),
            ('并发数', 'llm.max_workers', config.llm_max_workers),
            ('每分钟请求上限', 'llm.RPM', config.llm_RPM),
            ('额外请求参数（YAML）', 'llm.extra_params', config.llm_extra_params),
        ]},
        {'id': 'agents', 'title': 'Agents', 'fields': agent_fields},
        {'id': 'ai-news', 'title': 'AI News', 'fields': [
            ('服务地址', 'ai_news.url', config.ai_news_url),
            ('生成时间', 'ai_news.schedule', config.ai_news_schedule),
            ('问候提示词', 'ai_news.prompts.greeting', prompts.get('greeting')),
            ('摘要提示词', 'ai_news.prompts.summary', prompts.get('summary')),
            ('分类提示词', 'ai_news.prompts.summary_block', prompts.get('summary_block')),
        ]},
        {'id': 'feeds-status', 'title': 'Feeds Status', 'fields': [
            ('是否启用', 'feeds_status.enabled', config.feeds_status_enabled),
            ('服务地址', 'feeds_status.url', config.feeds_status_url),
            ('检查时间', 'feeds_status.schedule', config.feeds_status_schedule),
        ]},
    ]
    for section in sections:
        section['fields'] = [(label, key, _display_value(value)) for label, key, value in section['fields']]
    return sections


def register_admin_routes(app, config):
    @app.route('/admin/config', methods=['GET'])
    def admin_config():
        if not _is_authorized(config):
            return _unauthorized_response()

        return render_template('admin/config.html', sections=_config_sections(config))
