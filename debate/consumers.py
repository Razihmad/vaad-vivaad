import json
import logging
from typing import Callable, Dict

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from base.decorators import websocket_catch_service_exception
from base.exception import ServiceException
from debate.constants import DebateStatus, DebateViewerStatus, MatchQueueStatus
from debate.selectors import (
    update_debate_status,
    update_debate_viewer_status,
    update_match_queue_status,
)
from debate.serializers import (
    DebateViewerSerializer,
    MessageSerializer,
    RoundSerializer,
)
from debate.services import (
    DISCONNECT_GRACE_SECONDS,
    join_queue_outcome,
    check_and_add_user_reaction,
    create_debate_viewer,
    end_turn,
    get_pro_or_con,
    handle_ongoing_debate_disconnect,
    rejoin_active_debate,
    submit_message_and_maybe_advance,
    leave_queue,
    schedule_bot_response_if_needed,
    schedule_debate_judgement,
)
from debate.tasks import send_advance_round_event

logger = logging.getLogger(__name__)
DEBATE_GROUP = "debate_{debate_id}"


class DebateConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for a live debate session.

    Connect:  ws://<host>/ws/debate/

    Each connection is in a per-user group ``user_{user_id}`` (for DMs e.g. match
    offers) and, after a match, the shared group ``debate_{debate_id}`` with the
    opponent. You cannot "add the opponent’s channel" by id; each browser adds its
    own connection to the same named group. Fan-out to both players::

        await channel_layer.group_send(
            f"debate_{debate_id}",
            {"type": "debate.event", "client_type": "round.advanced", "data": {...}},
        )

    Client → Server events:
        {"type": "message", "data": {"content": "..."}}
        {"type": "join_queue", "data": {"topic_id": <int>}}  # fresh matchmaking
        {"type": "join_queue", "data": {"debate_id": <int>}}  # rejoin after a reconnect —
                                                               # pass the debate_id the
                                                               # client was in before the
                                                               # drop; skips matchmaking
        {"type": "debate_completed"}   # client signals its round sequence has finished

    Server → Client events:
        {"type": "queue.matched", "data": {"debate": {...}}}   # match found
        {"type": "queue.matched", "data": {                    # sent only to the
            "debate": {...}, "reconnected": true,              # rejoining client after
            "rounds": [{"round_id", "round_type", "order",     # a reconnect — includes
                        "started_at", "ended_at",               # the transcript so far
                        "messages": [{...}]}, ...]              # so it can resync
        }}
        {"type": "queue.waiting", "data": {"queue_id", "topic"}}  # wait for opponent
        {"type": "message.new",     "message":  {...}}
        {"type": "round.advanced",  "round":    {...}}
        {"type": "debate.judging"}
        {"type": "debate.completed","judgement": {...}}
        {"type": "opponent.disconnected", "data": {
            "debate_id": <int>, "user_id": <int>, "abandoned": <bool>,
            "grace_seconds": <int|null>,  # set when the debate is only pending
                                          # abandonment — the opponent has this long to
                                          # reconnect (send join_queue again) before the
                                          # debate is abandoned and judged as-is
        }}
        {"type": "error",           "message":  "..."}
    """

    # ── Connection lifecycle ──────────────────────────────────────────────────

    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            await self.close(code=4001)
            return
        self.user = user
        self.app_version = self.scope["app_version"]
        self.user_group_name = f"user_{self.user.id}"
        self.debate_id = None
        self.debate_group_name = None
        self.is_viewer = False

        await self._group_add(self.user_group_name, reason="connect")
        await self.accept()
        logger.info(
            f"user_id={self.user.id} app_version={self.app_version} "
            f"group={self.user_group_name} connected successfully"
        )

    async def disconnect(self, close_code):
        if not hasattr(self, "user"):
            return
        await self.handle_leave_queue({})
        await self.viewer_left(data={"status": DebateViewerStatus.DISCONNECTED})
        await self.handle_participant_disconnect(close_code)
        if getattr(self, "debate_group_name", None):
            await self._group_discard(self.debate_group_name, reason="disconnect")
        if hasattr(self, "user_group_name"):
            await self._group_discard(self.user_group_name, reason="disconnect")
        logger.info(f"user_id={self.user.id} disconnected close_code={close_code}")

    async def handle_participant_disconnect(self, close_code):
        """Covers a participant's socket closing mid-debate (``handle_leave_queue`` can't
        reach an ONGOING debate: once matched, the user's MatchQueue row is MATCHED not
        PENDING, so ``leave_queue`` always raises and that path is a no-op here).

        close_code 1000 is a clean close — the client deliberately closed the socket
        (e.g. the user tapped "leave debate") — so we abandon immediately. Any other
        code (or an abrupt drop with no close frame at all) is treated as a lost
        connection: we give the client ``DISCONNECT_GRACE_SECONDS`` to reconnect and
        rejoin (via ``join_queue``) before abandoning.
        """
        if self.is_viewer or not self.debate_id or not self.debate_group_name:
            return
        outcome = await database_sync_to_async(handle_ongoing_debate_disconnect)(
            user=self.user,
            debate_id=self.debate_id,
            group_name=self.debate_group_name,
            graceful=close_code == 1000,
        )
        if outcome == "noop":
            return
        logger.info(
            f"user_id={self.user.id} debate_id={self.debate_id} "
            f"participant_disconnect close_code={close_code} outcome={outcome}"
        )
        await self._group_send(
            self.debate_group_name,
            {
                "type": "opponent.disconnected",
                "data": {
                    "debate_id": self.debate_id,
                    "user_id": self.user.id,
                    "abandoned": outcome == "abandoned",
                    "grace_seconds": DISCONNECT_GRACE_SECONDS
                    if outcome == "grace_period"
                    else None,
                },
            },
        )

    async def _send_error(self, message: str):
        await self.send(text_data=json.dumps({"type": "error", "message": message}))

    # ── Group helpers (logged) ──────────────────────────────────────────────
    async def _group_add(self, group_name: str, reason: str = ""):
        logger.info(
            f"user_id={self.user.id} group_add group={group_name} reason={reason}"
        )
        await self.channel_layer.group_add(group_name, self.channel_name)

    async def _group_discard(self, group_name: str, reason: str = ""):
        logger.info(
            f"user_id={self.user.id} group_discard group={group_name} reason={reason}"
        )
        await self.channel_layer.group_discard(group_name, self.channel_name)

    async def _group_send(self, group_name: str, event: dict):
        logger.info(
            f"user_id={self.user.id} group_send group={group_name} "
            f"event_type={event.get('type')} event={event}"
        )
        await self.channel_layer.group_send(group_name, event)

    async def _add_to_debate_group(self, debate_id: int) -> None:
        """Subscribes this connection to the shared group for that debate (both users)."""
        self.debate_id = debate_id
        self.debate_group_name = DEBATE_GROUP.format(debate_id=debate_id)
        await self._group_add(self.debate_group_name, reason=f"debate_id={debate_id}")

    # ── Incoming messages ─────────────────────────────────────────────────────
    def event_mapping(self) -> Dict[str, Callable]:
        return {
            "message": self.handle_message,
            "end_turn": self.handle_end_turn,
            "typing": self.handle_typing,
            "join_queue": self.handle_join_queue,
            "leave_queue": self.handle_leave_queue,
            "join_viewer": self.handle_join_viewer,
            "viewer_left": self.viewer_left,
            "viewer_reaction": self.add_viewer_reaction,
            "debate_completed": self.handle_debate_completed,
        }

    @websocket_catch_service_exception(default_message="Could not process the message")
    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            await self._send_error("Expected text data")
            return
        try:
            payload = json.loads(text_data)
        except json.JSONDecodeError:
            await self._send_error("Invalid JSON")
            return

        event_type = payload.get("type")
        event_data = payload.get("data") or {}
        logger.info(
            f"user_id={self.user.id} event_received type={event_type} data={event_data}"
        )
        try:
            handler = self.event_mapping()[event_type]
        except KeyError:
            await self._send_error(f"Unknown event type: {event_type!r}")
            return

        await handler(event_data)

    async def handle_message(self, event_data: dict):
        content = event_data.get("content", "")
        if not content or not self.debate_id or not self.debate_group_name:
            await self._send_error("Content is required and debate is active")
            return
        await self.handle_message_submit(content)

    @websocket_catch_service_exception(default_message="Could not submit the message")
    async def handle_message_submit(self, content: str):
        if self.is_viewer:
            self._send_error(message="You cant send message to this deabate")
        debate_id = self.debate_id

        def _submit_and_serialize():
            msg, nxt = submit_message_and_maybe_advance(
                user=self.user, debate_id=debate_id, content=content
            )
            return MessageSerializer(msg).data, (
                RoundSerializer(nxt).data if nxt else None
            )

        message_data, round_data = await database_sync_to_async(_submit_and_serialize)()
        await self._group_send(
            self.debate_group_name,
            {
                "type": "message.new",
                "message": message_data,
            },
        )
        if round_data:
            logger.info(
                f"user_id={self.user.id} debate_id={debate_id} "
                f"scheduling send_advance_round_event group={self.debate_group_name}"
            )
            send_advance_round_event.apply_async(
                args=[self.debate_group_name, round_data],
                countdown=1,
            )
        await database_sync_to_async(schedule_bot_response_if_needed)(
            debate_id=debate_id
        )

    @websocket_catch_service_exception(default_message="Could not end your turn")
    async def handle_end_turn(self, event_data: dict):
        if not self.debate_id or not self.debate_group_name:
            await self._send_error("No active debate")
            return
        debate_id = self.debate_id

        def _end_turn_and_serialize():
            nxt = end_turn(user=self.user, debate_id=debate_id)
            return RoundSerializer(nxt).data if nxt else None

        round_data = await database_sync_to_async(_end_turn_and_serialize)()
        if round_data:
            logger.info(
                f"user_id={self.user.id} debate_id={debate_id} "
                f"scheduling send_advance_round_event group={self.debate_group_name}"
            )
            send_advance_round_event.apply_async(
                args=[self.debate_group_name, round_data],
                countdown=1,
            )
        # If this is a bot debate and it's now the bot's turn, schedule its response
        await database_sync_to_async(schedule_bot_response_if_needed)(
            debate_id=debate_id
        )

    async def handle_typing(self, event_data: dict):
        if not self.debate_id or not self.debate_group_name:
            return
        await self._group_send(
            self.debate_group_name,
            {
                "type": "opponent.typing",
                "user_id": self.user.id,
            },
        )

    async def opponent_typing(self, event):
        """Forward typing notification to the client, skipping the sender."""
        if event.get("user_id") == self.user.id:
            return
        await self.send(text_data=json.dumps({"type": "opponent.typing"}))

    async def opponent_disconnected(self, event):
        """Forward disconnect notification to the client, skipping the disconnecter."""
        data = event.get("data", {})
        if data.get("user_id") == self.user.id:
            return
        await self.send(
            text_data=json.dumps({"type": "opponent.disconnected", "data": data})
        )

    @websocket_catch_service_exception(default_message="Could not join the queue")
    async def handle_join_queue(self, event_data: dict):
        # Reconnecting mid-debate: client sends back the debate_id it was in (persisted
        # locally before the drop). Only checked when it's actually provided — a plain
        # join_queue for fresh matchmaking never has a debate_id.
        debate_id = event_data.get("debate_id")
        if debate_id is not None:
            logger.info(
                f"user_id={self.user.id} rejoin_active_debate debate_id={debate_id}"
            )
            rejoin_outcome = await database_sync_to_async(rejoin_active_debate)(
                user=self.user, debate_id=int(debate_id)
            )
            await self.process_join_queue_outcome(rejoin_outcome)
            return

        topic_id = int(event_data.get("topic_id", 0))
        pro_or_con = event_data.get("pro_or_con")
        category_id = event_data.get("category_id", 0)

        pro_or_con = await database_sync_to_async(get_pro_or_con)(
            pro_or_con=pro_or_con, topic_id=topic_id, category_id=category_id
        )
        logger.info(
            f"user_id={self.user.id} join_queue topic_id={topic_id} "
            f"pro_or_con={pro_or_con} category_id={category_id}"
        )
        outcome = await database_sync_to_async(join_queue_outcome)(
            user=self.user,
            topic_id=topic_id,
            pro_or_con=pro_or_con,
            category_id=category_id,
        )
        await self.process_join_queue_outcome(outcome)

    async def process_join_queue_outcome(self, outcome: Dict):
        if outcome["outcome"] == "matched":
            data = {"debate": outcome["debate"]}
            self.opponent_id = outcome["opponent_id"]
            await self._add_to_debate_group(outcome["debate"]["id"])

            # Reconnecting: tell this client explicitly (so it doesn't treat this as a
            # brand new match) and hand back the transcript so far, so it can resync
            # whatever it missed while disconnected. The opponent doesn't need this —
            # they already have the transcript — so they only get the base payload below.
            self_data = dict(data)
            if outcome.get("reconnected"):
                self_data["reconnected"] = True
                self_data["rounds"] = outcome.get("rounds", [])

            await self.send(
                text_data=json.dumps({"type": "queue.matched", "data": self_data})
            )
            logger.info(
                f"user_id={self.user.id} opponent_id={self.opponent_id} "
                f"queue.matched sent to self debate_id={outcome['debate']['id']}"
            )
            # Opponent is not in debate_* yet, so they still get this over user_*
            await self._group_send(
                f"user_{outcome['opponent_id']}",
                {
                    "type": "queue.matched",
                    "data": data,
                },
            )
        else:
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "queue.waiting",
                        "data": {
                            "queue_id": outcome["queue_id"],
                            "topic": outcome["topic"],
                        },
                    }
                )
            )

    async def handle_leave_queue(self, event_data: dict):
        try:
            await database_sync_to_async(leave_queue)(user=self.user)
        except ServiceException:
            # User was already matched or not in queue — nothing to leave.
            return
        if self.debate_id:
            await self.process_and_update_status_on_leave()
            await self._group_send(
                self.debate_group_name,
                {
                    "type": "queue.left",
                    "data": {
                        "debate_id": self.debate_id,
                        "left_by": self.user.id,
                        "opponent_id": self.opponent_id,
                    },
                },
            )
            await self._group_discard(self.user_group_name, reason="leave_queue")

    async def process_and_update_status_on_leave(self):
        logger.info(f"user_id={self.user.id} debate_id={self.debate_id} left the queue")
        await database_sync_to_async(update_debate_status)(
            debate_id=self.debate_id, status=DebateStatus.ABANDONED
        )
        await database_sync_to_async(update_match_queue_status)(
            user_id=self.opponent_id,
            status=MatchQueueStatus.PENDING,
            debate_id=self.debate_id,
        )
        await database_sync_to_async(update_match_queue_status)(
            user_id=self.user.id,
            status=MatchQueueStatus.ABANDONED,
            debate_id=self.debate_id,
        )

    async def queue_left(self, event):
        # TODO: what to do with opponent? once the debate is abandoned
        await self._group_discard(self.debate_group_name, reason="queue_left")
        self.debate_id = None
        self.debate_group_name = None
        self.opponent_id = None

    async def queue_matched(self, event):
        """The waitee: join the same ``debate_{id}`` group, then tell the client."""
        payload = event.get("data") or {}
        debate = payload.get("debate") or {}
        debate_id = debate.get("id")
        if debate_id:
            await self._add_to_debate_group(debate_id)
            pro = (debate.get("user_pro") or {}).get("id")
            con = (debate.get("user_con") or {}).get("id")
            if pro and con and self.user.id in (pro, con):
                self.opponent_id = con if self.user.id == pro else pro

        await self.send(
            text_data=json.dumps({"type": "queue.matched", "data": payload})
        )

    async def message_new(self, event):
        """In-debate broadcast from ``group_send`` (type ``message.new``)."""
        await self.send(
            text_data=json.dumps(
                {"type": "message.new", "message": event.get("message", {})}
            )
        )

    async def handle_join_viewer(self, data: Dict):
        debate_id = data.get("debate_id")
        if not debate_id:
            await self._send_error("Debate ID is required")
            return
        self.debate_id = debate_id
        await self._add_to_debate_group(debate_id)
        debate_viewer = await database_sync_to_async(create_debate_viewer)(
            user=self.user, debate_id=debate_id
        )
        self.viewer_id = debate_viewer.id
        self.is_viewer = True
        logger.info(
            f"user_id={self.user.id} debate_id={debate_id} "
            f"joined as viewer viewer_id={self.viewer_id}"
        )
        await self.send(
            text_data=json.dumps(
                {
                    "type": "viewer.joined",
                    "data": DebateViewerSerializer(debate_viewer).data,
                }
            )
        )

    async def viewer_left(self, data: Dict):
        if not self.is_viewer:
            return

        status = data.get("status", DebateViewerStatus.LEFT)

        logger.info(
            f"user_id={self.user.id} debate_id={self.debate_id} "
            f"viewer_id={getattr(self, 'viewer_id', None)} viewer_left status={status}"
        )
        await self._group_discard(self.debate_group_name, reason="viewer_left")
        await database_sync_to_async(update_debate_viewer_status)(
            id=self.viewer_id, status=status
        )
        await self._group_send(
            self.debate_group_name,
            {
                "type": "viewer_left",
                "data": {},
            },
        )

    async def add_viewer_reaction(self, data: Dict):
        if not self.is_viewer:
            return

        reaction = data.get("reaction")
        message_id = data.get("message_id")
        logger.info(
            f"user_id={self.user.id} debate_id={self.debate_id} "
            f"viewer_reaction reaction={reaction} message_id={message_id}"
        )
        await database_sync_to_async(check_and_add_user_reaction)(
            user=self.user, reaction=reaction, message_id=message_id
        )

    async def round_advance(self, event):
        """Forward round.advance group message → client as round.advanced."""
        await self.send(
            text_data=json.dumps(
                {"type": "round.advanced", "round": event.get("data", {})}
            )
        )

    async def debate_result(self, event):
        """Forward judgement group message → client as debate.completed."""
        await self.send(
            text_data=json.dumps(
                {"type": "debate_result", "data": event.get("data", {})}
            )
        )

    @websocket_catch_service_exception(
        default_message="Could not process debate completion"
    )
    async def handle_debate_completed(self, event_data: dict):
        if not self.debate_id or not self.debate_group_name:
            await self._send_error("No active debate")
            return
        logger.info(
            f"user_id={self.user.id} debate_id={self.debate_id} "
            f"scheduling debate judgement group={self.debate_group_name}"
        )
        await database_sync_to_async(schedule_debate_judgement)(
            debate_id=self.debate_id, group_name=self.debate_group_name
        )
