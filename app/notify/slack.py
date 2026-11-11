"""처리 결과를 원래 Slack 스레드에 회신한다. 토큰이 없으면 로그만 남긴다."""
import logging

log = logging.getLogger(__name__)


class SlackNotifier:
    def __init__(self, bot_token: str):
        self.client = None
        if bot_token:
            from slack_sdk import WebClient

            self.client = WebClient(token=bot_token)

    def post(self, channel: str, text: str, thread_ts: str | None = None) -> None:
        if not self.client:
            log.info("[slack disabled] #%s %s", channel, text[:500])
            return
        self.client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts, mrkdwn=True)
