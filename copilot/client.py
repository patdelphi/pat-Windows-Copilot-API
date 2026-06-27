"""High-level Copilot client — the recommended entry point.

One client, many conversations addressed by id. :meth:`CopilotClient.chat`
returns the full reply plus the conversation id; pass that id back to continue
the same conversation, or omit it to start a fresh one. :meth:`CopilotClient.stream`
is the incremental variant.

    from copilot import CopilotClient

    client = CopilotClient()                       # loads signed-in auth once
    r = client.chat("My name is Tomato. Remember it.")
    print(r.text, r.conversation_id)

    r2 = client.chat("What's my name?", r.conversation_id)   # continue
    print(r2.text)

    for chunk in client.stream("Tell me a joke"):  # new conversation, streamed
        print(chunk, end="", flush=True)

The signed-in access token is refreshed transparently; sign in once with
``python -m copilot login``. Pass ``anonymous=True`` to skip sign-in (only where
anonymous consumer chat is available), or ``proxy=...`` to route through a
supported region.
"""

import sys
import time
from dataclasses import dataclass, field
from typing import Generator, List, Optional, Union

from .auth import AUTH_MAX_AGE, load_auth
from .driver import ClearanceRequired, Copilot
from .models import Conversation, ImageResponse


def _status(msg: str) -> None:
    """Emit a recovery/progress line to stderr.

    Goes to stderr (not stdout) so it never mixes into the reply text the CLI and
    callers read off stdout. Plain ``print`` keeps it visible by default — this is
    a personal bridge, not a library that should stay silent."""
    print(f"[copilot] {msg}", file=sys.stderr, flush=True)


@dataclass
class ChatReply:
    """The full result of a :meth:`CopilotClient.chat` call."""

    text: str
    conversation_id: Optional[str]
    images: List[ImageResponse] = field(default_factory=list)


class ChatStream:
    """Iterable stream of reply chunks that also exposes the conversation id.

    Yields ``str`` text chunks (and :class:`~copilot.models.ImageResponse` for
    generated images). ``conversation_id`` is known up front when continuing an
    existing conversation, and is populated as soon as iteration begins when a
    new conversation is created.
    """

    def __init__(self, chunks: Generator, conversation_id: Optional[str]):
        self._chunks = chunks
        self.conversation_id = conversation_id

    def __iter__(self) -> Generator[Union[str, ImageResponse], None, None]:
        for item in self._chunks:
            if isinstance(item, Conversation):
                self.conversation_id = item.conversation_id
            else:
                yield item


