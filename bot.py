import discord
from discord.ext import commands
import os
import asyncio
import logging
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

# Import QOTD, movie, and general modules
import qotd
import movies
import general

load_dotenv()

TOKEN = os.getenv('DISCORD_BOT_TOKEN', 'YOUR_BOT_TOKEN')

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True


class QOTDBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, application_id=None)
        self.last_qotd = None
        self.reconnecting = False
        self.scheduler_started = False
        self.scheduler = None  # Store scheduler instance
        self.qotd_job_id = "qotd_job"
        self.movie_job_id = "movie_job"
        self.last_qotd_date = None  # Track when we last sent a QOTD
        self.pending_qotd = None  # Store a generated question that couldn't be sent
        self.pending_qotd_date = None  # When the pending question was generated
        self.last_qotd_messages = []  # Store message objects for reaction tracking

    async def setup_hook(self):
        self.tree.add_command(ping)
        # Add QOTD commands
        self.tree.add_command(qotd.qotd_repeat)
        self.tree.add_command(qotd.qotd_new)
        # Add movie commands
        self.tree.add_command(movies.movie_add)
        self.tree.add_command(movies.movie_choose)
        self.tree.add_command(movies.movie_list)
        self.tree.add_command(movies.movie_test)
        # Add general commands
        self.tree.add_command(general.comrade_settings)

    async def on_disconnect(self):
        logger.warning("Bot disconnected from Discord!")
        
    async def on_resumed(self):
        logger.info("Bot connection resumed!")
        
    async def close(self):
        logger.info("Bot is shutting down...")
        await super().close()


@discord.app_commands.command(name="ping", description="Replies with Pong!")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!")


bot = QOTDBot()


@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user.name}')
    try:
        await bot.tree.sync()
        logger.info("Command tree synced successfully")
    except Exception as e:
        logger.error(f"Failed to sync command tree: {e}")
        
    # Load the persistent last QOTD date from storage
    try:
        _, _, _, persistent_last_qotd_date = await qotd.load_question_data()
        if persistent_last_qotd_date:
            bot.last_qotd_date = persistent_last_qotd_date
            logger.info(f"Loaded last QOTD date from storage: {persistent_last_qotd_date}")
        else:
            logger.info("No last QOTD date found in storage")
    except Exception as e:
        logger.error(f"Failed to load last QOTD date: {e}")
        
    # Only start scheduler once, and don't restart on reconnections
    if not bot.scheduler_started:
        # Load settings for scheduler
        settings = general.load_settings()
        qotd_settings = settings.get("qotd", {})
        movie_settings = settings.get("movie", {})
        
        bot.scheduler = AsyncIOScheduler(timezone=pytz.timezone('Europe/Oslo'))
        
        # QOTD scheduler with dynamic settings
        qotd_time = qotd_settings.get("time", {"hour": 12, "minute": 0})
        qotd_days = qotd_settings.get("days", [0, 1, 2, 3, 4, 5, 6])
        # Convert day indices to day_of_week string (0=mon, 6=sun)
        qotd_days_str = ','.join(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'][i] for i in qotd_days)
        bot.scheduler.add_job(
            qotd.send_qotd, 
            CronTrigger(day_of_week=qotd_days_str, hour=qotd_time['hour'], minute=qotd_time['minute']), 
            args=[bot],
            id=bot.qotd_job_id
        )
        
        # Movie scheduler with dynamic settings (use notification time/day)
        movie_notification_time = movie_settings.get("notification_time", {"hour": 12, "minute": 0})
        movie_notification_days = movie_settings.get("notification_day", [4])
        movie_notification_days_str = ','.join(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'][i] for i in movie_notification_days)
        bot.scheduler.add_job(
            movies.choose_random_movie, 
            CronTrigger(day_of_week=movie_notification_days_str, hour=movie_notification_time['hour'], minute=movie_notification_time['minute']), 
            args=[bot],
            id=bot.movie_job_id
        )
        
        bot.scheduler.start()
        bot.scheduler_started = True
        logger.info(f"Scheduler started successfully (QOTD: {general.format_days(qotd_days)} at {general.format_time(qotd_time)}, Movie Notification: {general.format_days(movie_notification_days)} at {general.format_time(movie_notification_time)})")
    else:
        logger.info("Scheduler already running - not restarting")
    
    # Check if we have a pending QOTD to send after reconnection
    await qotd.check_pending_qotd(bot)
    
    # Clean up expired movie selections
    await movies.cleanup_movies_on_startup()


async def run_bot_with_retry():
    """Run the bot with automatic reconnection on failure."""
    retry_count = 0
    
    while True:
        try:
            logger.info(f"Starting bot (attempt {retry_count + 1})")
            await bot.start(TOKEN)
            
        except discord.LoginFailure:
            logger.error("Invalid bot token! Please check your DISCORD_BOT_TOKEN.")
            break  # Don't retry on invalid token
            
        except discord.HTTPException as e:
            retry_count += 1
            logger.error(f"Discord HTTP error (attempt {retry_count}): {e}")
            
        except discord.ConnectionClosed as e:
            retry_count += 1
            logger.error(f"Discord connection closed (attempt {retry_count}): {e}")
            
        except Exception as e:
            retry_count += 1
            logger.error(f"Unexpected bot error (attempt {retry_count}): {e}")
        
        if not bot.is_closed():
            logger.info("Closing bot connection before retry...")
            await bot.close()
            
        logger.info("Retrying bot connection in 10 seconds...")
        await asyncio.sleep(10)


if __name__ == '__main__':
    try:
        asyncio.run(run_bot_with_retry())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
