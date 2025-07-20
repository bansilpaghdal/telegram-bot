import os
import logging
import asyncio
import tempfile
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters
import hashlib
import time
from urllib.parse import quote
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import io

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration from environment variables
BOT_TOKEN = os.environ.get('BOT_TOKEN', '').strip()
MAX_FILE_SIZE = 2000 * 1024 * 1024  # 2000MB limit (Google Drive can handle more)

# Google Drive configuration
GDRIVE_FOLDER_ID = os.environ.get('GDRIVE_FOLDER_ID', '')  # Optional: specific folder
GOOGLE_CREDENTIALS = os.environ.get('GOOGLE_CREDENTIALS', '')  # Service account JSON

# Validate bot token
if BOT_TOKEN and not BOT_TOKEN.replace(':', '').replace('-', '').replace('_', '').isalnum():
    logger.error(f"Invalid BOT_TOKEN format. Token contains invalid characters.")
    BOT_TOKEN = None

class GoogleDriveManager:
    def __init__(self):
        self.service = None
        self.setup_drive_service()
    
    def setup_drive_service(self):
        """Initialize Google Drive service"""
        try:
            if not GOOGLE_CREDENTIALS:
                logger.error("GOOGLE_CREDENTIALS not found in environment variables")
                return
            
            # Parse credentials from environment variable
            credentials_info = json.loads(GOOGLE_CREDENTIALS)
            credentials = service_account.Credentials.from_service_account_info(
                credentials_info,
                scopes=['https://www.googleapis.com/auth/drive.file']
            )
            
            self.service = build('drive', 'v3', credentials=credentials)
            logger.info("Google Drive service initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize Google Drive service: {e}")
    
    async def upload_file(self, file_path, filename, mime_type='application/octet-stream'):
        """Upload file to Google Drive"""
        try:
            if not self.service:
                logger.error("Google Drive service not available")
                return None
            
            # File metadata
            file_metadata = {
                'name': filename,
                'parents': [GDRIVE_FOLDER_ID] if GDRIVE_FOLDER_ID else None
            }
            
            # Remove None values
            file_metadata = {k: v for k, v in file_metadata.items() if v is not None}
            
            # Upload file
            media = MediaFileUpload(file_path, mimetype=mime_type, resumable=True)
            
            # Execute upload in a thread to avoid blocking
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, 
                lambda: self.service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id,name,webViewLink,webContentLink'
                ).execute()
            )
            
            # Make file publicly accessible
            await loop.run_in_executor(
                None,
                lambda: self.service.permissions().create(
                    fileId=result['id'],
                    body={'role': 'reader', 'type': 'anyone'}
                ).execute()
            )
            
            # Generate direct download link
            file_id = result['id']
            direct_link = f"https://drive.google.com/uc?export=download&id={file_id}"
            
            return {
                'file_id': file_id,
                'name': result['name'],
                'view_link': result['webViewLink'],
                'direct_link': direct_link
            }
            
        except Exception as e:
            logger.error(f"Error uploading to Google Drive: {e}")
            return None
    
    def get_mime_type(self, filename):
        """Get MIME type from filename"""
        import mimetypes
        mime_type, _ = mimetypes.guess_type(filename)
        return mime_type or 'application/octet-stream'

