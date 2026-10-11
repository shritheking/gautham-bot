try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic_settings import BaseSettings
import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

class Settings(BaseSettings):
    supabase_url: str = os.getenv("SUPABASE_URL", "")
    supabase_service_role_key: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    bot_token: str = os.getenv("BOT_TOKEN", "")
    admin_telegram_ids: str = os.getenv("ADMIN_TELEGRAM_IDS", "")
    
from contextlib import asynccontextmanager
import asyncio
import sys

# We need to add the parent directory to sys.path if we are running from the backend directory, or just import using absolute path if we're running from the root.
# Assuming the root is gautam-trading
try:
    from bot.main import bot, bots, dp
except ImportError:
    import sys, os
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from bot.main import bot, bots, dp

from aiogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure any previous webhook is deleted for all bots
    for b in bots:
        try:
            await b.delete_webhook(drop_pending_updates=True)
            print("Webhook cleared for bot.")
        except Exception as e:
            print(f"Error deleting webhook: {e}")

        # Set default commands for everyone
        try:
            await b.set_my_commands(
                [BotCommand(command="start", description="Start the bot")],
                scope=BotCommandScopeDefault()
            )
        except Exception as e:
            print(f"Error setting default commands: {e}")
        
        # Set admin commands for admins
        admin_ids = settings.admin_telegram_ids.split(",")
        for aid in admin_ids:
            if aid.strip():
                try:
                    await b.set_my_commands(
                        [
                            BotCommand(command="start", description="Start the bot"),
                            BotCommand(command="admin", description="Admin Dashboard")
                        ],
                        scope=BotCommandScopeChat(chat_id=aid.strip())
                    )
                except Exception as e:
                    print(f"Error setting admin commands for {aid}: {e}")

    # Start multi-bot polling in the background with error logging
    async def run_bot():
        try:
            print(f"Telegram polling started successfully for {len(bots)} bot(s)!")
            await dp.start_polling(*bots)
        except Exception as e:
            print(f"Fatal error in Telegram bot polling: {e}")

    asyncio.create_task(run_bot())
    yield

settings = Settings()

app = FastAPI(title="Gautam Trading Admin API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_supabase() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_role_key)

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/api/dashboard/stats")
def get_dashboard_stats():
    # Fetch stats using Supabase
    supabase = get_supabase()
    
    # In a real app we would do count queries. Doing basic fetches for now.
    users_resp = supabase.table("users").select("id", count="exact").execute()
    active_users_resp = supabase.table("users").select("id", count="exact").eq("status", "active").execute()
    clicks_resp = supabase.table("affiliate_clicks").select("id", count="exact").execute()
    
    return {
        "total_users": users_resp.count if hasattr(users_resp, 'count') else 0,
        "active_users": active_users_resp.count if hasattr(active_users_resp, 'count') else 0,
        "affiliate_clicks": clicks_resp.count if hasattr(clicks_resp, 'count') else 0,
        "messages_waiting": 0
    }

@app.get("/api/users")
def get_users():
    supabase = get_supabase()
    res = supabase.table("users").select("*").order("joined_at", desc=True).execute()
    return res.data

@app.get("/api/users/{telegram_id}/messages")
def get_user_messages(telegram_id: str):
    supabase = get_supabase()
    res = supabase.table("messages").select("*").eq("telegram_id", telegram_id).order("created_at").execute()
    return res.data

@app.get("/api/reviews")
def get_reviews():
    supabase = get_supabase()
    res = supabase.table("reviews").select("*").order("created_at", desc=True).execute()
    return res.data

@app.get("/api/broadcasts")
def get_broadcasts():
    supabase = get_supabase()
    res = supabase.table("broadcasts").select("*").order("created_at", desc=True).execute()
    return res.data

from pydantic import BaseModel
import httpx

class ReplyRequest(BaseModel):
    message: str

@app.post("/api/users/{telegram_id}/reply")
async def send_reply(telegram_id: str, req: ReplyRequest):
    supabase = get_supabase()
    
    # Send via Telegram API
    url = f"https://api.telegram.org/bot{settings.bot_token}/sendMessage"
    async with httpx.AsyncClient() as client:
        res = await client.post(url, json={
            "chat_id": telegram_id,
            "text": req.message
        })
        
    if res.status_code == 200:
        # Save to DB
        supabase.table("messages").insert({
            "telegram_id": telegram_id,
            "direction": "outbound",
            "message": req.message,
            "message_type": "text"
        }).execute()
        return {"status": "success"}
    return {"status": "error", "details": res.text}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
