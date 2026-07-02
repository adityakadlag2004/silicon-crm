"""FCM push notifications via firebase-admin.

Activates only when Firebase credentials are configured — otherwise every
call is a silent no-op so the web app works unchanged without Firebase.

Setup (see mobile/README.md):
  1. Create a Firebase project, add an Android app with package
     bo.kadlaginvestment.crm, download google-services.json into
     mobile/android/app/.
  2. Project Settings → Service accounts → Generate new private key.
  3. On the server, set FIREBASE_CREDENTIALS=/path/to/service-account.json
"""
import logging
import os

logger = logging.getLogger(__name__)

_app = None
_unavailable = False


def _get_app():
    """Lazily initialize firebase-admin; returns None when not configured."""
    global _app, _unavailable
    if _app is not None:
        return _app
    if _unavailable:
        return None

    cred_path = os.environ.get("FIREBASE_CREDENTIALS", "").strip()
    if not cred_path or not os.path.exists(cred_path):
        _unavailable = True
        return None
    try:
        import firebase_admin
        from firebase_admin import credentials

        _app = firebase_admin.initialize_app(credentials.Certificate(cred_path))
        return _app
    except ImportError:
        logger.warning("firebase-admin not installed; push notifications disabled.")
        _unavailable = True
        return None
    except Exception:
        logger.exception("Firebase initialization failed; push notifications disabled.")
        _unavailable = True
        return None


def send_push_to_user(user, title, body, link=""):
    """Send a push to every registered device of `user`. Best-effort:
    failures are logged, dead tokens are pruned, nothing raises."""
    if _get_app() is None:
        return 0

    from firebase_admin import messaging
    from ..models import PushDevice

    devices = list(PushDevice.objects.filter(user=user))
    if not devices:
        return 0

    sent = 0
    for device in devices:
        message = messaging.Message(
            token=device.token,
            notification=messaging.Notification(title=title, body=body),
            data={"link": link or ""},
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    icon="ic_launcher", color="#E5B740", click_action="OPEN_LINK"
                ),
            ),
        )
        try:
            messaging.send(message)
            sent += 1
        except messaging.UnregisteredError:
            device.delete()  # token expired / app uninstalled
        except Exception:
            logger.exception("FCM send failed for device %s", device.pk)
    return sent
