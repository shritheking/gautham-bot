import asyncio
import os
import ssl

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo
from supabase import create_client, Client
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from pathlib import Path
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)
load_dotenv()

# Parse one or more bot tokens (comma-separated or single)
raw_tokens = os.getenv("BOT_TOKENS", os.getenv("BOT_TOKEN", "8922209875:AAHYofX2CaaL4hDRX28hwi2XJEzDyeYn098"))
BOT_TOKENS = [t.strip() for t in raw_tokens.split(",") if t.strip()]
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://trvwzyueymbsochgzeix.supabase.co")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InRydnd6eXVleW1ic29jaGd6ZWl4Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc5MTczMTIzMiwiZXhwIjoyMTA3MzA3MjMyfQ.v6S2aMdUlID7eUjIT9LiL0Gz0JFlIJsGtUF8w8qP8Xk")

from aiohttp import TCPConnector, ClientSession
from aiogram.client.session.aiohttp import AiohttpSession

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

class CustomSession(AiohttpSession):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._custom_session = None

    async def create_session(self) -> ClientSession:
        if self._custom_session is None or self._custom_session.closed:
            connector = TCPConnector(ssl=ssl_context)
            self._custom_session = ClientSession(connector=connector, json_serialize=self.json_dumps)
        return self._custom_session

    async def close(self) -> None:
        if self._custom_session and not self._custom_session.closed:
            await self._custom_session.close()
        await super().close()

BOT_MODE = os.getenv("BOT_MODE", "trading").lower()  # Options: 'trading' or 'ai'

# Create Bot instances for all configured tokens
bots = [Bot(token=t, session=CustomSession()) for t in BOT_TOKENS]
bot = bots[0]  # Primary bot reference
dp = Dispatcher()
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# ================= STATES =================
class RegState(StatesGroup):
    waiting_for_quotex_id = State()

class AdminEditState(StatesGroup):
    waiting_for_link = State()
    waiting_for_review_image = State()

class BroadcastState(StatesGroup):
    waiting_for_message = State()

class AIState(StatesGroup):
    waiting_for_prompt = State()

