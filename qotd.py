import discord
import logging
import os
import json
import datetime
import asyncio
import pytz

logger = logging.getLogger(__name__)

TIMEZONE = pytz.timezone('Europe/Oslo')

# A missed QOTD is only made up between the scheduled time and this hour (Oslo time).
# Outside that window it waits for the next scheduled run instead of posting at night.
MAKEUP_CUTOFF_HOUR = 22

HISTORY_PATH = os.path.join(os.path.dirname(__file__), "qotd_history.json")

# Prevents the scheduled job and a reconnect makeup from posting at the same time
_send_lock = asyncio.Lock()


def oslo_now():
    return datetime.datetime.now(TIMEZONE)


def is_in_makeup_window(settings, now):
    """Check if a missed QOTD may still be sent now: a scheduled day, after the scheduled time, before the cutoff."""
    qotd_settings = settings.get("qotd", {})
    qotd_days = qotd_settings.get("days", [0, 1, 2, 3, 4, 5, 6])
    qotd_time = qotd_settings.get("time", {"hour": 12, "minute": 0})

    if now.weekday() not in qotd_days:
        return False

    scheduled = now.replace(hour=qotd_time['hour'], minute=qotd_time['minute'], second=0, microsecond=0)
    return scheduled <= now and now.hour < MAKEUP_CUTOFF_HOUR


async def check_pending_qotd(bot):
    """Called on (re)connect: send today's QOTD if it was missed and it's still a reasonable time to post it."""
    try:
        from general import load_settings
        settings = load_settings()
        now = oslo_now()

        if bot.last_qotd_date == now.date():
            logger.info(f"Already sent QOTD for {now.date()}")
            return

        if not is_in_makeup_window(settings, now):
            logger.info(f"No QOTD sent yet for {now.date()}, but it's not due now ({now:%a %H:%M}). Waiting for the scheduler.")
            return

        logger.info(f"Missed scheduled QOTD for {now.date()}. Sending now...")
        await send_qotd(bot)

    except Exception as e:
        logger.error(f"Error checking pending QOTD: {e}")


async def send_qotd(bot):
    async with _send_lock:
        try:
            from general import load_settings
            settings = load_settings()
            qotd_channel_name = settings.get("qotd", {}).get("channel", "❓qotd")

            logger.info("send_qotd called")
            today = oslo_now().date()

            # Check if we already sent a QOTD today
            if bot.last_qotd_date == today:
                logger.info(f"Already sent QOTD today ({today}). Skipping.")
                return

            # Connection health checks
            if not bot.is_ready():
                logger.warning("Bot is not ready. Skipping QOTD.")
                return

            if not bot.guilds:
                logger.warning("Bot has no guilds. Skipping QOTD.")
                return

            # Test if we can actually communicate with Discord API
            try:
                await bot.fetch_user(bot.user.id)
            except Exception as e:
                logger.error(f"Connection test failed: {e}. Skipping QOTD.")
                return

            # Reuse a question from a previous failed attempt if there is one
            if bot.pending_qotd:
                logger.info("Using pending QOTD from a previous failed attempt")
                question_data = bot.pending_qotd
                bot.pending_qotd = None
            else:
                logger.info("Generating new QOTD")
                question_data = await generate_new_qotd()

            messages = await post_qotd(bot, question_data, qotd_channel_name)

            if messages:
                logger.info(f"QOTD sent successfully to {len(messages)} guilds")
                bot.last_qotd = question_data
                bot.last_qotd_messages = messages
                bot.last_qotd_date = today
                await record_question_used(question_data['title'], today)
            else:
                logger.error("Failed to send QOTD to any guild. Storing as pending.")
                bot.pending_qotd = question_data

        except Exception as e:
            logger.error(f"Error in send_qotd: {e}")


async def post_qotd(bot, question_data, channel_name):
    """Post the QOTD embed to the named channel in every guild and add voting reactions.

    Returns:
        list: The messages that were sent successfully
    """
    embed = create_qotd_embed(question_data)
    messages = []

    for guild in bot.guilds:
        channel = discord.utils.get(guild.text_channels, name=channel_name)
        if not channel:
            continue

        if not channel.permissions_for(guild.me).send_messages:
            logger.error(f"No permission to send messages to #{channel.name} in guild {guild.name}")
            continue

        try:
            message = await channel.send(embed=embed)
        except discord.HTTPException as e:
            logger.error(f"Failed to send QOTD to #{channel.name} in guild {guild.name}: {e}")
            continue

        logger.info(f"QOTD sent to #{channel.name} in guild {guild.name}")
        messages.append(message)

        try:
            await message.add_reaction("👍")
            await message.add_reaction("👎")
        except discord.HTTPException as e:
            logger.error(f"Failed to add reactions to QOTD in guild {guild.name}: {e}")

    return messages


