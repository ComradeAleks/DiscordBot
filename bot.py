import discord
from discord.ext import commands
import os
import asyncio
import logging
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
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
        self.setup_done = False
        self.scheduler = None  # Store scheduler instance
        self.scheduler_started = False
        self.qotd_job_id = "qotd_job"
        self.movie_job_id = "movie_job"
        self.last_qotd_date = None  # Track when we last sent a QOTD (Europe/Oslo date)
        self.pending_qotd = None  # Store a generated question that couldn't be sent
        self.last_qotd_messages = []  # Store message objects for reaction tracking

    async def setup_hook(self):
        # setup_hook runs on every login, so only do the one-time setup once
        if not self.setup_done:
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

            # Load the persistent last QOTD date from storage
            try:
                _, persistent_last_qotd_date = await qotd.load_question_data()
                self.last_qotd_date = persistent_last_qotd_date
                logger.info(f"Loaded last QOTD date from storage: {persistent_last_qotd_date}")
            except Exception as e:
                logger.error(f"Failed to load last QOTD date: {e}")

            start_scheduler(self)
            self.setup_done = True

        try:
            await self.tree.sync()
            logger.info("Command tree synced successfully")
        except Exception as e:
            logger.error(f"Failed to sync command tree: {e}")

    async def on_disconnect(self):
        logger.warning("Bot disconnected from Discord!")
        
    async def on_resumed(self):
        logger.info("Bot connection resumed!")
        
    async def close(self):
        logger.info("Bot is shutting down...")
        await super().close()


def start_scheduler(bot):
    bot.scheduler = AsyncIOScheduler(timezone=pytz.timezone('Europe/Oslo'))
    general.apply_schedule(bot)
    bot.scheduler.start()
    bot.scheduler_started = True
    logger.info("Scheduler started successfully")


@discord.app_commands.command(name="ping", description="Replies with Pong!")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!")


bot = QOTDBot()


@bot.event
async def on_ready():
    # on_ready also fires after reconnecting with a new session (e.g. after an internet outage),
    # so everything here must be safe to run more than once
    logger.info(f'Logged in as {bot.user.name}')

    # Send today's QOTD if it was missed while disconnected (only within the allowed time window)
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
        # Reset internal state so the closed client can be started again
        bot.clear()
            
        logger.info("Retrying bot connection in 10 seconds...")
        await asyncio.sleep(10)


if __name__ == '__main__':
    try:
        asyncio.run(run_bot_with_retry())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
