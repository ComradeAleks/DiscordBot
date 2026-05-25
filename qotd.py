import discord
from discord.ext import commands
from discord import ui
import logging
import os
import json
import datetime
import gc
import asyncio

logger = logging.getLogger(__name__)


async def check_pending_qotd(bot):
    """Check if there's a pending QOTD to send after reconnection, or if we missed today's question."""
    try:
        from general import load_settings
        settings = load_settings()
        qotd_channel_name = settings.get("qotd", {}).get("channel", "❓qotd")
        
        logger.info("Checking for pending QOTD...")
        today = datetime.date.today()
        
        # If we have a pending question and haven't sent today's QOTD yet
        if (bot.pending_qotd and bot.pending_qotd_date and 
            bot.last_qotd_date != today):
            
            logger.info(f"Found pending QOTD from {bot.pending_qotd_date}, attempting to send...")
            
            # Try to send the pending question
            try:
                bot.last_qotd = bot.pending_qotd
                bot.last_qotd_messages = []
                
                # Create embed
                embed = create_qotd_embed(bot.pending_qotd)
                
                success_count = 0
                for guild in bot.guilds:
                    try:
                        channel = discord.utils.get(guild.channels, name=qotd_channel_name)
                        if channel:
                            await send_message_with_retry(channel, embed=embed)
                            success_count += 1
                            
                            # Track the message for reaction counting
                            try:
                                messages = [msg async for msg in channel.history(limit=1)]
                                if messages:
                                    bot.last_qotd_messages.append(messages[0])
                                    # Add reaction emojis for voting
                                    await messages[0].add_reaction("👍")
                                    await messages[0].add_reaction("👎")
                            except Exception as e:
                                logger.error(f"Failed to retrieve message for reaction tracking: {e}")
                    except Exception as e:
                        logger.error(f"Failed to send pending QOTD to guild {guild.name}: {e}")
                
                if success_count > 0:
                    logger.info(f"Successfully sent pending QOTD to {success_count} guilds")
                    bot.last_qotd_date = today
                    await save_last_qotd_date(today)
                    
                    # Clear pending QOTD
                    bot.pending_qotd = None
                    bot.pending_qotd_date = None
                else:
                    logger.error("Failed to send pending QOTD to any guild")
                    
            except Exception as e:
                logger.error(f"Error sending pending QOTD: {e}")
        
        # Check if we missed today's scheduled question
        elif bot.last_qotd_date != today:
            logger.info(f"Missed scheduled QOTD for {today}. Generating and sending now...")
            
            # Generate a new question
            try:
                question_data = await generate_new_qotd()
                logger.info(f"Generated new QOTD: {question_data['title'][:50]}...")
                
                bot.last_qotd = question_data
                bot.last_qotd_messages = []
                
                # Create embed
                embed = create_qotd_embed(question_data)
                
                success_count = 0
                for guild in bot.guilds:
                    try:
                        channel = discord.utils.get(guild.channels, name=qotd_channel_name)
                        if channel:
                            await send_message_with_retry(channel, embed=embed)
                            success_count += 1
                            
                            # Track the message for reaction counting
                            try:
                                messages = [msg async for msg in channel.history(limit=1)]
                                if messages:
                                    bot.last_qotd_messages.append(messages[0])
                                    # Add reaction emojis for voting
                                    await messages[0].add_reaction("👍")
                                    await messages[0].add_reaction("👎")
                            except Exception as e:
                                logger.error(f"Failed to retrieve message for reaction tracking: {e}")
                    except Exception as e:
                        logger.error(f"Failed to send makeup QOTD to guild {guild.name}: {e}")
                
                if success_count > 0:
                    logger.info(f"Successfully sent makeup QOTD to {success_count} guilds")
                    bot.last_qotd_date = today
                    await save_last_qotd_date(today)
                else:
                    logger.error("Failed to send makeup QOTD to any guild")
                    # Store as pending
                    bot.pending_qotd = question_data
                    bot.pending_qotd_date = today
                    
            except Exception as e:
                logger.error(f"Error generating/sending makeup QOTD: {e}")
        else:
            logger.info(f"Already sent QOTD for {today}")
                
    except Exception as e:
        logger.error(f"Error checking pending QOTD: {e}")


