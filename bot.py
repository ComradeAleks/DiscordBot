import discord
from discord.ext import commands
import os
import asyncio
import logging
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

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
        self.last_qotd_date = None  # Track when we last sent a QOTD
        self.pending_qotd = None  # Store a generated question that couldn't be sent
        self.pending_qotd_date = None  # When the pending question was generated
        self.style_selector_enabled = True  # Style selector is enabled by default
        self.last_qotd_messages = []  # Store message objects for reaction tracking

    async def setup_hook(self):
        self.tree.add_command(ping)
        self.tree.add_command(repeat)
        self.tree.add_command(new)
        self.tree.add_command(ratings)
        self.tree.add_command(togglestyle)
        self.tree.add_command(addmovie)
        self.tree.add_command(choosemovie)

    async def on_disconnect(self):
        logger.warning("Bot disconnected from Discord!")
        
    async def on_resumed(self):
        logger.info("Bot connection resumed!")
        
    async def close(self):
        logger.info("Bot is shutting down...")
        await super().close()

async def check_pending_qotd(bot):
    """Check if there's a pending QOTD to send after reconnection, or if we missed today's question."""
    try:
        import datetime
        today = datetime.date.today()
        
        # If we have a pending question and haven't sent today's QOTD yet
        if (bot.pending_qotd and bot.pending_qotd_date and 
            bot.last_qotd_date != today):
            
            days_old = (today - bot.pending_qotd_date).days
            
            # Only send if it's from today or yesterday
            if days_old <= 1:
                logger.info(f"Found pending QOTD from {bot.pending_qotd_date} - attempting to send now")
                
                bot.last_qotd_messages = []
                success_count = 0
                for guild in bot.guilds:
                    channel = discord.utils.get(guild.text_channels, name="❓qotd")
                    if channel:
                        try:
                            message = await channel.send(bot.pending_qotd)
                            await message.add_reaction("👍")
                            await message.add_reaction("👎")
                            bot.last_qotd_messages.append(message)
                            success_count += 1
                        except Exception as e:
                            logger.error(f"Failed to send pending QOTD to #{channel.name}: {e}")
                            return  # Keep it pending if still can't send
                
                if success_count > 0:
                    bot.last_qotd_date = today
                    await save_last_qotd_date(today)
                    bot.last_qotd = bot.pending_qotd
                    bot.pending_qotd = None
                    bot.pending_qotd_date = None
                    logger.info(f"Successfully sent pending QOTD to {success_count} channels")
                else:
                    logger.warning("Still couldn't send pending QOTD - keeping as pending")
            else:
                logger.info(f"Discarding old pending QOTD from {bot.pending_qotd_date} (too old)")
                bot.pending_qotd = None
                bot.pending_qotd_date = None
        
        # Check if we missed today's QOTD (no pending question and haven't sent today)
        elif bot.last_qotd_date != today:
            logger.info(f"Last QOTD was sent on: {bot.last_qotd_date}, today is: {today}")
            # Check if it's past 12:00 PM (the scheduled time)
            import pytz
            oslo_tz = pytz.timezone('Europe/Oslo')
            now_oslo = datetime.datetime.now(oslo_tz)
            today_12pm = oslo_tz.localize(datetime.datetime.combine(today, datetime.time(12, 0)))
            
            if now_oslo >= today_12pm:
                logger.info("Missed today's scheduled QOTD time - generating and sending now")
                
                try:
                    # Generate a new question
                    response = await generate_new_qotd()
                    
                    bot.last_qotd_messages = []
                    success_count = 0
                    for guild in bot.guilds:
                        channel = discord.utils.get(guild.text_channels, name="❓qotd")
                        if channel:
                            try:
                                message = await channel.send(response)
                                await message.add_reaction("👍")
                                await message.add_reaction("👎")
                                bot.last_qotd_messages.append(message)
                                success_count += 1
                            except Exception as e:
                                logger.error(f"Failed to send makeup QOTD to #{channel.name}: {e}")
                                # Store as pending if we can't send
                                bot.pending_qotd = response
                                bot.pending_qotd_date = today
                                logger.info("Stored makeup QOTD as pending due to send failure")
                                return
                    
                    if success_count > 0:
                        bot.last_qotd_date = today
                        await save_last_qotd_date(today)
                        bot.last_qotd = response
                        logger.info(f"Successfully sent makeup QOTD to {success_count} channels")
                    else:
                        # Store as pending if no channels worked
                        bot.pending_qotd = response
                        bot.pending_qotd_date = today
                        logger.warning("Couldn't send makeup QOTD to any channels - stored as pending")
                        
                except Exception as e:
                    logger.error(f"Failed to generate makeup QOTD: {e}")
            else:
                logger.info(f"Reconnected before today's QOTD time ({today_12pm.strftime('%H:%M')} Oslo time) - will wait for scheduled time")
        else:
            logger.info(f"QOTD already sent today ({bot.last_qotd_date}) - no need to send another")
                
    except Exception as e:
        logger.error(f"Error checking pending QOTD: {e}")

