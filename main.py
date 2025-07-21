import os
import logging
import asyncio
import tempfile
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters
import hashlib
import time
from mega import Mega
import threading
from functools import partial

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.environ.get('BOT_TOKEN', '').strip()
MAX_FILE_SIZE = 2000 * 1024 * 1024  # 2000MB

# Mega.nz configuration
MEGA_EMAIL = os.environ.get('MEGA_EMAIL', '').strip()
MEGA_PASSWORD = os.environ.get('MEGA_PASSWORD', '').strip()
MEGA_FOLDER_NAME = os.environ.get('MEGA_FOLDER_NAME', 'TelegramUploads').strip()  # Optional folder name

class MegaNzManager:
    def __init__(self):
        self.mega = None
        self.folder_handle = None
        self.is_authenticated = False
        self.setup_mega_service()
    
    def setup_mega_service(self):
        """Initialize Mega.nz service"""
        try:
            if not MEGA_EMAIL or not MEGA_PASSWORD:
                logger.error("❌ Mega.nz credentials not found (EMAIL/PASSWORD)")
                return False
            
            logger.info("🔐 Authenticating with Mega.nz...")
            self.mega = Mega()
            
            # Login to Mega
            try:
                self.mega = self.mega.login(MEGA_EMAIL, MEGA_PASSWORD)
                logger.info("✅ Mega.nz authentication successful")
                self.is_authenticated = True
            except Exception as e:
                logger.error(f"❌ Mega.nz login failed: {e}")
                return False
            
            # Setup folder
            self.setup_upload_folder()
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize Mega.nz service: {e}")
            return False
    
    def setup_upload_folder(self):
        """Create or find upload folder"""
        try:
            if not MEGA_FOLDER_NAME:
                logger.info("📁 Using root folder for uploads")
                return
            
            # Get all folders
            folders = self.mega.get_files()
            
            # Look for existing folder
            for file_handle, file_info in folders.items():
                if (file_info['t'] == 1 and  # t=1 means folder
                    file_info['a'] and 
                    file_info['a'].get('n') == MEGA_FOLDER_NAME):
                    self.folder_handle = file_handle
                    logger.info(f"📁 Found existing folder: {MEGA_FOLDER_NAME}")
                    return
            
            # Create new folder if not found
            self.folder_handle = self.mega.create_folder(MEGA_FOLDER_NAME)
            logger.info(f"📁 Created new folder: {MEGA_FOLDER_NAME}")
            
        except Exception as e:
            logger.warning(f"⚠️ Could not setup folder: {e}")
            logger.info("📁 Will use root folder for uploads")
    
    def upload_file(self, file_path, filename):
        """Upload file to Mega.nz (synchronous)"""
        try:
            if not self.is_authenticated:
                logger.error("❌ Mega.nz not authenticated")
                return None
            
            logger.info(f"📤 Starting upload: {filename}")
            
            # Upload file
            if self.folder_handle:
                # Upload to specific folder
                uploaded_file = self.mega.upload(file_path, self.folder_handle, filename)
                logger.info(f"✅ File uploaded to folder: {MEGA_FOLDER_NAME}")
            else:
                # Upload to root
                uploaded_file = self.mega.upload(file_path, dest_filename=filename)
                logger.info("✅ File uploaded to root folder")
            
            if not uploaded_file:
                logger.error("❌ Upload returned None")
                return None
            
            # Get download link
            download_link = self.mega.get_upload_link(uploaded_file)
            
            # Get file info
            files = self.mega.get_files()
            file_info = files.get(uploaded_file)
            
            file_size = file_info.get('s', 0) if file_info else 0
            
            return {
                'file_handle': uploaded_file,
                'filename': filename,
                'download_link': download_link,
                'file_size': file_size,
                'folder': MEGA_FOLDER_NAME if self.folder_handle else 'Root'
            }
            
        except Exception as e:
            logger.error(f"❌ Error uploading to Mega.nz: {e}")
            return None
    
    async def upload_file_async(self, file_path, filename):
        """Async wrapper for file upload"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.upload_file, file_path, filename)
    
    def get_account_info(self):
        """Get account storage info"""
        try:
            if not self.is_authenticated:
                return None
            
            quota = self.mega.get_quota()
            return {
                'total': quota,
                'used': self.mega.get_storage_space(kilo=True)['used'],
                'available': quota - self.mega.get_storage_space(kilo=True)['used']
            }
        except Exception as e:
            logger.error(f"❌ Error getting account info: {e}")
            return None

class TelegramMegaBot:
    def __init__(self):
        self.app = Application.builder().token(BOT_TOKEN).build()
        self.mega_manager = MegaNzManager()
        self.setup_handlers()
    
    def setup_handlers(self):
        """Setup bot command and message handlers"""
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("help", self.help_command))
        self.app.add_handler(CommandHandler("status", self.status_command))
        self.app.add_handler(CommandHandler("quota", self.quota_command))
        self.app.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
        self.app.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        self.app.add_handler(MessageHandler(filters.VIDEO, self.handle_video))
        self.app.add_handler(MessageHandler(filters.AUDIO, self.handle_audio))
        self.app.add_handler(MessageHandler(filters.VOICE, self.handle_voice))
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        mega_status = "✅ Connected" if self.mega_manager.is_authenticated else "❌ Not configured"
        
        welcome_message = (
            "🚀 **Mega.nz Upload Bot**\n\n"
            f"📤 **Status**: {mega_status}\n"
            f"💾 **Max file size**: {MAX_FILE_SIZE // (1024*1024)}MB\n"
            f"📁 **Upload folder**: {MEGA_FOLDER_NAME or 'Root'}\n\n"
            "**How to use:**\n"
            "• Send any file to upload to Mega.nz\n"
            "• Get instant download links\n"
            "• Files stored in your Mega account\n\n"
            "**Commands:**\n"
            "/help - Show help\n"
            "/status - Check connection status\n"
            "/quota - Check storage quota"
        )
        await update.message.reply_text(welcome_message, parse_mode='Markdown')
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_message = (
            "📋 **How to use this bot:**\n\n"
            "1️⃣ Send any file to this bot\n"
            "2️⃣ Bot uploads to Mega.nz\n"
            "3️⃣ Get direct download link\n\n"
            "**Key Features:**\n"
            "• Uses your personal Mega.nz account\n"
            "• 50GB free storage (with registration)\n"
            "• Fast upload/download speeds\n"
            "• End-to-end encryption\n\n"
            "**Supported files:**\n"
            "• Documents, Photos, Videos\n"
            "• Audio files, Voice messages\n"
            "• Any file type up to 2GB\n\n"
            "**Commands:**\n"
            "/quota - Check your Mega storage quota"
        )
        await update.message.reply_text(help_message, parse_mode='Markdown')
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command"""
        mega_status = "✅ Connected" if self.mega_manager.is_authenticated else "❌ Not configured"
        folder_status = f"📁 {MEGA_FOLDER_NAME}" if MEGA_FOLDER_NAME else "📁 Root folder"
        
        status_message = (
            f"🤖 **Bot Status Report**\n\n"
            f"🔗 **Mega.nz**: {mega_status}\n"
            f"📁 **Upload Folder**: {folder_status}\n"
            f"💾 **Max File Size**: {MAX_FILE_SIZE // (1024*1024)}MB\n"
            f"👤 **Account**: {MEGA_EMAIL[:3]}***@***{MEGA_EMAIL.split('@')[-1] if '@' in MEGA_EMAIL else 'hidden'}\n\n"
            f"**Storage**: Your Mega.nz account quota\n"
            f"**Encryption**: End-to-end encrypted\n\n"
            "Use /quota to check storage usage"
        )
        
        await update.message.reply_text(status_message, parse_mode='Markdown')
    
    async def quota_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /quota command"""
        try:
            if not self.mega_manager.is_authenticated:
                await update.message.reply_text(
                    "❌ **Mega.nz not configured!**\n"
                    "Please contact administrator."
                )
                return
            
            quota_info = self.mega_manager.get_account_info()
            
            if not quota_info:
                await update.message.reply_text(
                    "❌ **Could not retrieve quota information**\n"
                    "Please try again later."
                )
                return
            
            total_gb = quota_info['total'] / (1024**3)
            used_gb = quota_info['used'] / (1024**3)
            available_gb = quota_info['available'] / (1024**3)
            usage_percent = (used_gb / total_gb) * 100
            
            quota_message = (
                f"💾 **Mega.nz Storage Quota**\n\n"
                f"📊 **Usage**: {usage_percent:.1f}%\n"
                f"✅ **Used**: {used_gb:.2f} GB\n"
                f"💚 **Available**: {available_gb:.2f} GB\n"
                f"📦 **Total**: {total_gb:.2f} GB\n\n"
                f"{'🔴 Storage almost full!' if usage_percent > 90 else '✅ Storage looking good!'}"
            )
            
            await update.message.reply_text(quota_message, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Error getting quota: {e}")
            await update.message.reply_text(
                "❌ **Error retrieving quota**\n"
                "Please try again later."
            )
    
    async def process_file(self, update: Update, file_obj, original_filename, file_size):
        """Process any file type"""
        try:
            # Check if Mega is available
            if not self.mega_manager.is_authenticated:
                await update.message.reply_text(
                    "❌ **Mega.nz not configured!**\n\n"
                    "Please contact administrator to set up:\n"
                    "• MEGA_EMAIL\n"
                    "• MEGA_PASSWORD\n\n"
                    "Use /status to check current configuration.",
                    parse_mode='Markdown'
                )
                return
            
            # Check file size
            if file_size and file_size > MAX_FILE_SIZE:
                size_mb = file_size // (1024*1024)
                max_mb = MAX_FILE_SIZE // (1024*1024)
                await update.message.reply_text(
                    f"❌ **File too large!**\n"
                    f"File size: {size_mb}MB\n"
                    f"Maximum allowed: {max_mb}MB"
                )
                return
            
            # Send processing message
            processing_msg = await update.message.reply_text(
                f"⏳ **Uploading to Mega.nz...**\n"
                f"📄 File: `{original_filename}`\n"
                f"📊 Size: {self.format_file_size(file_size) if file_size else 'Unknown'}\n"
                f"📁 Destination: {MEGA_FOLDER_NAME or 'Root'}",
                parse_mode='Markdown'
            )
            
            # Download and upload
            mega_result = await self.download_and_upload(file_obj, original_filename)
            
            if not mega_result:
                await processing_msg.edit_text(
                    "❌ **Upload Failed!**\n\n"
                    "Please try again. If the problem persists:\n"
                    "• Check your Mega.nz account status\n"
                    "• Check storage quota with /quota\n"
                    "• Try a smaller file\n"
                    "• Contact administrator"
                )
                return
            
            # Success message
            size_str = self.format_file_size(file_size) if file_size else "Unknown size"
            response_message = (
                f"✅ **Upload Successful!**\n\n"
                f"📄 **File:** `{original_filename}`\n"
                f"📊 **Size:** {size_str}\n"
                f"📁 **Folder:** {mega_result['folder']}\n\n"
                f"🔗 **[Download Link]({mega_result['download_link']})**\n\n"
                f"🔐 *File encrypted and stored on Mega.nz*\n"
                f"💡 *Link is permanent and shareable*"
            )
            
            await processing_msg.edit_text(
                response_message, 
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
            
        except Exception as e:
            logger.error(f"Error processing file: {e}")
            await update.message.reply_text(
                f"❌ **Processing Error**\n\n"
                f"An unexpected error occurred:\n`{str(e)}`\n\n"
                "Please try again or contact administrator."
            )
    
    async def download_and_upload(self, file_obj, original_filename):
        """Download from Telegram and upload to Mega.nz"""
        temp_file = None
        try:
            # Generate safe filename
            timestamp = str(int(time.time()))
            file_hash = hashlib.md5(original_filename.encode()).hexdigest()[:8]
            safe_filename = f"{timestamp}_{file_hash}_{original_filename}"
            
            # Create temporary file
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f"_{safe_filename}")
            temp_path = temp_file.name
            temp_file.close()
            
            # Download from Telegram
            logger.info(f"📥 Downloading from Telegram: {original_filename}")
            telegram_file = await file_obj.get_file()
            await telegram_file.download_to_drive(temp_path)
            
            # Get file info
            file_size = os.path.getsize(temp_path)
            logger.info(f"📋 File info - Size: {file_size} bytes")
            
            # Upload to Mega.nz
            mega_result = await self.mega_manager.upload_file_async(
                temp_path, 
                original_filename
            )
            
            return mega_result
            
        except Exception as e:
            logger.error(f"❌ Error in download_and_upload: {e}")
            return None
        finally:
            # Clean up
            if temp_file and os.path.exists(temp_file.name):
                try:
                    os.unlink(temp_file.name)
                    logger.info(f"🗑️ Cleaned up temporary file")
                except Exception as e:
                    logger.warning(f"⚠️ Could not clean up temp file: {e}")
    
    def format_file_size(self, size_bytes):
        """Format file size in human readable format"""
        if not size_bytes:
            return "Unknown"
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024**2:
            return f"{size_bytes/1024:.1f} KB"
        elif size_bytes < 1024**3:
            return f"{size_bytes/(1024**2):.1f} MB"
        else:
            return f"{size_bytes/(1024**3):.1f} GB"
    
    # File handlers
    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        document = update.message.document
        await self.process_file(update, document, document.file_name or "document", document.file_size)
    
    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        photo = update.message.photo[-1]
        await self.process_file(update, photo, f"photo_{photo.file_id}.jpg", photo.file_size)
    
    async def handle_video(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        video = update.message.video
        filename = video.file_name or f"video_{video.file_id}.mp4"
        await self.process_file(update, video, filename, video.file_size)
    
    async def handle_audio(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        audio = update.message.audio
        filename = audio.file_name or f"audio_{audio.file_id}.mp3"
        await self.process_file(update, audio, filename, audio.file_size)
    
    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        voice = update.message.voice
        filename = f"voice_{voice.file_id}.ogg"
        await self.process_file(update, voice, filename, voice.file_size)
    
    def run(self):
        """Start the bot"""
        logger.info("🚀 Starting Telegram Mega.nz Bot...")
        self.app.run_polling()

def test_mega_connection():
    """Test connection to Mega.nz"""
    try:
        if not MEGA_EMAIL or not MEGA_PASSWORD:
            print("❌ Missing Mega.nz credentials")
            return False
        
        # Test login
        mega = Mega()
        mega_instance = mega.login(MEGA_EMAIL, MEGA_PASSWORD)
        
        # Test getting files
        files = mega_instance.get_files()
        quota = mega_instance.get_quota()
        
        print("✅ Mega.nz connection successful!")
        print(f"Account has {len(files)} files/folders")
        print(f"Storage quota: {quota / (1024**3):.2f} GB")
        return True
        
    except Exception as e:
        print(f"❌ Mega.nz connection failed: {e}")
        return False

if __name__ == "__main__":
    print("🔍 Validating Mega.nz configuration...")
    
    # Check environment variables
    print(f"BOT_TOKEN: {'✅' if BOT_TOKEN else '❌'}")
    print(f"MEGA_EMAIL: {'✅' if MEGA_EMAIL else '❌'}")
    print(f"MEGA_PASSWORD: {'✅' if MEGA_PASSWORD else '❌'}")
    print(f"MEGA_FOLDER_NAME: {'✅' if MEGA_FOLDER_NAME else '❌ (optional - will use root)'}")
    
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN not set!")
        exit(1)
    
    if not MEGA_EMAIL or not MEGA_PASSWORD:
        print("❌ Mega.nz credentials incomplete!")
        print("Please set MEGA_EMAIL and MEGA_PASSWORD")
        exit(1)
    
    # Test connection
    if not test_mega_connection():
        exit(1)
    
    print("✅ All validations passed!")
    
    # Create and run bot
    try:
        bot = TelegramMegaBot()
        bot.run()
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Bot crashed: {e}")
    finally:
        logger.info("🔚 Bot shutdown complete")