async def send_qotd(bot):
    try:
        from general import load_settings
        settings = load_settings()
        qotd_channel_name = settings.get("qotd", {}).get("channel", "❓qotd")
        
        logger.info("send_qotd called")
        today = datetime.date.today()
        
        # Check if we already sent a QOTD today
        if bot.last_qotd_date == today:
            logger.info(f"Already sent QOTD today ({today}). Skipping.")
            return
        
        # More comprehensive connection health checks
        if not bot.is_ready():
            logger.warning("Bot is not ready. Skipping QOTD.")
            return
            
        if not bot.guilds:
            logger.warning("Bot has no guilds. Skipping QOTD.")
            return
            
        # Test if we can actually communicate with Discord API
        try:
            await bot.fetch_user(bot.user.id)
            logger.info("Connection test passed")
        except Exception as e:
            logger.error(f"Connection test failed: {e}. Skipping QOTD.")
            return
        
        logger.info("Starting QOTD process")
        
        # Check if we have a pending question from a previous failed attempt
        if bot.pending_qotd and bot.pending_qotd_date:
            logger.info(f"Using pending QOTD from {bot.pending_qotd_date}")
            question_data = bot.pending_qotd
            bot.pending_qotd = None
            bot.pending_qotd_date = None
        else:
            logger.info("Generating new QOTD")
            question_data = await generate_new_qotd()
        
        bot.last_qotd = question_data
        
        # Create embed
        embed = create_qotd_embed(question_data)
        
        # Clear previous message references
        bot.last_qotd_messages = []
        
        # Send to all guilds - but fail fast if there are connection issues
        success_count = 0
        for guild in bot.guilds:
            try:
                channel = discord.utils.get(guild.channels, name=qotd_channel_name)
                if channel:
                    await send_message_with_retry(channel, embed=embed)
                    success_count += 1
                    
                    # Track the message for reaction counting
                    try:
                        messages = [msg async for msg in channel.history(limit=1)]
                        if messages:
                            bot.last_qotd_messages.append(messages[0])
                            # Add reaction emojis for voting
                            await messages[0].add_reaction("👍")
                            await messages[0].add_reaction("👎")
                    except Exception as e:
                        logger.error(f"Failed to retrieve message for reaction tracking: {e}")
            except Exception as e:
                logger.error(f"Failed to send QOTD to guild {guild.name}: {e}")
                    
        if success_count > 0:
            logger.info(f"QOTD sent successfully to {success_count} guilds")
            bot.last_qotd_date = today
            await save_last_qotd_date(today)
        else:
            logger.error("Failed to send QOTD to any guild. Storing as pending.")
            bot.pending_qotd = question_data
            bot.pending_qotd_date = today
        
    except Exception as e:
        logger.error(f"Error in send_qotd: {e}")


async def send_message_with_retry(channel, message=None, embed=None, max_retries=1):
    """Send a Discord message with minimal retry to avoid flooding during connection issues."""
    try:
        # Test connection health first
        if not channel.guild.me or not channel.permissions_for(channel.guild.me).send_messages:
            logger.error(f"No permission to send messages to #{channel.name}")
            raise discord.Forbidden(None, "No permission to send messages")
        
        if embed:
            await channel.send(embed=embed)
        else:
            await channel.send(message)
        logger.info(f"Message sent successfully to #{channel.name}")
        return
        
    except discord.HTTPException as e:
        # Don't retry on HTTP errors - likely connection issues
        logger.error(f"Discord API error sending message to #{channel.name}: {e}")
        raise e
        
    except discord.Forbidden:
        logger.error(f"No permission to send message to #{channel.name}")
        raise
        
    except discord.NotFound:
        logger.error(f"Channel #{channel.name} not found")
        raise
        
    except Exception as e:
        logger.error(f"Unexpected error sending message to #{channel.name}: {e}")
        raise e