# ================= USER FLOW =================
@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext, bot: Bot):
    await state.clear()
    telegram_id = str(message.from_user.id)
    username = message.from_user.username
    first_name = message.from_user.first_name
    last_name = message.from_user.last_name
    
    # Get current bot info dynamically (name and username)
    try:
        bot_info = await bot.get_me()
        bot_name = bot_info.first_name or "Gautam Trading"
        bot_username = bot_info.username or "bot"
    except Exception:
        bot_name = "Gautam Trading"
        bot_username = "bot"
    
    # 1. Upsert User (with bot_username tracked)
    try:
        user_res = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute()
        user_data = {
            "telegram_id": telegram_id,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "bot_username": bot_username,
            "source": "organic",
            "status": "active"
        }
        if not user_res.data:
            try:
                supabase.table("users").insert(user_data).execute()
            except Exception:
                # Fallback if bot_username column has not been added yet
                user_data.pop("bot_username", None)
                supabase.table("users").insert(user_data).execute()
        else:
            try:
                supabase.table("users").update({"status": "active", "bot_username": bot_username}).eq("telegram_id", telegram_id).execute()
            except Exception:
                supabase.table("users").update({"status": "active"}).eq("telegram_id", telegram_id).execute()
    except Exception as e:
        print(f"Error upserting user: {e}")

    # 2. Check BOT_MODE: If switched to AI mode, display the AI Assistant interface
    if BOT_MODE == "ai":
        ai_menu = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💬 Ask AI Anything", callback_data="ai_ask")],
            [InlineKeyboardButton(text="📝 Rewrite Text", callback_data="ai_rewrite"),
             InlineKeyboardButton(text="📚 Summarize", callback_data="ai_summarize")],
            [InlineKeyboardButton(text="✍️ Generate Ideas", callback_data="ai_generate"),
             InlineKeyboardButton(text="✅ Fix Grammar", callback_data="ai_grammar")]
        ])
        await message.answer(
            f"<b>👋 Welcome to {bot_name} AI Assistant!</b>\n\n"
            f"Your personal AI-powered text and trading assistant.\n\n"
            f"Choose an option below to get started:",
            reply_markup=ai_menu,
            parse_mode="HTML"
        )
        return

    # 2. Sequential Messages for Trading Mode (Dynamic bot name!)
    await message.answer(f"👋 Welcome to {bot_name}!\n\nWe're excited to have you here.")
    await asyncio.sleep(1)
    
    # 3. Reviews (Images & Videos - filtered for this bot)
    try:
        reviews_res = supabase.table("review_images").select("*").eq("bot_username", bot_username).execute()
        r_list = reviews_res.data
        if not r_list:
            # Fallback to reviews without a specific bot_username or all reviews
            reviews_res = supabase.table("review_images").select("*").execute()
            r_list = reviews_res.data

        if r_list:
            media_group = []
            for r in r_list[:10]:
                mtype = r.get('media_type', 'photo')
                if mtype == 'video':
                    media_group.append(InputMediaVideo(media=r['file_id']))
                else:
                    media_group.append(InputMediaPhoto(media=r['file_id']))
            if media_group:
                await message.answer_media_group(media=media_group)
                await asyncio.sleep(1)
                await message.answer("⭐️ **These are some of the amazing results from our VIP clients!**", parse_mode="Markdown")
                await asyncio.sleep(1)
    except Exception as e:
        print("Error sending reviews:", e)
        
    # 4. Public Channel & Affiliate Settings
    pub_link = "https://t.me/GautamTrading"
    aff_link = "https://quotex.com"
    try:
        settings_res = supabase.table("bot_settings").select("*").execute()
        if settings_res.data:
            settings_dict = {s['setting_key']: s['setting_value'] for s in settings_res.data}
            pub_link = settings_dict.get(f"public_channel_link_{bot_username}", settings_dict.get("public_channel_link", pub_link))
            aff_link = settings_dict.get(f"affiliate_url_{bot_username}", settings_dict.get("affiliate_url", aff_link))
    except Exception as e:
        print(f"Error fetching settings: {e}")

    await message.answer(f"📢 Join our Free Public Channel for daily updates:\n{pub_link}")
    await asyncio.sleep(1)
    
    # 5. Quotex Affiliate
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ I Have Registered", callback_data="btn_registered")]
    ])
    await message.answer(
        f"🚀 To get VIP Access, you MUST register using our official Quotex link:\n\n{aff_link}\n\nOnce you have registered, click the button below!",
        reply_markup=markup,
        disable_web_page_preview=True
    )