async def send_qotd(bot):
    try:
        import datetime
        today = datetime.date.today()
        
        # Check if we already sent a QOTD today
        if bot.last_qotd_date == today:
            logger.info("QOTD already sent today - skipping")
            return
        
        # More comprehensive connection health checks
        if not bot.is_ready():
            logger.warning("Bot not ready - skipping QOTD generation")
            return
            
        if not bot.guilds:
            logger.warning("No guilds available - skipping QOTD generation")
            return
            
        # Test if we can actually communicate with Discord API
        try:
            # Try a simple API call to test connection
            await bot.fetch_user(bot.user.id)
        except Exception as e:
            logger.warning(f"Discord API test failed - skipping QOTD generation: {e}")
            return
        
        logger.info("Starting QOTD process")
        
        # Check if we have a pending question from a previous failed attempt
        if bot.pending_qotd and bot.pending_qotd_date:
            # Use the pending question if it's from today or yesterday (in case of overnight issues)
            days_old = (today - bot.pending_qotd_date).days
            if days_old <= 1:
                logger.info(f"Using pending QOTD from {bot.pending_qotd_date} (was generated but never sent)")
                response = bot.pending_qotd
                bot.pending_qotd = None  # Clear it since we're using it
                bot.pending_qotd_date = None
            else:
                logger.info(f"Discarding old pending QOTD from {bot.pending_qotd_date} (too old)")
                bot.pending_qotd = None
                bot.pending_qotd_date = None
                response = await generate_new_qotd()
        else:
            # Generate a new question
            response = await generate_new_qotd()
        
        bot.last_qotd = response
        
        # Clear previous message references
        bot.last_qotd_messages = []
        
        # Send to all guilds - but fail fast if there are connection issues
        success_count = 0
        for guild in bot.guilds:
            channel = discord.utils.get(guild.text_channels, name="❓qotd")
            if channel:
                try:
                    message = await channel.send(response)
                    # Add upvote and downvote reactions
                    await message.add_reaction("👍")
                    await message.add_reaction("👎")
                    # Store message for later reaction counting
                    bot.last_qotd_messages.append(message)
                    success_count += 1
                    logger.info(f"Message sent successfully to #{channel.name}")
                except Exception as e:
                    logger.error(f"Failed to send QOTD to #{channel.name} in {guild.name}: {e}")
                    # Store the question as pending since it couldn't be sent
                    bot.pending_qotd = response
                    bot.pending_qotd_date = today
                    logger.info("Stored QOTD as pending due to send failure")
                    break
                    
        if success_count > 0:
            bot.last_qotd_date = today  # Only mark as sent if actually successful
            await save_last_qotd_date(today)
            logger.info(f"QOTD sent successfully to {success_count} channels")
        else:
            logger.warning("QOTD generation completed but couldn't send to any channels - stored as pending")
        
    except Exception as e:
        logger.error(f"Error in send_qotd: {e}")

