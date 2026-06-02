import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)


class DonationConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.user = self.scope.get('user')

        # ── Reject anonymous / unauthenticated connections ────────────────────
        if not self.user or not self.user.is_authenticated:
            await self.close(code=4001)
            return

        # ── Guard: AnonymousUser has no .role attribute ───────────────────────
        user_role = getattr(self.user, 'role', None)
        if not user_role:
            await self.close(code=4001)
            return

        # ── Join role-specific group ──────────────────────────────────────────
        if user_role == 'hospital':
            self.group_name = f'hospital_{self.user.id}'
        elif user_role == 'donor':
            self.group_name = f'donor_{self.user.id}'
        elif user_role == 'ward_member':
            self.group_name = f'ward_{self.user.id}'
        else:
            await self.close(code=4001)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)

        # Hospitals also join TV screen group
        if user_role == 'hospital':
            await self.channel_layer.group_add(f'tv_{self.user.id}', self.channel_name)

        await self.accept()
        await self.send(json.dumps({
            'type': 'connected',
            'message': 'WebSocket connected',
            'role': user_role,
        }))
        logger.info(f'WS connected: {self.user.email} → {self.group_name}')

    async def disconnect(self, code):
        # Safe disconnect — guard against AnonymousUser & missing attributes
        try:
            if hasattr(self, 'group_name'):
                await self.channel_layer.group_discard(self.group_name, self.channel_name)
        except Exception:
            pass

        try:
            user_role = getattr(self.user, 'role', None) if hasattr(self, 'user') else None
            if user_role == 'hospital' and hasattr(self, 'user'):
                await self.channel_layer.group_discard(f'tv_{self.user.id}', self.channel_name)
        except Exception:
            pass

    async def receive(self, text_data):
        try:
            data     = json.loads(text_data)
            msg_type = data.get('type')

            if msg_type == 'join_chat':
                response_id = data.get('response_id')
                if response_id:
                    room = f'chat_{response_id}'
                    await self.channel_layer.group_add(room, self.channel_name)
                    await self.send(json.dumps({
                        'type': 'chat_joined',
                        'response_id': response_id,
                    }))

            elif msg_type == 'leave_chat':
                response_id = data.get('response_id')
                if response_id:
                    await self.channel_layer.group_discard(
                        f'chat_{response_id}', self.channel_name)

            elif msg_type == 'ping':
                await self.send(json.dumps({'type': 'pong'}))

        except (json.JSONDecodeError, Exception):
            pass

    # ── Event handlers (called via channel_layer.group_send) ─────────────────

    async def donation_event(self, event):
        await self.send(json.dumps({
            'type':    event.get('event_type'),
            'payload': event.get('payload', {}),
        }))

    async def chat_message(self, event):
        await self.send(json.dumps({
            'type':        'chat_message',
            'message_id':  event.get('message_id'),
            'sender_id':   event.get('sender_id'),
            'sender_role': event.get('sender_role'),
            'sender_name': event.get('sender_name'),
            'message':     event.get('message'),
            'created_at':  event.get('created_at'),
        }))
