"""Runs the residents' Telegram bot: python manage.py run_telegram_bot"""

import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from sensor import telegram

log = logging.getLogger(__name__)

# Telegram holds the request open this long when there is nothing new
POLL_SECONDS = 50


class Command(BaseCommand):

    help = "Runs the Telegram bot for residents (long polling)."

    def handle(self, *args, **options):

        if not telegram.enabled():
            # Idles instead of exiting, so Docker does not restart it in a loop
            self.stderr.write("TELEGRAM_BOT_TOKEN is not set; the bot is off. Add it to .env.")
            while True:
                time.sleep(3600)

        me = self.connect()

        self.stdout.write(f"Qaldyq bot @{me['username']} is running")

        offset = None

        while True:

            try:
                updates = telegram.call("getUpdates", {
                    "timeout": POLL_SECONDS,
                    "offset": offset,
                    "allowed_updates": ["message", "callback_query"],
                }, timeout=POLL_SECONDS + 15)
            except telegram.TelegramError as error:
                log.warning("getUpdates failed: %s", error)
                time.sleep(5)
                continue

            for update in updates:

                offset = update["update_id"] + 1

                close_old_connections()

                try:
                    telegram.handle_update(update)
                except Exception:
                    # One broken message must not stop the bot for everyone
                    log.exception("Telegram update %s failed", update.get("update_id"))

    def connect(self):

        while True:
            try:
                me = telegram.call("getMe")
                # Long polling only works without a webhook
                telegram.call("deleteWebhook")
                telegram.call("setMyCommands", {"commands": telegram.COMMANDS})
                return me
            except telegram.TelegramError as error:
                self.stderr.write(f"Telegram is not reachable yet: {error}")
                time.sleep(10)