async def send_message_with_retry(channel, message, max_retries=1):
    """Send a Discord message with minimal retry to avoid flooding during connection issues."""
    try:
        # Test connection health first
        if not channel.guild.me or not channel.permissions_for(channel.guild.me).send_messages:
            logger.error(f"No permission to send message to #{channel.name}")
            return
            
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
    Load question history and rating data from JSON file.
    
    Data structure:
    - history: List of [question, reaction_score] pairs
        Format: [["Question text", 5]] where score = upvotes - downvotes - 1
    - highest_rated: List of top-rated questions (score >= 0) - max 15
    - lowest_rated: List of low-rated questions (score < 0) - max 15
    - last_qotd_date: Date when the last QOTD was sent (for startup checks)
    """
    import json
    import datetime
    history_path = os.path.join(os.path.dirname(__file__), "qotd_history.json")
    
    last_qotd_date = None
    
    if os.path.exists(history_path):
        with open(history_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                # Load data
                history = data.get("history", [])
                highest_rated = data.get("highest_rated", [])
                lowest_rated = data.get("lowest_rated", [])
                # Load last QOTD date and convert from string to date object
                last_qotd_date_str = data.get("last_qotd_date")
                if last_qotd_date_str:
                    try:
                        last_qotd_date = datetime.datetime.fromisoformat(last_qotd_date_str).date()
                    except (ValueError, TypeError):
                        logger.warning(f"Invalid last_qotd_date format: {last_qotd_date_str}")
                        last_qotd_date = None
            else:
                # Old format - just a list
                history = [[q, None] for q in (data if isinstance(data, list) else [])]
                highest_rated = []
                lowest_rated = []
                last_qotd_date = None
    else:
        history = []
        highest_rated = []
        lowest_rated = []
        last_qotd_date = None
    
    return history, highest_rated, lowest_rated, last_qotd_date

def update_rating_lists(history, highest_rated, lowest_rated):
    """Update highest/lowest rated lists based on reaction scores."""
    if not history:
        return highest_rated, lowest_rated
    
    # Get the last rated question
    last_question, last_score = history[-1]
    
    # If the last question has no score, do nothing
    if last_score is None:
        return highest_rated, lowest_rated
    
    # Check if this question is already in either list
    question_already_in_lists = False
    for q, _ in highest_rated + lowest_rated:
        if q == last_question:
            question_already_in_lists = True
            break
    
    # If already in lists, don't add again
    if question_already_in_lists:
        return highest_rated, lowest_rated
    
    # Check if it should go in highest_rated (score >= 0)
    if last_score >= 0:
        # Add to highest_rated if list isn't full or if this score is higher than the lowest in the list
        if len(highest_rated) < 15:
            highest_rated.append([last_question, last_score])
        else:
            # Find the lowest score in highest_rated
            lowest_in_highest = min(highest_rated, key=lambda x: x[1])
            
            if last_score > lowest_in_highest[1]:
                highest_rated.remove(lowest_in_highest)
                highest_rated.append([last_question, last_score])
    
    # Check if it should go in lowest_rated (score < 0)
    else:
        # Add to lowest_rated if list isn't full or if this score is lower than the highest in the list
        if len(lowest_rated) < 15:
            lowest_rated.append([last_question, last_score])
        else:
            # Find the highest score in lowest_rated
            highest_in_lowest = max(lowest_rated, key=lambda x: x[1])
            
            if last_score < highest_in_lowest[1]:
                lowest_rated.remove(highest_in_lowest)
                lowest_rated.append([last_question, last_score])
    
    # Sort the lists
    highest_rated.sort(key=lambda x: x[1], reverse=True)
    lowest_rated.sort(key=lambda x: x[1])
    
    logger.info(f"Added question to rating lists: '{last_question[:50]}...' with score {last_score:+d}")
    
    return highest_rated, lowest_rated

async def save_question_data(history, highest_rated=None, lowest_rated=None, last_qotd_date=None):
    """Save question history and rating data to JSON file."""
    import json
    import datetime
    history_path = os.path.join(os.path.dirname(__file__), "qotd_history.json")
    
    # Load existing data to preserve highest/lowest rated if not provided
    if highest_rated is None or lowest_rated is None:
        _, existing_highest, existing_lowest, _ = await load_question_data()
        if highest_rated is None:
            highest_rated = existing_highest
        if lowest_rated is None:
            lowest_rated = existing_lowest
    
    # No longer trim history - save all questions permanently
    
    # Convert date to string for JSON serialization
    last_qotd_date_str = None
    if last_qotd_date:
        if isinstance(last_qotd_date, datetime.date):
            last_qotd_date_str = last_qotd_date.isoformat()
        else:
            last_qotd_date_str = last_qotd_date
    
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump({
            "history": history,
            "highest_rated": highest_rated,
            "lowest_rated": lowest_rated,
            "last_qotd_date": last_qotd_date_str
        }, f, ensure_ascii=False, indent=2)
    
    return history

async def save_last_qotd_date(date):
    """Save the last QOTD date to persistent storage without affecting question data."""
    try:
        history, highest_rated, lowest_rated, _ = await load_question_data()
        await save_question_data(history, highest_rated, lowest_rated, date)
        logger.info(f"Saved last QOTD date to storage: {date}")
    except Exception as e:
        logger.error(f"Failed to save last QOTD date: {e}")

async def load_movie_data():
    """Load movie list from JSON file."""
    import json
    movie_path = os.path.join(os.path.dirname(__file__), "movie_list.json")
    
    if os.path.exists(movie_path):
        with open(movie_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("movies", [])
    else:
        return []

async def save_movie_data(movies):
    """Save movie list to JSON file."""
    import json
    movie_path = os.path.join(os.path.dirname(__file__), "movie_list.json")
    
    with open(movie_path, "w", encoding="utf-8") as f:
        json.dump({"movies": movies}, f, ensure_ascii=False, indent=2)

async def generate_new_qotd():
    """Generate a new QOTD using ChatGPT."""
    from chatgpt_api import get_chatgpt_response_with_retry
    import random
    import gc
    
    history, highest_rated, lowest_rated, _ = await load_question_data()
    
    # Calculate reaction score for the last question before generating a new one
    bot_instance = None
    for obj in gc.get_objects():
        if isinstance(obj, QOTDBot):
            bot_instance = obj
            break
    
    if bot_instance and bot_instance.last_qotd_messages and len(history) > 0:
        # Count reactions on the last question
        total_upvotes = 0
        total_downvotes = 0
        
        for message in bot_instance.last_qotd_messages:
            try:
                # Refresh message to get current reactions
                message = await message.channel.fetch_message(message.id)
                for reaction in message.reactions:
                    if str(reaction.emoji) == "👍":
                        # Subtract 1 because bot reacted too
                        total_upvotes += max(0, reaction.count - 1)
                    elif str(reaction.emoji) == "👎":
                        # Subtract 1 because bot reacted too
                        total_downvotes += max(0, reaction.count - 1)
            except Exception as e:
                logger.error(f"Failed to fetch reactions: {e}")
        
        # Calculate score: upvotes - downvotes - 1
        reaction_score = total_upvotes - total_downvotes - 1
        
        # Update the last question's rating
        if history:
            history[-1][1] = reaction_score
            logger.info(f"Updated last question rating: {reaction_score} (👍{total_upvotes} - 👎{total_downvotes} - 1)")
            
            # Update highest/lowest rated lists
            highest_rated, lowest_rated = update_rating_lists(history, highest_rated, lowest_rated)
    
    # Extract question text with ratings for recent history (last 30)
    recent_questions_formatted = []
    for question, rating_score in history[-30:]:
        if rating_score is not None:
            recent_questions_formatted.append(f"{question} (Score: {rating_score:+d})")
        else:
            recent_questions_formatted.append(f"{question} (Not rated)")
    
    recent_questions = "\n".join(recent_questions_formatted)
    
    # Check if bot has style selector enabled
    # We need to access the bot instance - get it from the running bot
    bot_instance = None
    for bot in [b for b in asyncio.all_tasks() if hasattr(b, 'get_coro')]:
        try:
            if hasattr(bot.get_coro(), 'cr_frame') and bot.get_coro().cr_frame:
                for var in bot.get_coro().cr_frame.f_locals.values():
                    if isinstance(var, QOTDBot):
                        bot_instance = var
                        break
        except:
            pass
    
    # Try alternative way to get bot instance
    if bot_instance is None:
        import gc
        for obj in gc.get_objects():
            if isinstance(obj, QOTDBot):
                bot_instance = obj
                break
    
    # Style selector logic
    style_enabled = bot_instance.style_selector_enabled if bot_instance else True
    
    if style_enabled:
        styles = [
            "funny", "nostalgic", "weird", "food-related", "social", 
            "pop-culture", "personal", "random thought", "hypothetical scenario",
            "preference-based", "sensory (sound, smell, taste)", "childhood memory",
            "embarrassing moment", "unpopular opinion", "creative"
        ]
        chosen_style = random.choice(styles)
        
        prompt = f"""