@dp.callback_query(F.data == "btn_registered")
async def btn_registered(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("📝 Please enter your Quotex ID below:")
    await state.set_state(RegState.waiting_for_quotex_id)
    await callback.answer()

# ================= AI MODE HANDLERS =================
@dp.callback_query(F.data.startswith("ai_"))
async def handle_ai_callbacks(callback: types.CallbackQuery, state: FSMContext):
    action = callback.data.split("_")[1]
    prompt_types = {
        "ask": "💬 Please send the question you would like me to answer:",
        "rewrite": "📝 Please send the text you want me to rewrite professionally:",
        "summarize": "📚 Please send the text or article you want me to summarize:",
        "generate": "✍️ Please describe the ideas or content you want me to generate:",
        "grammar": "✅ Please send the sentence or paragraph to check and fix grammar:"
    }
    prompt_text = prompt_types.get(action, "Please send your text:")
    await state.set_state(AIState.waiting_for_prompt)
    await state.update_data(ai_action=action)
    await callback.message.answer(prompt_text)
    await callback.answer()

@dp.message(AIState.waiting_for_prompt)
async def process_ai_prompt(message: types.Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    action = data.get("ai_action", "ask")
    user_input = message.text.strip()
    
    await message.answer("🤖 Thinking... please wait a moment.")
    await state.clear()
    
    # Simple, high-speed OpenAI / AI completion if AI_API_KEY is configured
    ai_api_key = os.getenv("AI_API_KEY", "")
    if ai_api_key:
        try:
            import httpx
            ai_base_url = os.getenv("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
            headers = {"Authorization": f"Bearer {ai_api_key}", "Content-Type": "application/json"}
            payload = {
                "model": "gpt-3.5-turbo",
                "messages": [
                    {"role": "system", "content": f"You are a helpful AI assistant. Action requested: {action}."},
                    {"role": "user", "content": user_input}
                ]
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(f"{ai_base_url}/chat/completions", headers=headers, json=payload)
                result_json = res.json()
                reply_text = result_json["choices"][0]["message"]["content"]
                await message.answer(reply_text)
                return
        except Exception as e:
            print(f"AI API error: {e}")
            
    # Clean fallback response if no AI API key is set
    await message.answer(f"✨ <b>AI Result ({action.upper()}):</b>\n\nI processed your request for: <i>\"{user_input}\"</i>\n\n(To enable live ChatGPT responses, set <code>AI_API_KEY</code> in your environment variables).", parse_mode="HTML")

@dp.message(RegState.waiting_for_quotex_id)
async def process_quotex_id(message: types.Message, state: FSMContext):
    quotex_id = message.text.strip()
    telegram_id = str(message.from_user.id)
    
    # Check for duplicate Quotex ID
    existing_res = supabase.table("users").select("telegram_id").eq("quotex_id", quotex_id).execute()
    if existing_res.data:
        existing_owner = existing_res.data[0]["telegram_id"]
        if existing_owner == telegram_id:
            await message.answer("⚠️ You have already registered this Quotex ID!")
        else:
            await message.answer("❌ This Quotex ID is already registered with another Telegram account.")
        await state.clear()
        return
    
    # Update DB
    supabase.table("users").update({
        "quotex_id": quotex_id,
        "approval_status": "pending"
    }).eq("telegram_id", telegram_id).execute()
    
    await message.answer("✅ Your Quotex ID has been submitted and is currently under review by our admin. Please wait.")
    await state.clear()
    
    # Identify which bot received this registration
    try:
        this_bot_info = await message.bot.get_me()
        this_bot_username = this_bot_info.username or "bot"
    except Exception:
        this_bot_username = "bot"

    # Notify Admins — include bot_username in callback so approval goes back through correct bot
    admin_ids = os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")
    for aid in admin_ids:
        if not aid.strip(): continue
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Approve", callback_data=f"approve_{telegram_id}_{this_bot_username}")],
            [InlineKeyboardButton(text="❌ Reject", callback_data=f"reject_{telegram_id}_{this_bot_username}")]
        ])
        try:
            await message.bot.send_message(
                chat_id=aid.strip(),
                text=f"🔔 *New Registration!*\n\nUser: {message.from_user.full_name} (@{message.from_user.username or 'none'})\nQuotex ID: `{quotex_id}`\nBot: @{this_bot_username}",
                parse_mode="Markdown",
                reply_markup=markup
            )
        except Exception as e:
            print("Failed to notify admin:", e)

# ================= ADMIN REGISTRATION CONTROLS =================
@dp.callback_query(F.data.startswith("approve_"))
async def admin_approve(callback: types.CallbackQuery):
    if str(callback.from_user.id) not in os.getenv("ADMIN_TELEGRAM_IDS", "").split(","): return
    # callback_data format: approve_{telegram_id}_{bot_username}
    parts = callback.data.split("_", 2)
    user_id = parts[1]
    target_bot_username = parts[2] if len(parts) > 2 else None

    supabase.table("users").update({"approval_status": "approved"}).eq("telegram_id", user_id).execute()

    # Find the correct bot that the user registered through
    target_bot = bots[0]  # fallback to Bot 1
    if target_bot_username:
        for b in bots:
            try:
                info = await b.get_me()
                if info.username == target_bot_username:
                    target_bot = b
                    break
            except Exception:
                continue

    b_user = target_bot_username or "bot"
    settings_res = supabase.table("bot_settings").select("*").execute()
    vip_link = "No VIP link set by admin."
    if settings_res.data:
        s_dict = {s['setting_key']: s['setting_value'] for s in settings_res.data}
        vip_link = s_dict.get(f"vip_channel_link_{b_user}", s_dict.get("vip_channel_link", vip_link))

    try:
        await callback.message.edit_text(callback.message.text + "\n\n✅ **APPROVED**", parse_mode="Markdown")
        await target_bot.send_message(user_id, f"🎉 Approved! Join the VIP Channel here: {vip_link}")
    except Exception as e:
        print(f"Approve send error: {e}")
    await callback.answer("User Approved")

@dp.callback_query(F.data.startswith("reject_"))
async def admin_reject(callback: types.CallbackQuery):
    if str(callback.from_user.id) not in os.getenv("ADMIN_TELEGRAM_IDS", "").split(","): return
    # callback_data format: reject_{telegram_id}_{bot_username}
    parts = callback.data.split("_", 2)
    user_id = parts[1]
    target_bot_username = parts[2] if len(parts) > 2 else None

    supabase.table("users").update({"approval_status": "rejected"}).eq("telegram_id", user_id).execute()

    # Find the correct bot that the user registered through
    target_bot = bots[0]  # fallback to Bot 1
    if target_bot_username:
        for b in bots:
            try:
                info = await b.get_me()
                if info.username == target_bot_username:
                    target_bot = b
                    break
            except Exception:
                continue

    try:
        await callback.message.edit_text(callback.message.text + "\n\n❌ **REJECTED**", parse_mode="Markdown")
        await target_bot.send_message(user_id, "❌ Rejected! Please make sure you deposited at least $50 and try again.")
    except Exception as e:
        print(f"Reject send error: {e}")
    await callback.answer("User Rejected")

# ================= ADMIN DASHBOARD =================
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message, bot: Bot):
    if str(message.from_user.id) not in os.getenv("ADMIN_TELEGRAM_IDS", "").split(","):
        await message.answer("❌ This command is not accessible. You do not have admin permissions.")
        return
    
    try:
        b_info = await bot.get_me()
        b_name = b_info.first_name or "Trading Bot"
    except Exception:
        b_name = "Trading Bot"

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Broadcast Message", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🔗 Edit Affiliate Link", callback_data="edit_affiliate")],
        [InlineKeyboardButton(text="📢 Edit Public Channel Link", callback_data="edit_public")],
        [InlineKeyboardButton(text="👑 Edit VIP Channel Link", callback_data="edit_vip")],
        [InlineKeyboardButton(text="📸 Add Review Image/Video", callback_data="add_review_img")],
        [InlineKeyboardButton(text="🗑️ Clear This Bot's Reviews", callback_data="clear_reviews")]
    ])
    await message.answer(f"🛠 **{b_name} Admin Dashboard**\n\nSettings and actions you change here will apply specifically to this bot.", reply_markup=markup, parse_mode="Markdown")

