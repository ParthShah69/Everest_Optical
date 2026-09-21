from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_bcrypt import Bcrypt
from flask_migrate import Migrate

try:
    from flask_socketio import SocketIO
except ImportError:
    class SocketIO:
        def __init__(self, *args, **kwargs): pass
        def init_app(self, app, **kwargs): pass
        def on(self, event, *args, **kwargs):
            def decorator(f): return f
            return decorator
        def emit(self, *args, **kwargs): pass

db = SQLAlchemy()
login_manager = LoginManager()
bcrypt = Bcrypt()
migrate = Migrate()
socketio = SocketIO()

