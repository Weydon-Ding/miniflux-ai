from hmac import compare_digest
from pathlib import Path
from secrets import token_urlsafe

from itsdangerous import BadData, URLSafeTimedSerializer
from flask import Response, redirect, render_template, request, url_for
from yaml import safe_dump

from common.config_editor import (
    ConfigEditor, ConfigEditorError, ConfigLoadError, ConfigValidationError, FIELD_ORDER, INT_FIELDS,
    LLM_PROVIDERS, REQUIRED_STRING_FIELDS, SECRET_FIELDS,
)


_ADMIN_REALM = 'miniflux-ai admin'
AGENT_FIELDS = (
    ('title', '标题'),
    ('prompt', '提示词'),
    ('style_block', '引用块样式'),
    ('allow_list', '允许列表'),
    ('deny_list', '排除列表'),
)
_EDITABLE_FIELDS = {
    field for field in FIELD_ORDER
    if field.startswith(('miniflux.', 'llm.', 'ai_news.', 'feeds_status.'))
}


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
        {
            'id': 'miniflux', 'title': 'Miniflux',
            'summary': '连接 Miniflux，配置 API key、webhook secret 和未读条目轮询间隔。',
            'fields': [
                ('服务地址', 'miniflux.base_url', config.miniflux_base_url),
                ('API key', 'miniflux.api_key', _masked_secret(config.miniflux_api_key)),
                ('Webhook secret', 'miniflux.webhook_secret', _masked_secret(config.miniflux_webhook_secret)),
                ('轮询间隔（分钟）', 'miniflux.schedule_interval', config.miniflux_schedule_interval or '自动（未指定间隔）'),
            ],
        },
        {
            'id': 'llm', 'title': 'LLM',
            'summary': '配置 provider、模型、速率限制、超时和 provider 透传参数。',
            'fields': [
                ('服务商', 'llm.provider', config.llm_provider),
                ('服务地址', 'llm.base_url', config.llm_base_url),
                ('API key', 'llm.api_key', _masked_secret(config.llm_api_key)),
                ('模型', 'llm.model', config.llm_model),
                ('内容长度上限', 'llm.max_length', config.llm_max_length),
                ('超时（秒）', 'llm.timeout', config.llm_timeout),
                ('并发数', 'llm.max_workers', config.llm_max_workers),
                ('每分钟请求上限', 'llm.RPM', config.llm_RPM),
                ('额外请求参数（YAML）', 'llm.extra_params', config.llm_extra_params),
            ],
        },
        {
            'id': 'agents', 'title': 'Agents',
            'summary': '固定 summary / translate 两个 Agent；本页只读，使用独立页面编辑。',
            'fields': agent_fields,
        },
        {
            'id': 'ai-news', 'title': 'AI News',
            'summary': '配置每日生成时间和新闻提示词，用于生成 AI News RSS 内容。',
            'fields': [
                ('服务地址', 'ai_news.url', config.ai_news_url),
                ('生成时间', 'ai_news.schedule', config.ai_news_schedule),
                ('问候提示词', 'ai_news.prompts.greeting', prompts.get('greeting')),
                ('摘要提示词', 'ai_news.prompts.summary', prompts.get('summary')),
                ('分类提示词', 'ai_news.prompts.summary_block', prompts.get('summary_block')),
            ],
        },
        {
            'id': 'feeds-status', 'title': 'Feeds Status',
            'summary': '配置订阅健康检查 feed 的启用状态、地址和每日检查时间。',
            'fields': [
                ('是否启用', 'feeds_status.enabled', config.feeds_status_enabled),
                ('服务地址', 'feeds_status.url', config.feeds_status_url),
                ('检查时间', 'feeds_status.schedule', config.feeds_status_schedule),
            ],
        },
    ]
    for section in sections:
        section['fields'] = [(label, key, _display_value(value)) for label, key, value in section['fields']]
    return sections


def _validation_message(error):
    if error.code == 'invalid_time':
        return ('请一行填写一个 HH:MM 时间（00:00–23:59）。' if error.field == 'ai_news.schedule'
                else '请填写单个 HH:MM 时间（00:00–23:59）。')
    if error.code == 'required':
        return '此配置项必填；请补全后重试，只读项请在 config.yml 中修正。'
    if error.field in INT_FIELDS:
        return f'请输入不小于 {INT_FIELDS[error.field]} 的整数。'
    if error.code == 'choice':
        return '请选择 openai 或 gemini。'
    if error.code in ('invalid_yaml', 'invalid_mapping'):
        return '请输入有效的 YAML mapping，不能使用列表或标量。'
    return '配置值无效，请检查格式后重试。'


def register_admin_routes(app, config):
    config_path = Path('config.yml').resolve()

    @app.after_request
    def protect_admin_response(response):
        if request.endpoint == 'admin_config':
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Frame-Options'] = 'DENY'
        return response

    @app.route('/admin/config', methods=['GET', 'POST'])
    def admin_config():
        if not _is_authorized(config):
            return _unauthorized_response()

        signer = URLSafeTimedSerializer(config.admin_password, salt='admin-config-csrf')
        if request.method == 'POST':
            if any(key not in _EDITABLE_FIELDS | {'csrf_token'} or len(request.form.getlist(key)) != 1
                   for key in request.form):
                return Response('表单包含不支持或重复的字段，配置未保存。', 400, mimetype='text/plain')
            try:
                token = signer.loads(request.form.get('csrf_token', ''), max_age=3600)
                if not isinstance(token, dict) or token.get('username') != config.admin_username:
                    raise BadData('Invalid token')
            except BadData:
                return Response('表单已过期或无效，请刷新页面后重试。', 403, mimetype='text/plain')

        editor = ConfigEditor(config_path)
        try:
            form = editor.render_form()
        except Exception:
            return Response('读取配置失败，请检查 config.yml 的内容与读取权限。', 500, mimetype='text/plain')
        errors = {}
        save_error = None
        status = 200
        if request.method == 'POST':
            submitted = {key: value for key, value in request.form.items() if key != 'csrf_token'}
            try:
                editor.save(submitted)
            except ConfigValidationError as exc:
                errors = {error.field: _validation_message(error) for error in exc.errors}
                status = 400
            except ConfigLoadError:
                status = 500
                save_error = '读取配置失败，配置未保存。请检查 config.yml。'
            except ConfigEditorError:
                status = 500
                save_error = '备份或写入失败，配置未保存。请检查配置目录的写入权限和文件挂载方式。'
            else:
                return redirect(url_for('admin_config', saved='1'), code=303)
            form.update({key: value for key, value in submitted.items()
                         if key in _EDITABLE_FIELDS and key not in SECRET_FIELDS})

        return render_template(
            'admin/config.html', sections=_config_sections(config),
            saved=request.method == 'GET' and request.args.get('saved') == '1', errors=errors,
            form=form, editable_fields=_EDITABLE_FIELDS, save_error=save_error,
            secret_fields=SECRET_FIELDS, int_fields=INT_FIELDS,
            required_fields=REQUIRED_STRING_FIELDS, providers=LLM_PROVIDERS,
            csrf_token=signer.dumps({'username': config.admin_username, 'nonce': token_urlsafe(32)}),
        ), status
