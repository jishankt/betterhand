import os
import django
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.tokens import AccessToken
from urllib.parse import parse_qs
import logging

logger = logging.getLogger(__name__)


class JWTAuthMiddleware(BaseMiddleware):
    """
    Authenticates WebSocket connections using JWT token passed as
    ?token=<access_token> query parameter.
    """
    async def __call__(self, scope, receive, send):
        from channels.db import database_sync_to_async
        from accounts.models import User

        # Parse token from query string
        query_string = scope.get('query_string', b'').decode()
        params       = parse_qs(query_string)
        token_list   = params.get('token', [])

        scope['user'] = AnonymousUser()

        if token_list:
            token_key = token_list[0]
            try:
                # Validate JWT and get user
                access_token = AccessToken(token_key)
                user_id      = access_token['user_id']
                user         = await database_sync_to_async(
                    User.objects.select_related(
                        'hospital_profile', 'donor_profile'
                    ).get
                )(id=user_id)
                scope['user'] = user
                logger.debug(f'WS auth success: {user.email}')
            except Exception as e:
                logger.debug(f'WS auth failed: {e}')
                scope['user'] = AnonymousUser()

        return await super().__call__(scope, receive, send)


from donation.routing import websocket_urlpatterns

application = ProtocolTypeRouter({
    'http': get_asgi_application(),
    'websocket': JWTAuthMiddleware(
        URLRouter(websocket_urlpatterns)
    ),
})