# Edit Links (Saved specifically per bot)
@dp.callback_query(F.data == "edit_affiliate")
async def edit_aff(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    b_info = await bot.get_me()
    b_user = b_info.username or "bot"
    await state.set_state(AdminEditState.waiting_for_link)
    await state.update_data(setting_key=f"affiliate_url_{b_user}")
    await callback.message.answer(f"🔗 Please send the new **Quotex Affiliate Link** for @{b_user}:")
    await callback.answer()

@dp.callback_query(F.data == "edit_public")
async def edit_pub(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    b_info = await bot.get_me()
    b_user = b_info.username or "bot"
    await state.set_state(AdminEditState.waiting_for_link)
    await state.update_data(setting_key=f"public_channel_link_{b_user}")
    await callback.message.answer(f"📢 Please send the new **Public Channel Link** for @{b_user}:")
    await callback.answer()
    
@dp.callback_query(F.data == "edit_vip")
async def edit_vip(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    b_info = await bot.get_me()
    b_user = b_info.username or "bot"
    await state.set_state(AdminEditState.waiting_for_link)
    await state.update_data(setting_key=f"vip_channel_link_{b_user}")
    await callback.message.answer(f"👑 Please send the new **VIP Channel Link** for @{b_user}:")
    await callback.answer()
    
@dp.message(AdminEditState.waiting_for_link)
async def process_link_edit(message: types.Message, state: FSMContext):
    data = await state.get_data()
    key = data.get('setting_key', 'affiliate_url')
    new_val = message.text.strip()
    
    try:
        supabase.table("bot_settings").upsert(
            {"setting_key": key, "setting_value": new_val}, 
            on_conflict="setting_key"
        ).execute()
        await message.answer(f"✅ Link updated successfully for this bot!\n\n`{new_val}`", parse_mode="Markdown")
    except Exception as e:
        print(f"Error updating link: {e}")
        await message.answer(f"⚠️ Error updating database: {e}")
    await state.clear()

# Reviews (Tracked per bot)
@dp.callback_query(F.data == "add_review_img")
async def add_rev_img(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminEditState.waiting_for_review_image)
    await callback.message.answer("📸 Please send a **photo or video** to add as a review for this bot:")
    await callback.answer()
    
@dp.message(AdminEditState.waiting_for_review_image, F.photo | F.video)
async def process_rev_img(message: types.Message, state: FSMContext, bot: Bot):
    b_info = await bot.get_me()
    b_user = b_info.username or "bot"
    
    if message.photo:
        file_id = message.photo[-1].file_id
        mtype = 'photo'
    else:
        file_id = message.video.file_id
        mtype = 'video'
        
    try:
        supabase.table("review_images").insert({
            "file_id": file_id, 
            "media_type": mtype,
            "bot_username": b_user
        }).execute()
    except Exception:
        # Fallback if bot_username column not yet on review_images
        supabase.table("review_images").insert({
            "file_id": file_id, 
            "media_type": mtype
        }).execute()

    await message.answer("✅ Review media added successfully to this bot!")
    await state.clear()
    
@dp.callback_query(F.data == "clear_reviews")
async def clear_revs(callback: types.CallbackQuery, bot: Bot):
    b_info = await bot.get_me()
    b_user = b_info.username or "bot"
    try:
        supabase.table("review_images").delete().eq("bot_username", b_user).execute()
    except Exception:
        supabase.table("review_images").delete().neq("file_id", "none").execute()

    await callback.message.answer("🗑️ All review media for this bot have been deleted!")
    await callback.answer()

# Broadcast (Broadcasts specifically to users who started THIS bot)
@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(BroadcastState.waiting_for_message)
    await callback.message.answer("📢 **Broadcast Mode**\n\nPlease send the message you want to blast to users of this bot (or type /cancel to abort):", parse_mode="Markdown")
    await callback.answer()

@dp.message(BroadcastState.waiting_for_message)
async def process_broadcast(message: types.Message, state: FSMContext, bot: Bot):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Broadcast cancelled.")
        return
        
    await message.answer("🚀 Sending broadcast...")
    await state.clear()
    
    b_info = await bot.get_me()
    b_user = b_info.username or "bot"
    
    # Query users who registered with this specific bot (with fallback to all users)
    try:
        users_res = supabase.table("users").select("telegram_id").eq("bot_username", b_user).execute()
        users = users_res.data
        if not users:
            users_res = supabase.table("users").select("telegram_id").execute()
            users = users_res.data
    except Exception:
        users_res = supabase.table("users").select("telegram_id").execute()
        users = users_res.data
    
    success = 0
    failed = 0
    
    for u in users:
        try:
            await bot.send_message(chat_id=u["telegram_id"], text=message.text)
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
            
    try:
        supabase.table("broadcasts").insert({
            "message": message.text,
            "status": "completed",
            "total_users": len(users),
            "successful": success,
            "failed": failed
        }).execute()
    except Exception:
        pass
    
    await message.answer(f"✅ **Broadcast Complete!**\n\nSuccessful: {success}\nFailed: {failed}", parse_mode="Markdown")

# ================= RUNNER =================
async def main():
    print(f"Starting {len(bots)} bot(s)...")
    for b in bots:
        try:
            await b.delete_webhook(drop_pending_updates=True)
        except Exception as e:
            print(f"Webhook notice: {e}")
    # Poll all bots simultaneously
    await dp.start_polling(*bots)

if __name__ == "__main__":
    asyncio.run(main())
