"""
Live IMAP Email Fetcher for the Triage Ecosystem.

Connects to Gmail IMAP servers using App Passwords from .env,
fetches unread emails, and returns structured message objects.

This module is shared by both the Academic and Personal triage subsystems.
"""
import os
import imaplib
import email
import email.utils
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _load_env():
    """Load .env file from the agentic root into os.environ (no dependencies)."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        raise FileNotFoundError(
            f".env file not found at {env_path}. "
            "Copy .env.template to .env and fill in your credentials."
        )
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


class IMAPFetcher:
    """
    Connects to Gmail IMAP and fetches unread messages.
    
    Supports two account profiles loaded from .env:
      - 'university': UNIV_IMAP_SERVER / UNIV_IMAP_USER / UNIV_IMAP_PASS
      - 'personal':   PERSONAL_IMAP_SERVER / PERSONAL_IMAP_USER / PERSONAL_IMAP_PASS
    """

    PROFILES = {
        "university": ("UNIV_IMAP_SERVER", "UNIV_IMAP_USER", "UNIV_IMAP_PASS"),
        "personal": ("PERSONAL_IMAP_SERVER", "PERSONAL_IMAP_USER", "PERSONAL_IMAP_PASS"),
    }

    def __init__(self, profile: str = "university"):
        _load_env()
        if profile not in self.PROFILES:
            raise ValueError(f"Unknown profile '{profile}'. Choose from: {list(self.PROFILES.keys())}")

        server_key, user_key, pass_key = self.PROFILES[profile]
        self.server = os.environ[server_key]
        self.user = os.environ[user_key]
        self.password = os.environ[pass_key]
        self.profile = profile
        self._conn: Optional[imaplib.IMAP4_SSL] = None

    # ── connection management ───────────────────────────────────────────
    def connect(self) -> imaplib.IMAP4_SSL:
        """Establish an authenticated IMAP4_SSL connection."""
        logger.info("Connecting to %s as %s …", self.server, self.user)
        self._conn = imaplib.IMAP4_SSL(self.server)
        self._conn.login(self.user, self.password)
        logger.info("Authenticated successfully.")
        return self._conn

    def disconnect(self):
        if self._conn:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.disconnect()

    # ── email fetching ──────────────────────────────────────────────────
    def fetch_unread(self, mailbox: str = "INBOX", limit: int = 25) -> List[Dict[str, Any]]:
        """
        Fetch up to *limit* unread messages from *mailbox*.

        Returns a list of dicts:
            {
                "uid": str,
                "subject": str,
                "from": str,
                "from_email": str,
                "date": str,
                "body_plain": str,
                "body_html": str,
            }
        """
        if self._conn is None:
            self.connect()

        self._conn.select(mailbox, readonly=True)  # readonly so we don't auto‑mark as read
        status, data = self._conn.uid("search", None, "UNSEEN")
        if status != "OK":
            logger.warning("IMAP SEARCH failed: %s", status)
            return []

        uids = data[0].split() if data[0] else []
        if not uids:
            logger.info("No unread messages in %s/%s.", self.profile, mailbox)
            return []

        # Take only the most recent *limit* UIDs
        uids = uids[-limit:]
        logger.info("Found %d unread messages (fetching last %d).", len(uids), limit)

        messages: List[Dict[str, Any]] = []
        for uid in uids:
            status, msg_data = self._conn.uid("fetch", uid, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue

            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            subject = self._decode_header(msg.get("Subject", ""))
            from_raw = msg.get("From", "")
            from_name, from_addr = email.utils.parseaddr(from_raw)
            date_str = msg.get("Date", "")

            body_plain, body_html = self._extract_body(msg)

            messages.append({
                "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
                "subject": subject,
                "from": from_name or from_addr,
                "from_email": from_addr,
                "date": date_str,
                "body_plain": body_plain,
                "body_html": body_html,
            })

        return messages

    # ── draft saving (Never-Auto-Send) ──────────────────────────────────
    def save_draft(self, subject: str, body: str, in_reply_to: str = ""):
        """
        Appends an AI-generated draft to the account's [Gmail]/Drafts folder.
        This method intentionally does NOT send.
        """
        if self._conn is None:
            self.connect()

        from email.mime.text import MIMEText

        draft = MIMEText(body, "plain", "utf-8")
        draft["Subject"] = f"[AI-DRAFT] {subject}"
        draft["From"] = self.user
        if in_reply_to:
            draft["In-Reply-To"] = in_reply_to

        # Gmail stores drafts under [Gmail]/Drafts
        draft_folder = "[Gmail]/Drafts"
        status, _ = self._conn.append(
            draft_folder,
            "\\Draft",
            imaplib.Time2Internaldate(datetime.now(timezone.utc).timestamp()),
            draft.as_bytes(),
        )
        if status != "OK":
            raise RuntimeError(f"Failed to append draft: {status}")
        logger.info("Draft saved: %s", subject)

    # ── helpers ──────────────────────────────────────────────────────────
    @staticmethod
    def _decode_header(raw: str) -> str:
        parts = email.header.decode_header(raw)
        decoded = []
        for part, charset in parts:
            if isinstance(part, bytes):
                decoded.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                decoded.append(part)
        return " ".join(decoded)

    @staticmethod
    def _extract_body(msg: email.message.Message):
        body_plain = ""
        body_html = ""
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                disp = str(part.get("Content-Disposition", ""))
                if "attachment" in disp:
                    continue
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
                if ct == "text/plain":
                    body_plain = text
                elif ct == "text/html":
                    body_html = text
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                body_plain = payload.decode(charset, errors="replace")
        return body_plain, body_html
