from flask import Flask

from common.config import Config

app = Flask(__name__)
config = Config()

from myapp import ai_news, ai_summary, feeds_status

if config.admin_enabled:
    from myapp.admin import register_admin_routes
    from myapp.admin_agents import register_admin_agent_routes

    register_admin_routes(app, config)
    register_admin_agent_routes(app, config)
