import asyncio
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo
from supabase import create_client, Client
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

bot = Bot(token=BOT_TOKEN)
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

# ================= USER FLOW =================
@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    telegram_id = str(message.from_user.id)
    username = message.from_user.username
    first_name = message.from_user.first_name
    last_name = message.from_user.last_name
    
    # 1. Upsert User
    try:
        user_res = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute()
        if not user_res.data:
            supabase.table("users").insert({
                "telegram_id": telegram_id,
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "source": "organic",
                "status": "active"
            }).execute()
        else:
            supabase.table("users").update({"status": "active"}).eq("telegram_id", telegram_id).execute()
    except Exception as e:
        print(f"Error upserting user: {e}")

    # 2. Sequential Messages
    await message.answer("👋 Welcome to Gautam Trading!\n\nWe're excited to have you here.")
    await asyncio.sleep(1)
    
    # 3. Reviews (Images & Videos)
    try:
        reviews_res = supabase.table("review_images").select("*").execute()
        if reviews_res.data:
            media_group = []
            for r in reviews_res.data[:10]:
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
            pub_link = settings_dict.get("public_channel_link", pub_link)
            aff_link = settings_dict.get("affiliate_url", aff_link)
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
    
    # Notify Admins
    admin_ids = os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")
    for aid in admin_ids:
        if not aid.strip(): continue
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Approve", callback_data=f"approve_{telegram_id}")],
            [InlineKeyboardButton(text="❌ Reject", callback_data=f"reject_{telegram_id}")]
        ])
        try:
            await bot.send_message(
                chat_id=aid.strip(), 
                text=f"🔔 *New Registration!*\n\nUser: {message.from_user.full_name} (@{message.from_user.username or 'none'})\nQuotex ID: `{quotex_id}`",
                parse_mode="Markdown",
                reply_markup=markup
            )
        except Exception as e:
            print("Failed to notify admin:", e)

# ================= ADMIN REGISTRATION CONTROLS =================
@dp.callback_query(F.data.startswith("approve_"))
async def admin_approve(callback: types.CallbackQuery):
    if str(callback.from_user.id) not in os.getenv("ADMIN_TELEGRAM_IDS", "").split(","): return
    user_id = callback.data.split("_")[1]
    
    supabase.table("users").update({"approval_status": "approved"}).eq("telegram_id", user_id).execute()
    
    # Fetch VIP Channel link
    settings_res = supabase.table("bot_settings").select("setting_value").eq("setting_key", "vip_channel_link").execute()
    vip_link = "No VIP link set by admin."
    if settings_res.data:
        vip_link = settings_res.data[0].get("setting_value", vip_link)
        
    try:
        await callback.message.edit_text(callback.message.text + "\n\n✅ **APPROVED**", parse_mode="Markdown")
        await bot.send_message(user_id, f"🎉 Approved! Join the VIP Channel here: {vip_link}")
    except: pass
    await callback.answer("User Approved")

@dp.callback_query(F.data.startswith("reject_"))
async def admin_reject(callback: types.CallbackQuery):
    if str(callback.from_user.id) not in os.getenv("ADMIN_TELEGRAM_IDS", "").split(","): return
    user_id = callback.data.split("_")[1]
    
    supabase.table("users").update({"approval_status": "rejected"}).eq("telegram_id", user_id).execute()
    
    try:
        await callback.message.edit_text(callback.message.text + "\n\n❌ **REJECTED**", parse_mode="Markdown")
        await bot.send_message(user_id, "❌ Rejected! Please make sure you deposited at least $50 and try again.")
    except: pass
    await callback.answer("User Rejected")

