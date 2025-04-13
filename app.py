import os
import tempfile
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from pydub import AudioSegment
from faster_whisper import WhisperModel
from googletrans import Translator
from gtts import gTTS
import moviepy.editor as mp
import asyncio
from concurrent.futures import ThreadPoolExecutor

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class TranslationBot:
    def __init__(self):
        self.model = None
        self.translator = Translator()
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.model_loaded = False
        self.model_lock = asyncio.Lock()
        
    async def load_model(self):
        """Load the Whisper model if not already loaded"""
        if self.model_loaded:
            return
            
        async with self.model_lock:
            if not self.model_loaded:  # Double-check locking
                try:
                    logger.info("Loading Whisper model...")
                    self.model = WhisperModel(
                        "small",
                        device="cpu",
                        compute_type="int8",
                        download_root="./models"
                    )
                    self.model_loaded = True
                    logger.info("Model loaded successfully")
                except Exception as e:
                    logger.error(f"Failed to load model: {e}")
                    raise

    async def process_audio(self, audio_file: str) -> str:
        """Process audio file and return translated text"""
        try:
            # Convert audio to proper format
            audio = AudioSegment.from_file(audio_file)
            audio = audio.set_frame_rate(16000).set_channels(1)
            
            # Save to temporary WAV file
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_file:
                audio.export(wav_file.name, format="wav")
                
                # Transcribe audio to text
                segments, _ = await asyncio.get_event_loop().run_in_executor(
                    self.executor,
                    lambda: list(self.model.transcribe(wav_file.name))
                
                text = " ".join([segment.text for segment in segments])
                
                # Translate text
                translation = await asyncio.get_event_loop().run_in_executor(
                    self.executor,
                    lambda: self.translator.translate(text, src='uz', dest='en'))
                
                return translation.text
        finally:
            if 'wav_file' in locals() and os.path.exists(wav_file.name):
                os.unlink(wav_file.name)

    async def handle_audio(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle audio messages"""
        processing_msg = await update.message.reply_text("🔊 Processing audio...")
        
        try:
            await self.load_model()
            
            with tempfile.NamedTemporaryFile(suffix=".ogg") as temp_audio:
                # Download audio
                audio_file = await update.message.audio.get_file()
                await audio_file.download_to_drive(temp_audio.name)
                
                # Process and translate
                translated_text = await self.process_audio(temp_audio.name)
                
                # Convert to speech
                with tempfile.NamedTemporaryFile(suffix=".mp3") as output_file:
                    await asyncio.get_event_loop().run_in_executor(
                        self.executor,
                        lambda: gTTS(translated_text, lang='en').save(output_file.name))
                    
                    # Send to user
                    await update.message.reply_audio(
                        audio=open(output_file.name, 'rb'),
                        title="Translated Audio",
                        performer="Translation Bot"
                    )
        except Exception as e:
            logger.error(f"Audio processing error: {e}")
            await update.message.reply_text("❌ Error processing audio. Please try again.")
        finally:
            await processing_msg.delete()

    async def handle_video(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle video messages"""
        processing_msg = await update.message.reply_text("🎥 Processing video...")
        
        try:
            await self.load_model()
            
            # Create temporary files
            with tempfile.NamedTemporaryFile(suffix=".mp4") as temp_video, \
                 tempfile.NamedTemporaryFile(suffix=".wav") as temp_audio:
                
                # Download video
                video_file = await update.message.video.get_file()
                await video_file.download_to_drive(temp_video.name)
                
                # Extract audio
                await asyncio.get_event_loop().run_in_executor(
                    self.executor,
                    lambda: mp.VideoFileClip(temp_video.name).audio.write_audiofile(
                        temp_audio.name, codec='pcm_s16le'))
                
                # Process and translate
                translated_text = await self.process_audio(temp_audio.name)
                
                # Create output files
                with tempfile.NamedTemporaryFile(suffix=".mp3") as tts_file, \
                     tempfile.NamedTemporaryFile(suffix=".mp4") as output_file:
                    
                    # Generate TTS
                    await asyncio.get_event_loop().run_in_executor(
                        self.executor,
                        lambda: gTTS(translated_text, lang='en').save(tts_file.name))
                    
                    # Merge video with new audio
                    cmd = f"ffmpeg -y -i {temp_video.name} -i {tts_file.name} -c:v copy -map 0:v:0 -map 1:a:0 -shortest {output_file.name}"
                    await asyncio.get_event_loop().run_in_executor(
                        self.executor,
                        lambda: os.system(cmd))
                    
                    # Send to user
                    await update.message.reply_video(
                        video=open(output_file.name, 'rb'),
                        supports_streaming=True,
                        caption="Translated Video"
                    )
        except Exception as e:
            logger.error(f"Video processing error: {e}")
            await update.message.reply_text("❌ Error processing video. Please try again.")
        finally:
            await processing_msg.delete()

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    await update.message.reply_text(
        "🎙️ Hello! I'm an audio/video translation bot\n\n"
        "Send me an audio or video message in Uzbek, "
        "and I'll translate it to English for you!"
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Update {update} caused error: {context.error}")
    if update.message:
        await update.message.reply_text("⚠️ An error occurred. Please try again later.")

def main():
    """Start the bot"""
    try:
        # Create bot instance
        bot = TranslationBot()
        
        # Build application
        application = Application.builder() \
            .token("8067463029:AAEtgsxAvuoEh8FXypfzUCPDhdVzmRpjxxk") \
            .build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(MessageHandler(filters.AUDIO, bot.handle_audio))
        application.add_handler(MessageHandler(filters.VIDEO, bot.handle_video))
        application.add_error_handler(error_handler)
        
        # Run bot
        logger.info("Starting bot...")
        application.run_polling()
    except Exception as e:
        logger.critical(f"Failed to start bot: {e}")

if __name__ == "__main__":
    main()
