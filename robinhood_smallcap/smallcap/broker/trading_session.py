"""
Managed Agent session manager.

Loads the agent/vault/environment IDs from agent_config.json (written by
init_agent.py) and wraps session lifecycle: create → send task → stream
response → return parsed JSON result.

No Robinhood token is needed here — it lives in the Anthropic vault.
"""
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

import anthropic

BASE = Path(__file__).parent.parent
CONFIG_FILE = BASE / "agent_config.json"

logger = logging.getLogger(__name__)


def _load_config() -> dict:
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"agent_config.json not found at {CONFIG_FILE}. "
            "Run smallcap/setup/init_agent.py first."
        )
    return json.loads(CONFIG_FILE.read_text())


class TradingSession:
    """
    One session per trading day.

    Usage:
        with TradingSession(title="2026-06-22") as session:
            result = session.run_task("Scan WOLF, MARA for signals…")
    """

    def __init__(self, title: Optional[str] = None):
        config = _load_config()
        self.client = anthropic.Anthropic()
        self.agent_id = config["agent_id"]
        self.environment_id = config["environment_id"]
        self.vault_id = config["vault_id"]
        self.title = title or f"Trading {time.strftime('%Y-%m-%d')}"
        self._session = None

    # ── Context manager ────────────────────────────────────────────────────────

    def __enter__(self):
        kwargs = dict(
            agent=self.agent_id,
            environment_id=self.environment_id,
            title=self.title,
        )
        if self.vault_id:
            kwargs["vault_ids"] = [self.vault_id]
        self._session = self.client.beta.sessions.create(**kwargs)
        logger.info(f"Session started: {self._session.id}")
        return self

    def __exit__(self, *_):
        if self._session:
            try:
                self.client.beta.sessions.archive(session_id=self._session.id)
                logger.info(f"Session archived: {self._session.id}")
            except Exception as exc:
                logger.warning(f"Session archive failed: {exc}")
            self._session = None

    # ── Task execution ─────────────────────────────────────────────────────────

    def run_task(self, task: str, timeout_secs: int = 300) -> dict:
        """
        Send a task message to the agent and block until it goes idle.
        Returns the parsed JSON result block from the agent's response.
        """
        if not self._session:
            raise RuntimeError("Session not started. Use as a context manager.")

        session_id = self._session.id
        full_text = ""
        deadline = time.monotonic() + timeout_secs

        with self.client.beta.sessions.events.stream(session_id=session_id) as stream:
            self.client.beta.sessions.events.send(
                session_id=session_id,
                events=[{
                    "type": "user.message",
                    "content": [{"type": "text", "text": task}],
                }],
            )
            for event in stream:
                if time.monotonic() > deadline:
                    logger.warning(f"Task timed out after {timeout_secs}s")
                    break
                if event.type == "agent.message":
                    for block in event.content:
                        if block.type == "text":
                            full_text += block.text
                elif event.type == "session.status_idle":
                    break
                elif event.type == "session.status_terminated":
                    logger.error("Session terminated unexpectedly")
                    self._session = None
                    break
                elif event.type == "session.error":
                    logger.error(f"Session error: {event}")
                    break

        return _extract_json(full_text)


def _extract_json(text: str) -> dict:
    """Pull the last ```json … ``` block from the agent's response."""
    matches = re.findall(r"```json\s*([\s\S]*?)\s*```", text)
    if matches:
        try:
            return json.loads(matches[-1])
        except json.JSONDecodeError:
            pass
    # Fallback: try to find a bare {...} object
    bare = re.search(r"\{[\s\S]*\}", text)
    if bare:
        try:
            return json.loads(bare.group())
        except json.JSONDecodeError:
            pass
    logger.warning("Could not parse JSON from agent response")
    return {"status": "parse_error", "raw": text[:500]}
