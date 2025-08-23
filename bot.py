import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

load_dotenv()

TOKEN = os.getenv('DISCORD_BOT_TOKEN', 'YOUR_BOT_TOKEN')

intents = discord.Intents.default()
intents.message_content = True

class QOTDBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, application_id=None)
        self.last_qotd = None

    async def setup_hook(self):
        self.tree.add_command(ping)
        self.tree.add_command(repeat)

async def send_qotd(bot):
    from chatgpt_api import get_chatgpt_response
    response = get_chatgpt_response("Give me a question of the day (QOTD) dont say anything else than that question")
    bot.last_qotd = response
    for guild in bot.guilds:
        channel = discord.utils.get(guild.text_channels, name="qotd")
        if channel:
            await channel.send(response)

@discord.app_commands.command(name="ping", description="Replies with Pong!")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!")

@discord.app_commands.command(name="repeat", description="Repeats the last QOTD or fetches a new one if none exists.")
async def repeat(interaction: discord.Interaction):
    bot = interaction.client
    if hasattr(bot, 'last_qotd') and bot.last_qotd:
        await interaction.response.send_message(bot.last_qotd)
    else:
        from chatgpt_api import get_chatgpt_response
        response = get_chatgpt_response("Give me a question of the day (QOTD) dont say anything else than that question")
        bot.last_qotd = response
        await interaction.response.send_message(response)
        

bot = QOTDBot()

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    await bot.tree.sync()
    scheduler = AsyncIOScheduler(timezone=pytz.timezone('Europe/Oslo'))
    scheduler.add_job(send_qotd, CronTrigger(hour=12, minute=0), args=[bot])
    scheduler.start()

if __name__ == '__main__':
    bot.run(TOKEN)
