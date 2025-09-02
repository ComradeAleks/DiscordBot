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
    import json
    from chatgpt_api import get_chatgpt_response
    history_path = os.path.join(os.path.dirname(__file__), "qotd_history.json")
    # Load last 31 QOTDs
    if os.path.exists(history_path):
        with open(history_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            history = data.get("history", [])
    else:
        history = []

    last_31 = history[-31:]
    last_deep = None
    for i, q in enumerate(reversed(last_31)):
        if "deep" in q.lower() or "philosoph" in q.lower() or "meaning" in q.lower():
            last_deep = i
            break

    prompt = "Your task is to give me a question of the day (QOTD) Follow these rules: "
    prompt += "1. Each question should be unique. "
    prompt += "2. All questions should have varying structure and wording and theme. "
    prompt += "3. Questions should be simple and fun to answer (no economics, no death, no boring things). "
    prompt += "4. Only reply with the question, nothing else. "
    prompt += "These are the previous asked questions of the past month: "
    prompt += "; ".join(last_31) + ". "

    response = get_chatgpt_response(prompt)
    bot.last_qotd = response
    # Save new QOTD, keep only last 31
    history.append(response)
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump({"history": history[-31:]}, f, ensure_ascii=False, indent=2)
    for guild in bot.guilds:
        channel = discord.utils.get(guild.text_channels, name="❓qotd")
        if channel:
            await channel.send(response)

@discord.app_commands.command(name="ping", description="Replies with Pong!")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!")

@discord.app_commands.command(name="repeat", description="Repeats the last QOTD or fetches a new one if none exists.")
async def repeat(interaction: discord.Interaction):
    await interaction.response.defer()  # Immediately acknowledge the interaction to avoid timeout
    bot = interaction.client
    if hasattr(bot, 'last_qotd') and bot.last_qotd:
        await interaction.followup.send(bot.last_qotd)
    else:
        import json
        from chatgpt_api import get_chatgpt_response
        history_path = os.path.join(os.path.dirname(__file__), "qotd_history.json")
        if os.path.exists(history_path):
            with open(history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                history = data.get("history", [])
        else:
            history = []
        last_31 = history[-31:]
        last_deep = None
        for i, q in enumerate(reversed(last_31)):
            if "deep" in q.lower() or "philosoph" in q.lower() or "meaning" in q.lower():
                last_deep = i
                break
        prompt = "Give me a question of the day (QOTD) that is not a duplicate or too similar of any of these: "
        prompt += "; ".join(last_31) + ". "
        prompt += "Avoid deep or philosophical questions if one was asked in the last 7 days. ONLY reply with the question."
        response = get_chatgpt_response(prompt)
        bot.last_qotd = response
        history.append(response)
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump({"history": history[-31:]}, f, ensure_ascii=False, indent=2)
        await interaction.followup.send(response)
        

bot = QOTDBot()

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    await bot.tree.sync()
    if not hasattr(bot, 'scheduler_started'):
        scheduler = AsyncIOScheduler(timezone=pytz.timezone('Europe/Oslo'))
        scheduler.add_job(send_qotd, CronTrigger(hour=12, minute=0), args=[bot])
        scheduler.start()
        bot.scheduler_started = True

if __name__ == '__main__':
    bot.run(TOKEN)