async def load_question_data():
    """
    Load question history from JSON file.
    
    Data structure:
    - history: List of [question, rating_percentage, upvotes, downvotes] tuples
        Format: [["Question text", None, 0, 0]] (rating not actively used anymore)
    - last_qotd_date: Date when the last QOTD was sent (for startup checks)
    """
    logger.info("Loading question data")
    
    history_path = os.path.join(os.path.dirname(__file__), "qotd_history.json")
    
    last_qotd_date = None
    
    if os.path.exists(history_path):
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                
                # Handle empty file
                if not content:
                    logger.warning("History file is empty, initializing with default values")
                    history = []
                    last_qotd_date = None
                else:
                    data = json.loads(content)
                    raw_history = data.get("history", [])
                    
                    # Normalize history to format [question, percentage, upvotes, downvotes]
                    history = []
                    for item in raw_history:
                        if len(item) == 2:  # Old format: [question, percentage]
                            history.append([item[0], item[1], 0, 0])
                        elif len(item) == 4:  # Current format: [question, percentage, upvotes, downvotes]
                            history.append(item)
                        else:
                            history.append([item[0], None, 0, 0])  # Fallback
                    
                    last_qotd_date_str = data.get("last_qotd_date")
                    
                    # Parse the last_qotd_date string
                    if last_qotd_date_str:
                        try:
                            last_qotd_date = datetime.datetime.strptime(last_qotd_date_str, "%Y-%m-%d").date()
                        except ValueError:
                            logger.warning(f"Invalid date format in history file: {last_qotd_date_str}")
                            last_qotd_date = None
                    else:
                        last_qotd_date = None
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse history file: {e}. Initializing with default values.")
            history = []
            last_qotd_date = None
    else:
        history = []
        last_qotd_date = None
    
    return history, [], [], last_qotd_date


async def save_question_data(history, highest_rated=None, lowest_rated=None, last_qotd_date=None):
    """Save question history to JSON file."""
    logger.info("Saving question data")
    
    history_path = os.path.join(os.path.dirname(__file__), "qotd_history.json")
    
    # Convert date to string for JSON serialization
    last_qotd_date_str = None
    if last_qotd_date:
        if isinstance(last_qotd_date, datetime.date):
            last_qotd_date_str = last_qotd_date.strftime("%Y-%m-%d")
        else:
            last_qotd_date_str = last_qotd_date
    
    # Build JSON with compact arrays
    data = {
        "history": history,
        "last_qotd_date": last_qotd_date_str
    }
    
    lines = ["{"]
    lines.append('  "history": [')
    for i, item in enumerate(history):
        comma = "," if i < len(history) - 1 else ""
        lines.append(f"    {json.dumps(item, ensure_ascii=False)}{comma}")
    lines.append("  ],")
    lines.append(f'  "last_qotd_date": {json.dumps(last_qotd_date_str)}')
    lines.append("}")
    
    with open(history_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    
    return history


async def save_last_qotd_date(date):
    """Save the last QOTD date to persistent storage without affecting question data."""
    try:
        history, _, _, _ = await load_question_data()
        await save_question_data(history, None, None, date)
        logger.info(f"Saved last QOTD date to storage: {date}")
    except Exception as e:
        logger.error(f"Failed to save last QOTD date: {e}")


async def generate_new_qotd():
    """Generate a new QOTD by fetching from Reddit."""
    from reddit_api import get_reddit_question
    from general import load_settings
    
    logger.info("Generating new QOTD from Reddit")
    
    # Load settings to get subreddit list
    settings = load_settings()
    subreddit_list = settings.get("qotd", {}).get("subreddits", ["randomquestions", "randomquestion"])
    
    # Load question history
    history, _, _, _ = await load_question_data()
    
    # Extract just the question texts for comparison
    used_questions = [item[0] for item in history]
    
    # Fetch a question from Reddit
    question_data = await get_reddit_question(subreddit_list, used_questions)
    
    if not question_data:
        logger.error("Failed to fetch question from Reddit")
        raise Exception("Could not find any unused questions from Reddit (past 24h, >5 words). Try adding more subreddits or clearing history.")
    
    # Add new question to history (store the cleaned title)
    history.append([question_data['title'], None, 0, 0])
    
    # Save the updated history
    await save_question_data(history, [], [])
    
    return question_data


def create_qotd_embed(question_data):
    """Create a Discord embed for QOTD with source information."""
    embed = discord.Embed(
        title="❓ Question of the Day",
        description=question_data['title'],
        color=discord.Color.blue(),
        url=question_data['url']
    )
    
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