class TelegramGDriveBot:
    def __init__(self):
        self.app = Application.builder().token(BOT_TOKEN).build()
        self.drive_manager = GoogleDriveManager()
        self.setup_handlers()
    
    def setup_handlers(self):
        """Setup bot command and message handlers"""
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("help", self.help_command))
        self.app.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
        self.app.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        self.app.add_handler(MessageHandler(filters.VIDEO, self.handle_video))
        self.app.add_handler(MessageHandler(filters.AUDIO, self.handle_audio))
        self.app.add_handler(MessageHandler(filters.VOICE, self.handle_voice))
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        welcome_message = (
            "🚀 Welcome to Google Drive Upload Bot!\n\n"
            "📤 Forward any file to me and I'll:\n"
            "• Upload to Google Drive\n"
            "• Provide direct download link\n"
            "• No storage limitations!\n"
            "• Keep files safe in cloud\n\n"
            "Supported files: Documents, Photos, Videos, Audio, Voice messages\n"
            f"Max file size: {MAX_FILE_SIZE // (1024*1024)}MB"
        )
        await update.message.reply_text(welcome_message)
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_message = (
            "📋 How to use this bot:\n\n"
            "1. Forward any file to this bot\n"
            "2. Bot uploads to Google Drive\n"
            "3. Get direct download link\n\n"
            "Features:\n"
            "• Unlimited cloud storage\n"
            "• Fast Google Drive downloads\n"
            "• Public sharing links\n"
            "• File backup in cloud\n\n"
            "Commands:\n"
            "/start - Start the bot\n"
            "/help - Show this help message"
        )
        await update.message.reply_text(help_message)
    
    async def download_and_upload(self, file_obj, original_filename):
        """Download from Telegram and upload to Google Drive"""
        temp_file = None
        try:
            # Generate unique filename
            timestamp = str(int(time.time()))
            file_hash = hashlib.md5(original_filename.encode()).hexdigest()[:8]
            safe_filename = f"{timestamp}_{file_hash}_{original_filename}"
            
            # Create temporary file
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f"_{safe_filename}")
            temp_path = temp_file.name
            temp_file.close()
            
            # Get Telegram file and download
            telegram_file = await file_obj.get_file()
            await telegram_file.download_to_drive(temp_path)
            
            # Get MIME type
            mime_type = self.drive_manager.get_mime_type(original_filename)
            
            # Upload to Google Drive
            drive_result = await self.drive_manager.upload_file(
                temp_path, 
                original_filename,  # Use original filename for Drive
                mime_type
            )
            
            return drive_result
            
        except Exception as e:
            logger.error(f"Error in download_and_upload: {e}")
            return None
        finally:
            # Clean up temporary file
            if temp_file and os.path.exists(temp_file.name):
                try:
                    os.unlink(temp_file.name)
                except:
                    pass
    
    def format_file_size(self, size_bytes):
        """Format file size in human readable format"""
        if not size_bytes:
            return "Unknown size"
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024**2:
            return f"{size_bytes/1024:.1f} KB"
        elif size_bytes < 1024**3:
            return f"{size_bytes/(1024**2):.1f} MB"
        else:
            return f"{size_bytes/(1024**3):.1f} GB"
    
    async def process_file(self, update: Update, file_obj, original_filename, file_size):
        """Process any file type"""
        try:
            # Check if Google Drive is available
            if not self.drive_manager.service:
                await update.message.reply_text(
                    "❌ Google Drive not configured. Please contact administrator."
                )
                return
            
            # Check file size
            if file_size and file_size > MAX_FILE_SIZE:
                await update.message.reply_text(
                    f"❌ File too large! Maximum size: {MAX_FILE_SIZE // (1024*1024)}MB"
                )
                return
            
            # Send processing message
            processing_msg = await update.message.reply_text(
                "⏳ Downloading and uploading to Google Drive..."
            )
            
            # Download and upload to Google Drive
            drive_result = await self.download_and_upload(file_obj, original_filename)
            
            if not drive_result:
                await processing_msg.edit_text(
                    "❌ Failed to upload file to Google Drive. Please try again."
                )
                return
            
            # Create response message with multiple link options
            size_str = self.format_file_size(file_size) if file_size else "Unknown size"
            response_message = (
                f"✅ File uploaded to Google Drive!\n\n"
                f"📄 **File:** `{original_filename}`\n"
                f"📊 **Size:** {size_str}\n"
                f"☁️ **Cloud Storage:** Google Drive\n\n"
                f"🔗 **Direct Download:** [Click Here]({drive_result['direct_link']})\n"
                f"👀 **View in Drive:** [Open in Browser]({drive_result['view_link']})\n\n"
                f"💡 *Files are stored permanently in Google Drive*"
            )
            
            await processing_msg.edit_text(
                response_message, 
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
            
        except Exception as e:
            logger.error(f"Error processing file: {e}")
            await update.message.reply_text(
                "❌ An error occurred while processing your file."
            )
    
    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle document files"""
        document = update.message.document
        await self.process_file(
            update, 
            document, 
            document.file_name or "document", 
            document.file_size
        )
    
    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle photo files"""
        photo = update.message.photo[-1]  # Get highest resolution
        await self.process_file(
            update, 
            photo, 
            f"photo_{photo.file_id}.jpg", 
            photo.file_size
        )
    
    async def handle_video(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle video files"""
        video = update.message.video
        filename = video.file_name or f"video_{video.file_id}.mp4"
        await self.process_file(update, video, filename, video.file_size)
    
    async def handle_audio(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle audio files"""
        audio = update.message.audio
        filename = audio.file_name or f"audio_{audio.file_id}.mp3"
        await self.process_file(update, audio, filename, audio.file_size)
    
    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle voice messages"""
        voice = update.message.voice
        filename = f"voice_{voice.file_id}.ogg"
        await self.process_file(update, voice, filename, voice.file_size)
    
    def run(self):
        """Start the bot"""
        logger.info("Starting Telegram Google Drive Bot...")
        self.app.run_polling()

    def test_credentials():
    try:
        import json
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        
        credentials_info = json.loads(GOOGLE_CREDENTIALS)
        credentials = service_account.Credentials.from_service_account_info(
            credentials_info,
            scopes=['https://www.googleapis.com/auth/drive.file']
        )
        service = build('drive', 'v3', credentials=credentials)
        
        # Test API call
        results = service.files().list(pageSize=1).execute()
        print("✅ Google Drive API connection successful!")
        return True
    except Exception as e:
        print(f"❌ Google Drive API test failed: {e}")
        return False

# Call before starting bot
test_credentials()

# Main execution
if __name__ == "__main__":
    # Debug token
    print("=== TOKEN DEBUG ===")
    token_raw = os.environ.get('BOT_TOKEN', '')
    print(f"Token exists: {'BOT_TOKEN' in os.environ}")
    print(f"Token length: {len(token_raw)}")
    print(f"Token preview: {token_raw[:15] if token_raw else 'EMPTY'}...")
    print(f"GDrive credentials: {'GOOGLE_CREDENTIALS' in os.environ}")
    print("==================")
    
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable not set or invalid!")
        exit(1)
    
    if not GOOGLE_CREDENTIALS:
        logger.error("GOOGLE_CREDENTIALS not set! Please configure Google Drive API.")
        exit(1)
    
    # Create and run bot
    bot = TelegramGDriveBot()
    
    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    finally:
        logger.info("Bot shutdown complete")
