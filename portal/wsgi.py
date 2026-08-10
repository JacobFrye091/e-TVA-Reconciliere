"""WSGI entrypoint for a production server (gunicorn).

Mirrors run.py's `python -m portal.run` invocation - same data_dir(),
same create_app(..., enable_backup_scheduler=True,
enable_trial_reminder_scheduler=True, enable_risk_alerts_scheduler=True,
enable_plan_schimbari_scheduler=True) - but exposes a module-level `app`
object instead of calling Flask's own dev server, since gunicorn needs an
importable WSGI callable rather than a `.run()` call.

MUST be run as exactly one worker process. create_app() starts daemon
threads (see portal/backup.py, portal/trial_reminders.py,
portal/risk_alerts.py and portal/plan_schimbari.py) that share one
threading.RLock and one
long-lived sqlite3/SQLCipher connection per database file. A second
worker process would open its own, unsynchronized connections and its
own lock to the same files - at best duplicate backups/reminder emails,
at worst interleaved writes and corruption. Concurrency inside this one
process (e.g. `--threads N`) is fine: create_app()'s before_request /
teardown_request hooks already serialize every request around that same
lock, exactly as they do today under the single-threaded dev server.
"""
from werkzeug.middleware.proxy_fix import ProxyFix

from portal.app import create_app
from portal.run import data_dir

app = create_app(data_dir(), enable_backup_scheduler=True,
                 enable_trial_reminder_scheduler=True,
                 enable_risk_alerts_scheduler=True,
                 enable_plan_schimbari_scheduler=True)

# Apache sits in front of gunicorn and talks to it over plain HTTP on
# 127.0.0.1, even when the public-facing connection is HTTPS - without this,
# url_for(..., _external=True) (email verification links) would always
# render as http://. Trusts X-Forwarded-Proto, which only the productie
# HTTPS vhost currently sets; harmless no-op everywhere else.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)
