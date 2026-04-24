import asyncio
import contextlib
import logging

import uvicorn
from aiohttp import ClientSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from starlette.applications import Starlette

from nephthys.tasks.daily_stats import send_daily_stats
from nephthys.tasks.fulfillment_reminder import send_fulfillment_reminder
from nephthys.tasks.update_helpers import update_helpers
from nephthys.utils.delete_thread import process_queue
from nephthys.utils.env import env
from nephthys.utils.logging import parse_level_name
from nephthys.utils.logging import send_heartbeat
from nephthys.utils.logging import setup_otel_logging

print("[nephthys] Module loaded, initializing...", flush=True)
load_dotenv()

try:
    import uvloop

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass

logging.basicConfig(level=logging.NOTSET)
stderr_logger = logging.StreamHandler()
stderr_logger.setLevel(parse_level_name(env.log_level_stderr))
stderr_logger.setFormatter(logging.Formatter(logging.BASIC_FORMAT))
logging.getLogger().handlers = [stderr_logger]
if env.otel_logs_url:
    setup_otel_logging()


@contextlib.asynccontextmanager
async def main(_app: Starlette):
    print("[nephthys] Lifespan starting...", flush=True)
    await send_heartbeat(":neodog_nom_verified: Bot is online!")
    print("[nephthys] Heartbeat sent", flush=True)
    async with ClientSession() as session:
        env.session = session
        await env.db.connect()
        print(f"[nephthys] DB connected: {env.db.is_connected()}", flush=True)

        scheduler = AsyncIOScheduler(timezone="Europe/London")
        if env.daily_summary:
            scheduler.add_job(send_daily_stats, "cron", hour=0, minute=0)

        scheduler.add_job(
            send_fulfillment_reminder,
            "cron",
            hour=14,
            minute=0,
            day_of_week="mon-fri",
            timezone="Europe/London",
        )

        from nephthys.tasks.close_stale import close_stale_tickets
        from datetime import datetime

        if env.stale_ticket_days:
            scheduler.add_job(
                close_stale_tickets,
                "interval",
                hours=1,
                max_instances=1,
                next_run_time=datetime.now(),
            )
        else:
            logging.debug("Stale ticket closing has not been configured")
        scheduler.start()

        delete_msg_task = asyncio.create_task(process_queue())
        await update_helpers()
        handler = None
        if env.slack_app_token:
            if env.environment == "production":
                logging.warning(
                    "You are currently running Socket mode in production. This is NOT RECOMMENDED - you should set up a proper HTTP server with a request URL."
                )
            from slack_bolt.adapter.socket_mode.async_handler import (
                AsyncSocketModeHandler,
            )
            from nephthys.utils.slack import app as slack_app

            handler = AsyncSocketModeHandler(slack_app, env.slack_app_token)
            logging.info("Starting Socket Mode handler")
            await handler.connect_async()

        print(f"[nephthys] Slack app token present: {bool(env.slack_app_token)}", flush=True)
        print(f"[nephthys] Help channel: {env.slack_help_channel}", flush=True)
        print(f"[nephthys] Ticket channel: {env.slack_ticket_channel}", flush=True)
        print(f"[nephthys] Program: {env.program}", flush=True)
        print(f"[nephthys] Lifespan ready, yielding to Uvicorn on port {env.port}", flush=True)

        yield
        scheduler.shutdown()
        delete_msg_task.cancel()

        if handler:
            logging.info("Stopping Socket Mode handler")
            await handler.close_async()


def start():
    try:
        print(f"[nephthys] Starting on port {env.port}, environment={env.environment}", flush=True)
        uvicorn.run(
            "nephthys.utils.starlette:app",
            host="0.0.0.0",
            port=env.port,
            log_level="info" if env.environment != "production" else "warning",
            reload=env.environment == "development",
        )
    except Exception as e:
        print(f"[nephthys] Fatal error during startup: {e}", flush=True)
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    try:
        start()
    except Exception as e:
        print(f"[nephthys] Fatal error during startup: {e}", flush=True)
        import traceback
        traceback.print_exc()
        raise