async def load_question_data():
    """
    Load question history from JSON file.

    Data structure:
    - history: List of [question, rating_percentage, upvotes, downvotes] tuples
        Format: [["Question text", None, 0, 0]] (rating not actively used anymore)
    - last_qotd_date: Date when the last QOTD was sent (for startup checks)

    Returns:
        tuple: (history, last_qotd_date)
    """
    history = []
    last_qotd_date = None

    if not os.path.exists(HISTORY_PATH):
        return history, last_qotd_date

    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            content = f.read().strip()
    except OSError as e:
        logger.error(f"Failed to read history file: {e}")
        return history, last_qotd_date

    if not content:
        logger.warning("History file is empty, initializing with default values")
        return history, last_qotd_date

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse history file: {e}. Initializing with default values.")
        return history, last_qotd_date

    # Normalize history to format [question, percentage, upvotes, downvotes]
    for item in data.get("history", []):
        if len(item) == 2:  # Old format: [question, percentage]
            history.append([item[0], item[1], 0, 0])
        elif len(item) == 4:  # Current format: [question, percentage, upvotes, downvotes]
            history.append(item)
        else:
            history.append([item[0], None, 0, 0])  # Fallback

    last_qotd_date_str = data.get("last_qotd_date")
    if last_qotd_date_str:
        try:
            last_qotd_date = datetime.datetime.strptime(last_qotd_date_str, "%Y-%m-%d").date()
        except ValueError:
            logger.warning(f"Invalid date format in history file: {last_qotd_date_str}")

    return history, last_qotd_date


async def save_question_data(history, last_qotd_date=None):
    """Save question history to JSON file."""
    logger.info("Saving question data")

    last_qotd_date_str = None
    if isinstance(last_qotd_date, datetime.date):
        last_qotd_date_str = last_qotd_date.strftime("%Y-%m-%d")
    elif last_qotd_date:
        last_qotd_date_str = last_qotd_date

    # Build JSON with compact arrays (one question per line)
    lines = ["{"]
    lines.append('  "history": [')
    for i, item in enumerate(history):
        comma = "," if i < len(history) - 1 else ""
        lines.append(f"    {json.dumps(item, ensure_ascii=False)}{comma}")
    lines.append("  ],")
    lines.append(f'  "last_qotd_date": {json.dumps(last_qotd_date_str)}')
    lines.append("}")

    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


async def record_question_used(title, sent_date=None):
    """Add a posted question to the history so it isn't used again.

    If sent_date is given it becomes the new last_qotd_date, otherwise the stored date is kept.
    """
    try:
        history, last_qotd_date = await load_question_data()
        history.append([title, None, 0, 0])
        await save_question_data(history, sent_date or last_qotd_date)
    except Exception as e:
        logger.error(f"Failed to record question in history: {e}")


async def generate_new_qotd():
    """Fetch a new, unused QOTD from Reddit. It is only added to the history once it has been posted."""
    from reddit_api import get_reddit_question
    from general import load_settings

    logger.info("Generating new QOTD from Reddit")

    settings = load_settings()
    subreddit_list = settings.get("qotd", {}).get("subreddits", ["randomquestions", "randomquestion"])

    history, _ = await load_question_data()
    used_questions = [item[0] for item in history]

    question_data = await get_reddit_question(subreddit_list, used_questions)

    if not question_data:
        logger.error("Failed to fetch question from Reddit")
        raise Exception("Could not find any unused questions from Reddit in the past 24h. Try adding more subreddits.")

    return question_data


def create_qotd_embed(question_data):
    """Create a Discord embed for QOTD with source information."""
    embed = discord.Embed(
        title="❓ Question of the Day",
        description=question_data['title'],
        color=discord.Color.blue(),
        url=question_data['url']
    )

    # Add description/context if available and meaningful
    if question_data.get('description') and len(question_data['description'].strip()) > 10:
        # Limit description to 1024 characters (Discord field limit)
        description_text = question_data['description']
        if len(description_text) > 1024:
            description_text = description_text[:1021] + "..."
        embed.add_field(name="Context", value=description_text, inline=False)

    embed.set_footer(
        text=f"From r/{question_data['subreddit']} • Posted by u/{question_data['author']}"
    )

    # Add image if available (and not spoilered/NSFW)
    if 'image_url' in question_data:
        embed.set_image(url=question_data['image_url'])

    return embed


# Command definitions
@discord.app_commands.command(name="qotd-repeat", description="Repeats the last QOTD")
async def qotd_repeat(interaction: discord.Interaction):
    bot = interaction.client
    if bot.last_qotd:
        # Create embed from stored question data
        embed = create_qotd_embed(bot.last_qotd)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message("No QOTD has been sent yet!", ephemeral=True)


@discord.app_commands.command(name="qotd-new", description="Get a new question from Reddit")
async def qotd_new(interaction: discord.Interaction):
    await interaction.response.defer()
    bot = interaction.client

    try:
        question_data = await generate_new_qotd()

        # Create embed
        embed = create_qotd_embed(question_data)

        # Send the question as a followup
        message = await interaction.followup.send(embed=embed, wait=True)
        await record_question_used(question_data['title'])

        # Add reaction emojis for voting
        await message.add_reaction("👍")
        await message.add_reaction("👎")

        # Track the message and update bot state
        bot.last_qotd = question_data
        bot.last_qotd_messages = [message]  # Clear previous and add new message

        logger.info(f"New QOTD generated via /qotd-new command: {question_data['title'][:50]}...")

    except Exception as e:
        logger.error(f"Error generating new QOTD: {e}")
        await interaction.followup.send(f"Failed to generate a new QOTD: {str(e)}", ephemeral=True)
