from flask import Flask

from common.config import Config

app = Flask(__name__)
config = Config()

from myapp import ai_news, ai_summary, feeds_status

if config.admin_enabled:
    from myapp.admin import register_admin_routes

    register_admin_routes(app)