# ================= ADMIN DASHBOARD =================
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if str(message.from_user.id) not in os.getenv("ADMIN_TELEGRAM_IDS", "").split(","):
        await message.answer("❌ This command is not accessible. You do not have admin permissions.")
        return
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Broadcast Message", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🔗 Edit Affiliate Link", callback_data="edit_affiliate")],
        [InlineKeyboardButton(text="📢 Edit Public Channel Link", callback_data="edit_public")],
        [InlineKeyboardButton(text="👑 Edit VIP Channel Link", callback_data="edit_vip")],
        [InlineKeyboardButton(text="📸 Add Review Image", callback_data="add_review_img")],
        [InlineKeyboardButton(text="🗑️ Clear All Reviews", callback_data="clear_reviews")]
    ])
    await message.answer("🛠 **Gautam Trading Admin Dashboard**", reply_markup=markup, parse_mode="Markdown")

# Edit Links
@dp.callback_query(F.data == "edit_affiliate")
async def edit_aff(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminEditState.waiting_for_link)
    await state.update_data(setting_key="affiliate_url")
    await callback.message.answer("🔗 Please send the new **Quotex Affiliate Link**:")
    await callback.answer()

@dp.callback_query(F.data == "edit_public")
async def edit_pub(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminEditState.waiting_for_link)
    await state.update_data(setting_key="public_channel_link")
    await callback.message.answer("📢 Please send the new **Public Channel Link**:")
    await callback.answer()
    
@dp.callback_query(F.data == "edit_vip")
async def edit_vip(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminEditState.waiting_for_link)
    await state.update_data(setting_key="vip_channel_link")
    await callback.message.answer("👑 Please send the new **VIP Channel Link**:")
    await callback.answer()
    
@dp.message(AdminEditState.waiting_for_link)
async def process_link_edit(message: types.Message, state: FSMContext):
    data = await state.get_data()
    key = data['setting_key']
    
    supabase.table("bot_settings").upsert({"setting_key": key, "setting_value": message.text.strip()}).execute()
    
    await message.answer(f"✅ Link updated successfully!")
    await state.clear()

# Reviews
@dp.callback_query(F.data == "add_review_img")
async def add_rev_img(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminEditState.waiting_for_review_image)
    await callback.message.answer("📸 Please send a **photo or video** to add as a review:")
    await callback.answer()
    
@dp.message(AdminEditState.waiting_for_review_image, F.photo | F.video)
async def process_rev_img(message: types.Message, state: FSMContext):
    if message.photo:
        file_id = message.photo[-1].file_id
        mtype = 'photo'
    else:
        file_id = message.video.file_id
        mtype = 'video'
        
    supabase.table("review_images").insert({"file_id": file_id, "media_type": mtype}).execute()
    await message.answer("✅ Review media added successfully!")
    await state.clear()
    
@dp.callback_query(F.data == "clear_reviews")
async def clear_revs(callback: types.CallbackQuery):
    # PostgREST needs a filter to delete, we delete everything not equal to 'none'
    supabase.table("review_images").delete().neq("file_id", "none").execute()
    await callback.message.answer("🗑️ All review images have been deleted!")
    await callback.answer()

# Broadcast
@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(BroadcastState.waiting_for_message)
    await callback.message.answer("📢 **Broadcast Mode**\n\nPlease send the message you want to blast to all users. (Or type /cancel to abort)", parse_mode="Markdown")
    await callback.answer()

@dp.message(BroadcastState.waiting_for_message)
async def process_broadcast(message: types.Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Broadcast cancelled.")
        return
        
    await message.answer("🚀 Sending broadcast...")
    await state.clear()
    
    users_res = supabase.table("users").select("telegram_id").execute()
    users = users_res.data
    
    success = 0
    failed = 0
    
    for u in users:
        try:
            # We can broadcast text, we should ideally handle photo/video too but for now text
            await bot.send_message(chat_id=u["telegram_id"], text=message.text)
            success += 1
            await asyncio.sleep(0.05) # Prevent spam limits
        except Exception:
            failed += 1
            
    supabase.table("broadcasts").insert({
        "message": message.text,
        "status": "completed",
        "total_users": len(users),
        "successful": success,
        "failed": failed
    }).execute()
    
    await message.answer(f"✅ **Broadcast Complete!**\n\nSuccessful: {success}\nFailed: {failed}", parse_mode="Markdown")

# ================= RUNNER =================
async def main():
    print("Starting bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