You are generating a Question of the Day (QOTD) for a Discord community in a {chosen_style} style.

### Goals
- Be unique, interesting, and quick to answer.
- Be easy and fun — avoid anything serious, philosophical, or world-changing.
- Keep it casual and conversational. No "If you could..." repetition.
- Make sure the question is not too long and natural to say in chat.
- Avoid complex multi-part questions ("what, why, and how").
- Match the {chosen_style} style while staying fun and casual.
- Don’t assume the user owns anything, goes anywhere, or has specific life experiences.

### Examples of good QOTD style
- What's your go-to snack right now?
- Would you rather never use emojis again or never send GIFs again?
- What's a smell that instantly makes you happy?
- What song always puts you in a good mood?

### Avoid repetitive question starts
Don't overuse the same question starters. Vary your approach and avoid patterns like:
- "If you could..."
- "What's your favorite..."
- "Would you rather..."
- "What's the..."
Mix up your question structures to keep things fresh and interesting.

### Recent questions to avoid repeating ideas from:
{recent_questions}

### Output format
Respond with only one question, nothing else.
Make it not too long, casual, and conversational.

### Final self-check
Before finalizing, check: “does this question make logical sense? and does it adhere to all the rules above?”
"""
    else:
        prompt = f"""
You are generating a Question of the Day (QOTD) for a Discord community.