class CopilotClient:
    """A Copilot client: one object, many conversations addressed by id.

    Parameters
    ----------
    anonymous:
        Skip sign-in and talk to Copilot anonymously. Only works where the
        anonymous consumer experience is available (it is geo-blocked in some
        regions, e.g. India). Default ``False`` uses the signed-in session.
    proxy:
        Optional ``scheme://user:pass@host:port`` proxy, applied to both the
        auth refresh and every request.
    max_age:
        Seconds a cached access token is trusted before it is refreshed.
    interactive_clear:
        When a turn is gated behind a Cloudflare Turnstile (expired
        ``cf_clearance``), recover by opening a *visible* browser and refreshing
        clearance there (the checkbox is auto-clicked, or a human clicks it), then
        retry. Default ``True``. Set ``False`` for headless/server use, where a
        :class:`~copilot.driver.ClearanceRequired` error is raised instead of
        popping a window.
    headless_clear:
        Attempt a *headless* clearance refresh before the visible one. Default
        ``False``: headless Turnstile solving is unreliable on low-trust egress
        (VPN/datacenter IPs) and a failed pass can leave a half-cleared state, so
        the dependable path is a visible window. Enable once headless is proven on
        your egress.
    """

    def __init__(
        self,
        anonymous: bool = False,
        proxy: Optional[str] = None,
        max_age: int = AUTH_MAX_AGE,
        interactive_clear: bool = True,
        headless_clear: bool = False,
    ):
        self._driver = Copilot()
        self._anonymous = anonymous
        self._proxy = proxy
        self._max_age = max_age
        self._interactive_clear = interactive_clear
        self._headless_clear = headless_clear
        self._auth: Optional[dict] = None

    def stream(
        self,
        prompt: str,
        conversation_id: Optional[str] = None,
        **kwargs,
    ) -> ChatStream:
        """Stream the reply to ``prompt`` as a :class:`ChatStream`.

        Starts a new conversation when ``conversation_id`` is ``None``; otherwise
        continues that conversation. Read ``.conversation_id`` on the returned
        stream (during/after iteration) to continue the chat later.

        If the turn is gated behind a Cloudflare Turnstile (expired
        ``cf_clearance``), it is transparently recovered: clearance is refreshed
        in a visible browser (the checkbox is auto-clicked, or a human clicks it)
        and the turn is retried. Recovery only happens before any text is emitted,
        so output is never duplicated.
        """
        return ChatStream(
            self._stream_with_recovery(prompt, conversation_id, kwargs),
            conversation_id,
        )

    def _stream_with_recovery(self, prompt, conversation_id, kwargs):
        """Drive the turn, recovering from an expired-clearance Turnstile by
        refreshing clearance in a browser and retrying.

        Recovery opens a *visible* browser by default: headless solving is
        unreliable on low-trust egress, so the dependable path is a real window
        (auto-clicked checkbox, or a human click). A headless pre-pass runs only
        when ``headless_clear`` is set. Recovery happens only before any text is
        emitted, so output is never duplicated.
        """
        # Browser recovery passes, in order: a headless pre-pass (opt-in) then a
        # visible window. Each entry is the ``headless`` flag for that pass.
        strategies = []
        if not self._anonymous:
            if self._headless_clear:
                strategies.append(True)
            if self._interactive_clear:
                strategies.append(False)
        total = len(strategies) + 1  # +1 for the initial as-is attempt

        for attempt in range(total):
            auth = self._fresh_auth()
            kw = dict(
                stream=True,
                proxy=self._proxy,
                cookies=(auth.get("cookie_records") or auth.get("cookies")) if auth else None,
                access_token=auth["access_token"] if auth else None,
                identity_type=auth.get("identity_type") if auth else None,
                chat_hub=auth.get("chat_hub") if auth else None,
                chat_request_template=auth.get("chat_request_template") if auth else None,
                **kwargs,
            )
            if conversation_id is None:
                kw["return_conversation"] = True  # have the driver hand back its id
            else:
                kw["conversation_id"] = conversation_id

            if attempt:
                _status(f"Retrying the message (attempt {attempt + 1}/{total})...")
            produced = False  # any user-visible output yet? (Conversation doesn't count)
            try:
                for item in self._driver.create_completion(prompt, **kw):
                    if not isinstance(item, Conversation):
                        produced = True
                    yield item
                return
            except ClearanceRequired:
                if produced:
                    _status("Cloudflare clearance expired mid-reply — can't recover "
                            "without duplicating output; surfacing the error.")
                    raise
                if attempt >= len(strategies):
                    # No (more) recovery passes available — anonymous, server
                    # (no visible browser), or the last pass already ran.
                    _status("Cloudflare clearance could not be refreshed; giving up.")
                    raise
                self._refresh_clearance(headless=strategies[attempt])
                self._auth = None  # force a reload of the freshly-snapshotted auth

    def _refresh_clearance(self, headless: bool) -> None:
        """Refresh Cloudflare clearance via a browser, re-snapshotting token.json.

        Headless first (automatic when Cloudflare trusts the session); a visible
        window second so a human can pass an escalated interactive checkbox.
        """
        from .browser import BrowserCopilot

        if headless:
            _status("Cloudflare clearance expired — attempting a headless refresh...")
        else:
            _status("Cloudflare clearance expired — opening a browser. "
                    "Click the 'verify you're human' checkbox if it appears.")
        bot = BrowserCopilot(headless=headless, proxy=self._proxy)
        try:
            earned = bot.auto_clear()
        finally:
            bot.close()
        _status("Clearance refreshed." if earned
                else "Browser did not earn fresh clearance.")

    def chat(
        self,
        prompt: str,
        conversation_id: Optional[str] = None,
        **kwargs,
    ) -> ChatReply:
        """Return the full reply to ``prompt`` as a :class:`ChatReply`.

        Buffers the whole response; use :meth:`stream` for incremental output.
        """
        s = self.stream(prompt, conversation_id=conversation_id, **kwargs)
        text: List[str] = []
        images: List[ImageResponse] = []
        for item in s:
            if isinstance(item, str):
                text.append(item)
            elif isinstance(item, ImageResponse):
                images.append(item)
        return ChatReply("".join(text), s.conversation_id, images)

    def _fresh_auth(self) -> Optional[dict]:
        """Return current signed-in auth, refreshing it when stale (or None)."""
        if self._anonymous:
            return None
        if self._auth is None or (time.time() - self._auth.get("saved_at", 0)) >= self._max_age:
            self._auth = load_auth(max_age=self._max_age, proxy=self._proxy)
        return self._auth
