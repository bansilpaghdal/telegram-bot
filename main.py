import os
import logging
import asyncio
import tempfile
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters
import hashlib
import time
import json
import pickle
import base64
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.environ.get('BOT_TOKEN', '').strip()
MAX_FILE_SIZE = 2000 * 1024 * 1024  # 2000MB

# OAuth2 configuration (from Google Cloud Console)
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
GOOGLE_REFRESH_TOKEN = os.environ.get('GOOGLE_REFRESH_TOKEN', '')  # We'll generate this
GDRIVE_FOLDER_ID = os.environ.get('GDRIVE_FOLDER_ID', '')

# OAuth2 scopes
SCOPES = ['https://www.googleapis.com/auth/drive.file']

class GoogleDriveOAuth2Manager:
    def __init__(self):
        self.service = None
        self.setup_drive_service()
    
    def setup_drive_service(self):
        """Initialize Google Drive service using OAuth2"""
        try:
            if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
                logger.error("❌ OAuth2 credentials not found (CLIENT_ID/CLIENT_SECRET)")
                return False
            
            creds = None
            
            # Check if we have a refresh token
            if GOOGLE_REFRESH_TOKEN:
                try:
                    # Create credentials from refresh token
                    creds = Credentials(
                        token=None,
                        refresh_token=GOOGLE_REFRESH_TOKEN,
                        id_token=None,
                        token_uri='https://oauth2.googleapis.com/token',
                        client_id=GOOGLE_CLIENT_ID,
                        client_secret=GOOGLE_CLIENT_SECRET
                    )
                    
                    # Refresh the token
                    creds.refresh(Request())
                    logger.info("✅ OAuth2 credentials refreshed successfully")
                    
                except Exception as e:
                    logger.error(f"❌ Failed to refresh OAuth2 token: {e}")
                    return False
            else:
                logger.error("❌ GOOGLE_REFRESH_TOKEN not found")
                logger.error("Please run the setup script to generate refresh token")
                return False
            
            # Build service
            self.service = build('drive', 'v3', credentials=creds)
            
            # Test connection
            self.service.files().list(pageSize=1).execute()
            logger.info("✅ Google Drive OAuth2 service initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize Google Drive OAuth2 service: {e}")
            return False
    
    async def upload_file(self, file_path, filename, mime_type='application/octet-stream'):
        """Upload file to Google Drive using OAuth2"""
        try:
            if not self.service:
                logger.error("❌ Google Drive service not available")
                return None
            
            logger.info(f"📤 Starting upload: {filename}")
            
            # File metadata
            file_metadata = {'name': filename}
            
            # Add folder if specified
            if GDRIVE_FOLDER_ID:
                file_metadata['parents'] = [GDRIVE_FOLDER_ID]
                logger.info(f"📁 Uploading to folder: {GDRIVE_FOLDER_ID}")
            
            # Upload file
            media = MediaFileUpload(file_path, mimetype=mime_type, resumable=True)
            
            # Execute upload in thread
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, 
                lambda: self.service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id,name,webViewLink,webContentLink'
                ).execute()
            )
            
            file_id = result['id']
            logger.info(f"✅ File uploaded successfully. ID: {file_id}")
            
            # Make file publicly accessible
            try:
                await loop.run_in_executor(
                    None,
                    lambda: self.service.permissions().create(
                        fileId=file_id,
                        body={'role': 'reader', 'type': 'anyone'}
                    ).execute()
                )
                logger.info("🌐 File made publicly accessible")
            except Exception as e:
                logger.warning(f"⚠️ Could not make file public: {e}")
            
            # Generate links
            direct_link = f"https://drive.google.com/uc?export=download&id={file_id}"
            
            return {
                'file_id': file_id,
                'name': result['name'],
                'view_link': result.get('webViewLink', ''),
                'direct_link': direct_link
            }
            
        except Exception as e:
            logger.error(f"❌ Error uploading to Google Drive: {e}")
            return None
    
    def get_mime_type(self, filename):
        """Get MIME type from filename"""
        import mimetypes
        mime_type, _ = mimetypes.guess_type(filename)
        return mime_type or 'application/octet-stream'

