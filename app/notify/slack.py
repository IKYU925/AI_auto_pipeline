"""처리 결과를 원래 Slack 스레드에 회신한다. 토큰이 없으면 로그만 남긴다."""
import logging

log = logging.getLogger(__name__)


class SlackNotifier:
    def __init__(self, bot_token: str):
        self.client = None
        if bot_token:
            from slack_sdk import WebClient

            self.client = WebClient(token=bot_token)

    def post(self, channel: str, text: str, thread_ts: str | None = None, blocks: list[dict] | None = None) -> str:
        """보낸 메시지의 ts. 보내지 않았거나(토큰 없음) 실패하면 빈 문자열."""
        if not self.client:
            log.info("[slack disabled] #%s %s", channel, text[:500])
            return ""
        from slack_sdk.errors import SlackApiError

        try:
            resp = self.client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts, blocks=blocks, mrkdwn=True)
            return resp.get("ts", "")
        except SlackApiError as e:
            # 처리 결과는 이미 저장됐다. 회신 실패로 워커가 멈추거나 메시지를 다시 처리하면 안 된다
            log.warning("slack reply to #%s failed: %s", channel, e.response.get("error"))
            return ""