### Goals
- Be unique, interesting, and quick to answer.
- Be easy and fun — avoid anything serious, philosophical, or world-changing.
- Keep it casual and conversational. No "If you could..." or other similar question repetitions.
- Make sure the question is not too long and natural to say in chat.
- Avoid complex multi-part questions ("what, why, and how").
- Don’t assume the user owns anything, goes anywhere, or has specific life experiences.

### Examples of good QOTD style
- What's your go-to snack right now?
- Would you rather never use emojis again or never send GIFs again?
- What's a smell that instantly makes you happy?
- What song always puts you in a good mood?

### Avoid repetitive question starts
Don't overuse the same question starters. Vary your approach and avoid patterns like:
- "If you could..."
- "What's your favorite..."
- "Would you rather..."
- "What's the..."
Mix up your question structures to keep things fresh and interesting.

### Recent questions to avoid repeating ideas from:
{recent_questions}

### Output format
Respond with only one question, nothing else.
Make it not too long, casual, and conversational.

### Final self-check
Before finalizing, check: “does this question make logical sense? and does it adhere to all the rules above?”
"""
    
    response = await get_chatgpt_response_with_retry(prompt)
    
    # Add new question to history with no rating data initially
    history.append([response, None])
    
    # Save the updated data
    history = await save_question_data(history, highest_rated, lowest_rated)
    
    return response

async def choose_random_movie(bot):
    """Choose a random movie from the list and post it to movie-night channels."""
    try:
        import random
        
        # Load movie list
        movies = await load_movie_data()
        
        if not movies:
            logger.info("No movies in the list to choose from")
            # Send message to movie-night channels that there are no movies
            for guild in bot.guilds:
                channel = discord.utils.get(guild.text_channels, name="movie-night")
                if channel:
                    try:
                        await channel.send("🎬 It's movie night time! But there are no movies in the list. Add some with `/addmovie`!")
                    except Exception as e:
                        logger.error(f"Failed to send no-movies message to #{channel.name}: {e}")
            return
        
        # Choose random movie
        chosen_movie = random.choice(movies)
        
        # Remove from list
        movies.remove(chosen_movie)
        await save_movie_data(movies)
        
        # Send to all movie-night channels
        for guild in bot.guilds:
            channel = discord.utils.get(guild.text_channels, name="movie-night")
            if channel:
                try:
                    await channel.send(f"🎬 **Tonight's Movie:** {chosen_movie}\n\n🍿 Enjoy the show!")
                    logger.info(f"Sent movie choice '{chosen_movie}' to #{channel.name}")
                except Exception as e:
                    logger.error(f"Failed to send movie choice to #{channel.name}: {e}")
        
        logger.info(f"Chose movie: {chosen_movie}. {len(movies)} movies remaining in list.")
        
    except Exception as e:
        logger.error(f"Error in choose_random_movie: {e}")

@discord.app_commands.command(name="ping", description="Replies with Pong!")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!")

@discord.app_commands.command(name="repeat", description="Repeats the last QOTD")
async def repeat(interaction: discord.Interaction):
    await interaction.response.defer()
    bot = interaction.client
    
    try:
        if hasattr(bot, 'last_qotd') and bot.last_qotd:
            await interaction.followup.send(bot.last_qotd)
            logger.info("Last QOTD repeated successfully")
        else:
            await interaction.followup.send("No question has been posted yet! Use wait for 12:00 PM or ask Comrade to generate a new question.")
            
    except Exception as e:
        logger.error(f"Error in repeat command: {e}")
        try:
            await interaction.followup.send("Sorry, I'm having trouble retrieving the last question right now. Please try again later.")
        except:
            logger.error("Failed to send error message to user")

@discord.app_commands.command(name="new", description="Generate a new QOTD (Comrade only)")
async def new(interaction: discord.Interaction):
    await interaction.response.defer()
    bot = interaction.client
    
    try:
        # Check if user has the "Comrade" role
        comrade_role_id = 536303389764485130
        user_role_ids = [role.id for role in interaction.user.roles]
        
        if comrade_role_id not in user_role_ids:
            await interaction.followup.send("❌ You don't have permission to use this command. Only users with the 'Comrade' role can generate new questions.")
            logger.warning(f"User {interaction.user.display_name} ({interaction.user.id}) attempted to use /new without Comrade role")
            return
        
        logger.info(f"Comrade {interaction.user.display_name} generating new QOTD")
        
        # Use the shared function to generate a new question
        response = await generate_new_qotd()
        bot.last_qotd = response
        
        # Clear previous message references
        bot.last_qotd_messages = []
        
        # Send the new question to the current channel
        await interaction.followup.send(f"🎯 **New Question of the Day:**\n{response}")
        
        # Also send to all designated QOTD channels (except current channel if it's already a QOTD channel)
        current_channel_name = interaction.channel.name if hasattr(interaction.channel, 'name') else ""
        
        for guild in bot.guilds:
            channel = discord.utils.get(guild.text_channels, name="❓qotd")
            if channel and channel.id != interaction.channel_id:  # Don't send to the same channel twice
                try:
                    message = await channel.send(response)
                    # Add reactions
                    await message.add_reaction("👍")
                    await message.add_reaction("👎")
                    # Store message for later reaction counting
                    bot.last_qotd_messages.append(message)
                except Exception as e:
                    logger.error(f"Failed to send to #{channel.name}: {e}")
        
        logger.info(f"New QOTD generated and sent by Comrade {interaction.user.display_name}")
        
    except Exception as e:
        logger.error(f"Error in new command: {e}")
        try:
            await interaction.followup.send("Sorry, I'm having trouble generating a new question right now. Please try again later.")
        except:
            logger.error("Failed to send error message to user")

@discord.app_commands.command(name="ratings", description="View highest and lowest rated questions")
async def ratings(interaction: discord.Interaction):
    await interaction.response.defer()
    
    try:
        # Load question data
        history, highest_rated, lowest_rated, _ = await load_question_data()
        
        embed = discord.Embed(title="📊 Question Ratings", color=0x00ff00)
        
        # Add highest rated questions
        if highest_rated:
            highest_text = ""
            for i, (question, score) in enumerate(highest_rated[:10]):
                highest_text += f"{i+1}. {question} (Score: {score:+d})\n"
            embed.add_field(name="🔥 Top Rated Questions", value=highest_text[:1024], inline=False)
        else:
            embed.add_field(name="🔥 Top Rated Questions", value="No rated questions yet", inline=False)
        
        # Add lowest rated questions
        if lowest_rated:
            lowest_text = ""
            for i, (question, score) in enumerate(lowest_rated[:10]):
                lowest_text += f"{i+1}. {question} (Score: {score:+d})\n"
            embed.add_field(name="👎 Lowest Rated Questions", value=lowest_text[:1024], inline=False)
        else:
            embed.add_field(name="👎 Lowest Rated Questions", value="No rated questions yet", inline=False)
        
        embed.set_footer(text="Scores = (👍 upvotes - 👎 downvotes - 1). Higher is better!")
        
        await interaction.followup.send(embed=embed)
        logger.info("Ratings list displayed successfully")
        
    except Exception as e:
        logger.error(f"Error in ratings command: {e}")
        try:
            await interaction.followup.send("Sorry, I'm having trouble retrieving the ratings right now. Please try again later.")
        except:
            logger.error("Failed to send error message to user")

@discord.app_commands.command(name="togglestyle", description="Toggle style selector on/off (Comrade only)")
async def togglestyle(interaction: discord.Interaction):
    await interaction.response.defer()
    bot = interaction.client
    
    try:
        # Check if user has the "Comrade" role
        comrade_role_id = 536303389764485130
        user_role_ids = [role.id for role in interaction.user.roles]
        
        if comrade_role_id not in user_role_ids:
            await interaction.followup.send("❌ You don't have permission to use this command. Only users with the 'Comrade' role can toggle the style selector.")
            logger.warning(f"User {interaction.user.display_name} ({interaction.user.id}) attempted to use /togglestyle without Comrade role")
            return
        
        # Toggle the style selector
        bot.style_selector_enabled = not bot.style_selector_enabled
        
        status = "enabled ✅" if bot.style_selector_enabled else "disabled ❌"
        await interaction.followup.send(f"Style selector is now **{status}**")
        logger.info(f"Comrade {interaction.user.display_name} toggled style selector to: {bot.style_selector_enabled}")
        
    except Exception as e:
        logger.error(f"Error in togglestyle command: {e}")
        try:
            await interaction.followup.send("Sorry, I'm having trouble toggling the style selector right now. Please try again later.")
        except:
            logger.error("Failed to send error message to user")

@discord.app_commands.command(name="addmovie", description="Add a movie to the movie night list")
@discord.app_commands.describe(movie="The name of the movie to add")
async def addmovie(interaction: discord.Interaction, movie: str):
    await interaction.response.defer()
    
    try:
        # Load current movie list
        movies = await load_movie_data()
        
        # Check if movie already exists (case-insensitive)
        if any(m.lower() == movie.lower() for m in movies):
            await interaction.followup.send(f"❌ '{movie}' is already in the list!")
            return
        
        # Add movie to list
        movies.append(movie)
        await save_movie_data(movies)
        
        await interaction.followup.send(f"✅ Added '{movie}' to the movie list! ({len(movies)} movies total)")
        logger.info(f"User {interaction.user.display_name} added movie: {movie}")
        
    except Exception as e:
        logger.error(f"Error in addmovie command: {e}")
        try:
            await interaction.followup.send("Sorry, I'm having trouble adding the movie right now. Please try again later.")
        except:
            logger.error("Failed to send error message to user")

@discord.app_commands.command(name="choosemovie", description="Choose a random movie from the list")
async def choosemovie(interaction: discord.Interaction):
    await interaction.response.defer()
    
    try:
        import random
        
        # Load movie list
        movies = await load_movie_data()
        
        if not movies:
            await interaction.followup.send("❌ The movie list is empty! Add some movies with `/addmovie` first.")
            return
        
        # Choose random movie
        chosen_movie = random.choice(movies)
        
        # Remove from list
        movies.remove(chosen_movie)
        await save_movie_data(movies)
        
        await interaction.followup.send(f"🎬 **Tonight's Movie:** {chosen_movie}\n\n🍿 Enjoy the show!\n\n({len(movies)} movies remaining in list)")
        logger.info(f"User {interaction.user.display_name} chose movie: {chosen_movie}")
        
    except Exception as e:
        logger.error(f"Error in choosemovie command: {e}")
        try:
            await interaction.followup.send("Sorry, I'm having trouble choosing a movie right now. Please try again later.")
        except:
            logger.error("Failed to send error message to user")
        
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
        _, _, _, persistent_last_qotd_date = await load_question_data()
        if persistent_last_qotd_date:
            bot.last_qotd_date = persistent_last_qotd_date
            logger.info(f"Loaded last QOTD date from storage: {persistent_last_qotd_date}")
        else:
            logger.info("No last QOTD date found in storage")
    except Exception as e:
        logger.error(f"Failed to load last QOTD date: {e}")
        
    # Only start scheduler once, and don't restart on reconnections
    if not bot.scheduler_started:
        scheduler = AsyncIOScheduler(timezone=pytz.timezone('Europe/Oslo'))
        # Daily QOTD at 12:00 PM CET
        scheduler.add_job(send_qotd, CronTrigger(hour=12, minute=0), args=[bot])
        # Movie night every Friday at 12:00 PM CET
        scheduler.add_job(choose_random_movie, CronTrigger(day_of_week='fri', hour=12, minute=0), args=[bot])
        scheduler.start()
        bot.scheduler_started = True
        logger.info("Scheduler started successfully (QOTD daily + Movie night Fridays)")
    else:
        logger.info("Scheduler already running - not restarting")
    
    # Check if we have a pending QOTD to send after reconnection
    await check_pending_qotd(bot)

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
