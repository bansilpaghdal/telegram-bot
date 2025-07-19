import os
import logging
import asyncio
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters
import aiohttp
import aiofiles
from urllib.parse import quote
import hashlib
import time

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.environ.get('BOT_TOKEN')
DOWNLOAD_FOLDER = "downloads"
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:8000')
PORT = int(os.environ.get('PORT', 8000))
MAX_FILE_SIZE = 2000 * 1024 * 1024  # 20MB limit for free hosting

class TelegramDownloadBot:
    def __init__(self):
        self.app = Application.builder().token(BOT_TOKEN).build()
        self.setup_handlers()
        self.ensure_download_folder()
    
    def ensure_download_folder(self):
        """Create downloads folder if it doesn't exist"""
        if not os.path.exists(DOWNLOAD_FOLDER):
            os.makedirs(DOWNLOAD_FOLDER)
    
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
            "🚀 Welcome to Fast Download Bot!\n\n"
            "📤 Forward any file to me and I'll provide:\n"
            "• Direct download link\n"
            "• Faster download speeds\n"
            "• File information\n\n"
            "Supported files: Documents, Photos, Videos, Audio, Voice messages\n"
            f"Max file size: {MAX_FILE_SIZE // (1024*1024)}MB"
        )
        await update.message.reply_text(welcome_message)
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_message = (
            "📋 How to use this bot:\n\n"
            "1. Forward any file to this bot\n"
            "2. Wait for processing\n"
            "3. Get your direct download link\n\n"
            "Features:\n"
            "• Fast direct downloads\n"
            "• File information display\n"
            "• Support for all file types\n\n"
            "Commands:\n"
            "/start - Start the bot\n"
            "/help - Show this help message"
        )
        await update.message.reply_text(help_message)
    
    async def download_file(self, file_obj, filename):
        """Download file from Telegram servers"""
        try:
            # Generate unique filename to avoid conflicts
            timestamp = str(int(time.time()))
            file_hash = hashlib.md5(filename.encode()).hexdigest()[:8]
            safe_filename = f"{timestamp}_{file_hash}_{filename}"
            file_path = os.path.join(DOWNLOAD_FOLDER, safe_filename)
            
            # Download file
            file_data = await file_obj.download_as_bytearray()
            
            # Save file asynchronously
            async with aiofiles.open(file_path, 'wb') as f:
                await f.write(file_data)
            
            return file_path, safe_filename
        except Exception as e:
            logger.error(f"Error downloading file: {e}")
            return None, None
    
    def format_file_size(self, size_bytes):
        """Format file size in human readable format"""
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
            # Check file size
            if file_size > MAX_FILE_SIZE:
                await update.message.reply_text(
                    f"❌ File too large! Maximum size: {MAX_FILE_SIZE // (1024*1024)}MB"
                )
                return
            
            # Send processing message
            processing_msg = await update.message.reply_text("⏳ Processing your file...")
            
            # Download file
            file_path, safe_filename = await self.download_file(file_obj, original_filename)
            
            if not file_path:
                await processing_msg.edit_text("❌ Failed to download file. Please try again.")
                return
            
            # Generate download URL
            encoded_filename = quote(safe_filename)
            download_url = f"{BASE_URL}/download/{encoded_filename}"
            
            # Create response message
            response_message = (
                f"✅ File processed successfully!\n\n"
                f"📄 **File:** `{original_filename}`\n"
                f"📊 **Size:** {self.format_file_size(file_size)}\n"
                f"🔗 **Direct Link:** [Download Here]({download_url})\n\n"
                f"💡 *Click the link for faster download*"
            )
            
            await processing_msg.edit_text(
                response_message, 
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
            
        except Exception as e:
            logger.error(f"Error processing file: {e}")
            await update.message.reply_text("❌ An error occurred while processing your file.")
    
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
        logger.info("Starting Telegram Download Bot...")
        self.app.run_polling()

# HTTP Server for serving files
from aiohttp import web, hdrs
import mimetypes

async def download_handler(request):
    """Handle file download requests"""
    filename = request.match_info['filename']
    file_path = os.path.join(DOWNLOAD_FOLDER, filename)
    
    if not os.path.exists(file_path):
        return web.Response(status=404, text="File not found")
    
    # Determine content type
    content_type, _ = mimetypes.guess_type(filename)
    if not content_type:
        content_type = 'application/octet-stream'
    
    # Set headers for faster download
    headers = {
        hdrs.CONTENT_TYPE: content_type,
        hdrs.CONTENT_DISPOSITION: f'attachment; filename="{filename}"',
        hdrs.ACCEPT_RANGES: 'bytes',
        hdrs.CACHE_CONTROL: 'no-cache',
    }
    
    # Support range requests for resume capability
    file_size = os.path.getsize(file_path)
    range_header = request.headers.get('Range')
    
    if range_header:
        # Parse range header
        range_match = range_header.replace('bytes=', '').split('-')
        start = int(range_match[0]) if range_match[0] else 0
        end = int(range_match[1]) if range_match[1] else file_size - 1
        
        headers[hdrs.CONTENT_RANGE] = f'bytes {start}-{end}/{file_size}'
        headers[hdrs.CONTENT_LENGTH] = str(end - start + 1)
        
        return web.FileResponse(
            file_path,
            status=206,
            headers=headers,
            chunk_size=8192
        )
    else:
        headers[hdrs.CONTENT_LENGTH] = str(file_size)
        return web.FileResponse(file_path, headers=headers, chunk_size=8192)

async def create_web_server():
    """Create and configure web server"""
    app = web.Application()
    app.router.add_get('/download/{filename}', download_handler)
    return app

async def run_web_server():
    """Run the web server"""
    app = await create_web_server()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)  # Bind to all interfaces
    await site.start()
    logger.info(f"Web server started on port {PORT}")
    
    # Keep server running
    while True:
        await asyncio.sleep(3600)

# Main execution
if __name__ == "__main__":
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable not set!")
        exit(1)
    
    # Create bot instance
    bot = TelegramDownloadBot()
    
    # Start web server and bot concurrently
    async def main():
        # Start web server
        server_task = asyncio.create_task(run_web_server())
        
        # Start bot
        await bot.app.initialize()
        await bot.app.start()
        await bot.app.updater.start_polling()
        
        # Wait for tasks
        await asyncio.gather(server_task)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    finally:
        logger.info("Bot shutdown complete")