class TelegramGDriveBot:
    def __init__(self):
        self.app = Application.builder().token(BOT_TOKEN).build()
        self.drive_manager = GoogleDriveOAuth2Manager()
        self.setup_handlers()
    
    def setup_handlers(self):
        """Setup bot command and message handlers"""
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("help", self.help_command))
        self.app.add_handler(CommandHandler("status", self.status_command))
        self.app.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
        self.app.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        self.app.add_handler(MessageHandler(filters.VIDEO, self.handle_video))
        self.app.add_handler(MessageHandler(filters.AUDIO, self.handle_audio))
        self.app.add_handler(MessageHandler(filters.VOICE, self.handle_voice))
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        drive_status = "✅ Connected" if self.drive_manager.service else "❌ Not configured"
        
        welcome_message = (
            "🚀 **Google Drive Upload Bot (OAuth2)**\n\n"
            f"📤 **Status**: {drive_status}\n"
            f"💾 **Max file size**: {MAX_FILE_SIZE // (1024*1024)}MB\n\n"
            "**How to use:**\n"
            "• Send any file to upload to YOUR Google Drive\n"
            "• Uses your personal Drive storage quota\n"
            "• Get instant download links\n\n"
            "**Commands:**\n"
            "/help - Show help\n"
            "/status - Check connection status"
        )
        await update.message.reply_text(welcome_message, parse_mode='Markdown')
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_message = (
            "📋 **How to use this bot:**\n\n"
            "1️⃣ Send any file to this bot\n"
            "2️⃣ Bot uploads to YOUR Google Drive\n"
            "3️⃣ Get direct download link\n\n"
            "**Key Features:**\n"
            "• Uses your personal Google Drive storage\n"
            "• No service account limitations\n"
            "• All standard Google Drive features\n"
            "• Files stored in your account\n\n"
            "**Supported files:**\n"
            "• Documents, Photos, Videos\n"
            "• Audio files, Voice messages\n"
            "• Any file type up to 2GB"
        )
        await update.message.reply_text(help_message, parse_mode='Markdown')
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command"""
        drive_status = "✅ Connected (OAuth2)" if self.drive_manager.service else "❌ Not configured"
        folder_status = "✅ Set" if GDRIVE_FOLDER_ID else "❌ Using root folder"
        
        status_message = (
            f"🤖 **Bot Status Report**\n\n"
            f"🔗 **Google Drive**: {drive_status}\n"
            f"📁 **Target Folder**: {folder_status}\n"
            f"💾 **Max File Size**: {MAX_FILE_SIZE // (1024*1024)}MB\n\n"
            f"**Authentication**: OAuth2 (Personal Account)\n"
            f"**Storage**: Your Google Drive quota\n"
            f"**Folder ID**: `{GDRIVE_FOLDER_ID or 'Root folder'}`"
        )
        
        await update.message.reply_text(status_message, parse_mode='Markdown')
    
    async def process_file(self, update: Update, file_obj, original_filename, file_size):
        """Process any file type"""
        try:
            # Check if Google Drive is available
            if not self.drive_manager.service:
                await update.message.reply_text(
                    "❌ **Google Drive not configured!**\n\n"
                    "OAuth2 authentication required.\n"
                    "Please contact administrator to set up:\n"
                    "• GOOGLE_CLIENT_ID\n"
                    "• GOOGLE_CLIENT_SECRET\n"
                    "• GOOGLE_REFRESH_TOKEN\n\n"
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
                f"⏳ **Uploading to Google Drive...**\n"
                f"📄 File: `{original_filename}`\n"
                f"📊 Size: {self.format_file_size(file_size) if file_size else 'Unknown'}",
                parse_mode='Markdown'
            )
            
            # Download and upload
            drive_result = await self.download_and_upload(file_obj, original_filename)
            
            if not drive_result:
                await processing_msg.edit_text(
                    "❌ **Upload Failed!**\n\n"
                    "Please try again. If the problem persists:\n"
                    "• Check your Google Drive storage quota\n"
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
                f"🆔 **File ID:** `{drive_result['file_id']}`\n\n"
                f"🔗 [**Direct Download**]({drive_result['direct_link']})\n"
                f"👀 [**View in Drive**]({drive_result['view_link']})\n\n"
                f"💡 *File stored in your personal Google Drive*"
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
        """Download from Telegram and upload to Google Drive"""
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
            mime_type = self.drive_manager.get_mime_type(original_filename)
            logger.info(f"📋 File info - Size: {file_size}, MIME: {mime_type}")
            
            # Upload to Google Drive
            drive_result = await self.drive_manager.upload_file(
                temp_path, 
                original_filename,
                mime_type
            )
            
            return drive_result
            
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
        logger.info("🚀 Starting Telegram Google Drive Bot (OAuth2)...")
        self.app.run_polling()

def test_oauth2_connection():
    """Test OAuth2 connection to Google Drive"""
    try:
        if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET or not GOOGLE_REFRESH_TOKEN:
            print("❌ Missing OAuth2 credentials")
            return False
        
        # Create credentials
        creds = Credentials(
            token=None,
            refresh_token=GOOGLE_REFRESH_TOKEN,
            id_token=None,
            token_uri='https://oauth2.googleapis.com/token',
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET
        )
        
        # Refresh token
        creds.refresh(Request())
        
        # Test API
        service = build('drive', 'v3', credentials=creds)
        results = service.files().list(pageSize=1).execute()
        
        print("✅ OAuth2 Google Drive connection successful!")
        print(f"Account has access to {len(results.get('files', []))} files (showing 1)")
        return True
        
    except Exception as e:
        print(f"❌ OAuth2 connection failed: {e}")
        return False

if __name__ == "__main__":
    print("🔍 Validating OAuth2 configuration...")
    
    # Check environment variables
    print(f"BOT_TOKEN: {'✅' if BOT_TOKEN else '❌'}")
    print(f"GOOGLE_CLIENT_ID: {'✅' if GOOGLE_CLIENT_ID else '❌'}")
    print(f"GOOGLE_CLIENT_SECRET: {'✅' if GOOGLE_CLIENT_SECRET else '❌'}")
    print(f"GOOGLE_REFRESH_TOKEN: {'✅' if GOOGLE_REFRESH_TOKEN else '❌'}")
    print(f"GDRIVE_FOLDER_ID: {'✅' if GDRIVE_FOLDER_ID else '❌ (optional)'}")
    
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN not set!")
        exit(1)
    
    if not all([GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN]):
        print("❌ OAuth2 credentials incomplete!")
        print("Please set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REFRESH_TOKEN")
        exit(1)
    
    # Test connection
    if not test_oauth2_connection():
        exit(1)
    
    print("✅ All validations passed!")
    
    # Create and run bot
    try:
        bot = TelegramGDriveBot()
        bot.run()
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Bot crashed: {e}")
    finally:
        logger.info("🔚 Bot shutdown complete")
