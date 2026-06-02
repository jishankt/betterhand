import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def send_push_notification(fcm_token, title, body, data=None):
    if not fcm_token:
        logger.warning('No FCM token provided — skipping push.')
        return False
    try:
        import firebase_admin
        from firebase_admin import credentials, messaging
        if not firebase_admin._apps:
            cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)
            firebase_admin.initialize_app(cred)
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={str(k): str(v) for k, v in (data or {}).items()},
            token=fcm_token,
            android=messaging.AndroidConfig(priority='high'),
        )
        response = messaging.send(message)
        logger.info(f'FCM sent: {response}')
        return True
    except Exception as exc:
        logger.error(f'FCM error: {exc}')
        return False


def send_push_to_many(fcm_tokens, title, body, data=None):
    """Send to multiple tokens."""
    tokens = [t for t in fcm_tokens if t]
    if not tokens:
        return
    try:
        import firebase_admin
        from firebase_admin import credentials, messaging
        if not firebase_admin._apps:
            cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)
            firebase_admin.initialize_app(cred)
        messages = [
            messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                data={str(k): str(v) for k, v in (data or {}).items()},
                token=token,
                android=messaging.AndroidConfig(priority='high'),
            )
            for token in tokens
        ]
        response = messaging.send_each(messages)
        logger.info(f'FCM batch sent to {len(tokens)} tokens.')
        return response
    except Exception as exc:
        logger.error(f'FCM batch error: {exc}')
