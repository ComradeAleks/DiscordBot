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

    async def setup_hook(self):
        self.tree.add_command(ping)
        self.tree.add_command(repeat)
        self.tree.add_command(rate)
        self.tree.add_command(new)
        self.tree.add_command(ratings)

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
                
                success_count = 0
                for guild in bot.guilds:
                    channel = discord.utils.get(guild.text_channels, name="❓qotd")
                    if channel:
                        try:
                            await send_message_with_retry(channel, bot.pending_qotd)
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
                    
                    success_count = 0
                    for guild in bot.guilds:
                        channel = discord.utils.get(guild.text_channels, name="❓qotd")
                        if channel:
                            try:
                                await send_message_with_retry(channel, response)
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
        
        # Send to all guilds - but fail fast if there are connection issues
        success_count = 0
        for guild in bot.guilds:
            channel = discord.utils.get(guild.text_channels, name="❓qotd")
            if channel:
                try:
                    await send_message_with_retry(channel, response)
                    success_count += 1
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
    
    New data structure:
    - history: List of [question, rating_metadata] pairs
        Format: [["Question text", {"total_score": 15, "vote_count": 2, "voters": ["user1", "user2"]}]]
    - highest_rated: List of top-rated questions (rating >= 5.0) - max 15
    - lowest_rated: List of low-rated questions (rating < 5.0) - max 15
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
                # Check if we have old format and need to convert
                if "rating_data" in data:
                    # Convert from old format to new format
                    history = convert_old_format_to_new(data.get("history", []), data.get("rating_data", {}))
                    highest_rated = data.get("highest_rated", [])
                    lowest_rated = data.get("lowest_rated", [])
                    # Convert string-based lists to new format too
                    highest_rated = convert_rating_lists_to_new_format(highest_rated)
                    lowest_rated = convert_rating_lists_to_new_format(lowest_rated)
                    # No last_qotd_date in old format
                    last_qotd_date = None
                else:
                    # New format
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
                # Very old format - just a list
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

def convert_old_format_to_new(old_history, old_rating_data):
    """Convert old separate history and rating_data to new combined format."""
    import re
    new_history = []
    rating_pattern = r" Rated: [\d.]+(?:/10)?"
    
    for i, question in enumerate(old_history):
        # Remove rating text to get clean question
        clean_question = re.sub(rating_pattern, "", question)
        
        # Get rating data if it exists
        rating_metadata = old_rating_data.get(str(i))
        
        new_history.append([clean_question, rating_metadata])
    
    return new_history

def convert_rating_lists_to_new_format(old_list):
    """Convert old string-based rating lists to new format."""
    import re
    new_list = []
    
    for item in old_list:
        if isinstance(item, str):
            # Extract question and rating info from string like "Question (Rating: 7.5/10, 2 votes)"
            match = re.match(r"(.+) \(Rating: ([\d.]+)/10, (\d+) votes?\)", item)
            if match:
                question = match.group(1)
                rating = float(match.group(2))
                votes = int(match.group(3))
                
                # Create rating metadata
                rating_metadata = {
                    "total_score": int(rating * votes),
                    "vote_count": votes,
                    "voters": []  # We can't recover voter IDs from old format
                }
                
                new_list.append([question, rating_metadata])
    
    return new_list

def update_rating_lists_on_new_question(history, highest_rated, lowest_rated):
    """Update highest/lowest rated lists when a new question is generated (not when rating)."""
    if len(history) < 2:
        return highest_rated, lowest_rated  # Need at least 2 questions (previous + new)
    
    # Get the PREVIOUSLY asked question (second to last, since last is the new unrated question)
    previous_question, previous_rating = history[-2]
    
    # If the previous question has no rating data, do nothing
    if not previous_rating or previous_rating["vote_count"] < 1:
        return highest_rated, lowest_rated
    
    # Check if this question is already in either list
    question_already_in_lists = False
    for q, _ in highest_rated + lowest_rated:
        if q == previous_question:
            question_already_in_lists = True
            break
    
    # If already in lists, don't add again
    if question_already_in_lists:
        return highest_rated, lowest_rated
    
    average_rating = previous_rating["total_score"] / previous_rating["vote_count"]
    
    # Check if it should go in highest_rated (>= 5.0)
    if average_rating >= 5.0:
        # Add to highest_rated if list isn't full or if this rating is higher than the lowest in the list
        if len(highest_rated) < 15:
            highest_rated.append([previous_question, previous_rating])
        else:
            # Find the lowest rating in highest_rated
            lowest_in_highest = min(highest_rated, key=lambda x: x[1]["total_score"] / x[1]["vote_count"])
            lowest_rating = lowest_in_highest[1]["total_score"] / lowest_in_highest[1]["vote_count"]
            
            if average_rating > lowest_rating:
                highest_rated.remove(lowest_in_highest)
                highest_rated.append([previous_question, previous_rating])
    
    # Check if it should go in lowest_rated (< 5.0)
    elif average_rating < 5.0:
        # Add to lowest_rated if list isn't full or if this rating is lower than the highest in the list
        if len(lowest_rated) < 15:
            lowest_rated.append([previous_question, previous_rating])
        else:
            # Find the highest rating in lowest_rated
            highest_in_lowest = max(lowest_rated, key=lambda x: x[1]["total_score"] / x[1]["vote_count"])
            highest_rating = highest_in_lowest[1]["total_score"] / highest_in_lowest[1]["vote_count"]
            
            if average_rating < highest_rating:
                lowest_rated.remove(highest_in_lowest)
                lowest_rated.append([previous_question, previous_rating])
    
    # Sort the lists
    highest_rated.sort(key=lambda x: x[1]["total_score"] / x[1]["vote_count"], reverse=True)
    lowest_rated.sort(key=lambda x: x[1]["total_score"] / x[1]["vote_count"])
    
    logger.info(f"Added previous question to rating lists: '{previous_question[:50]}...' with rating {average_rating:.1f}")
    
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
    
    # Keep only the last 31 questions
    if len(history) > 31:
        history = history[-31:]
        logger.info(f"Trimmed history to last 31 questions")
    
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

async def generate_new_qotd():
    """Generate a new QOTD using ChatGPT."""
    from chatgpt_api import get_chatgpt_response_with_retry
    
    history, highest_rated, lowest_rated, _ = await load_question_data()
    
    # Extract just the question text for recent history
    recent_questions = [q[0] for q in history[-31:]]
    
    prompt = "Your task is to give me a question of the day (QOTD) Follow these rules: "
    prompt += "1. Each question should be unique, no similar theme. "
    prompt += "2. All questions should have varying structure and wording and theme. "
    prompt += "3. Questions should be simple and fun to answer (no economics, no death, no boring things). "
    prompt += "4. Only reply with the question, nothing else. "
    prompt += "5. Do not write questions with the same theme or structure AT ALL, as the lower rated questions provided under."
    prompt += "These are the previous asked questions of the past month: "
    prompt += "; ".join(recent_questions) + ". "
    
    # Add highest rated questions as examples of good formats
    if highest_rated:
        high_rated_questions = [q[0] for q in highest_rated[:10]]  # Top 10
        prompt += "Here are examples of highly rated questions (use these as inspiration for structure and style): "
        prompt += "; ".join(high_rated_questions) + ". "
    
    # Add lowest rated questions as examples to avoid
    if lowest_rated:
        low_rated_questions = [q[0] for q in lowest_rated[:10]]  # Bottom 10
        prompt += "AVOID these question formats and styles (these were poorly rated): "
        prompt += "; ".join(low_rated_questions) + ". "
    
    prompt += "questions might be rated on a scale from 1 to 10 on how good they are, ask questions more aligning with 8-10 in structure."
    
    response = await get_chatgpt_response_with_retry(prompt)
    
    # Add new question to history with no rating data initially
    history.append([response, None])
    
    # AFTER adding the new question: Update highest/lowest lists with the previously asked question
    highest_rated, lowest_rated = update_rating_lists_on_new_question(history, highest_rated, lowest_rated)
    
    # Save the updated data
    history = await save_question_data(history, highest_rated, lowest_rated)
    
    return response

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
        
        # Send the new question to the current channel
        await interaction.followup.send(f"🎯 **New Question of the Day:**\n{response}")
        
        # Also send to all designated QOTD channels (except current channel if it's already a QOTD channel)
        current_channel_name = interaction.channel.name if hasattr(interaction.channel, 'name') else ""
        
        for guild in bot.guilds:
            channel = discord.utils.get(guild.text_channels, name="❓qotd")
            if channel and channel.id != interaction.channel_id:  # Don't send to the same channel twice
                await send_message_with_retry(channel, response)
        
        logger.info(f"New QOTD generated and sent by Comrade {interaction.user.display_name}")
        
    except Exception as e:
        logger.error(f"Error in new command: {e}")
        try:
            await interaction.followup.send("Sorry, I'm having trouble generating a new question right now. Please try again later.")
        except:
            logger.error("Failed to send error message to user")

@discord.app_commands.command(name="rate", description="Rate the last QOTD with a number from 1-10")
@discord.app_commands.describe(rating="Rating from 1 to 10 for the last QOTD")
async def rate(interaction: discord.Interaction, rating: int):
    await interaction.response.defer()
    bot = interaction.client
    
    try:
        # Validate rating range
        if rating < 1 or rating > 10:
            await interaction.followup.send("Please provide a rating between 1 and 10.")
            return
            
        if not hasattr(bot, 'last_qotd') or not bot.last_qotd:
            await interaction.followup.send("No question has been posted yet to rate!")
            return
            
        import json
        import re
        
        logger.info(f"User {interaction.user.display_name} rating QOTD with {rating}")
        
        # Load question data using shared function
        history, highest_rated, lowest_rated, _ = await load_question_data()
            
        if not history:
            await interaction.followup.send("No questions in history to rate!")
            return
            
        # Get the last question
        last_question, last_rating = history[-1]
        
        # Initialize rating data if it doesn't exist
        if last_rating is None:
            last_rating = {
                "total_score": 0,
                "vote_count": 0,
                "voters": []
            }
        
        # Check if this user has already voted
        user_id = str(interaction.user.id)
        if user_id in last_rating["voters"]:
            await interaction.followup.send("You have already rated this question! Each person can only rate once.")
            return
        
        # Add the new rating
        last_rating["total_score"] += rating
        last_rating["vote_count"] += 1
        last_rating["voters"].append(user_id)
        
        # Update the question in history
        history[-1] = [last_question, last_rating]
        
        # Calculate average for display
        average_rating = last_rating["total_score"] / last_rating["vote_count"]
        vote_count = last_rating["vote_count"]
        
        # Save updated history
        await save_question_data(history, highest_rated, lowest_rated)
        
        # Update bot's last question with rating display
        if vote_count == 1:
            display_question = f"{last_question} Rated: {rating}/10"
        else:
            display_question = f"{last_question} Rated: {average_rating:.1f}/10"
        
        bot.last_qotd = display_question
            
        if vote_count == 1:
            await interaction.followup.send(f"Thank you for rating! You gave this question a {rating}/10")
        else:
            await interaction.followup.send(f"Thank you for rating! You gave this question a {rating}/10. The average is now {average_rating:.1f}/10 ({vote_count} votes)")
            
        logger.info(f"QOTD rating updated successfully - New average: {average_rating:.1f} from {vote_count} votes")
        
    except Exception as e:
        logger.error(f"Error in rate command: {e}")
        try:
            await interaction.followup.send("Sorry, I'm having trouble processing your rating right now. Please try again later.")
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
            for i, (question, rating_data) in enumerate(highest_rated[:10]):
                avg_rating = rating_data["total_score"] / rating_data["vote_count"]
                highest_text += f"{i+1}. {question} (Rating: {avg_rating:.1f}/10, {rating_data['vote_count']} votes)\n"
            embed.add_field(name="🔥 Top Rated Questions", value=highest_text[:1024], inline=False)
        else:
            embed.add_field(name="🔥 Top Rated Questions", value="No rated questions yet", inline=False)
        
        # Add lowest rated questions
        if lowest_rated:
            lowest_text = ""
            for i, (question, rating_data) in enumerate(lowest_rated[:10]):
                avg_rating = rating_data["total_score"] / rating_data["vote_count"]
                lowest_text += f"{i+1}. {question} (Rating: {avg_rating:.1f}/10, {rating_data['vote_count']} votes)\n"
            embed.add_field(name="👎 Lowest Rated Questions", value=lowest_text[:1024], inline=False)
        else:
            embed.add_field(name="👎 Lowest Rated Questions", value="No rated questions yet", inline=False)
        
        embed.set_footer(text="All questions with at least 1 vote are included in these lists")
        
        await interaction.followup.send(embed=embed)
        logger.info("Ratings list displayed successfully")
        
    except Exception as e:
        logger.error(f"Error in ratings command: {e}")
        try:
            await interaction.followup.send("Sorry, I'm having trouble retrieving the ratings right now. Please try again later.")
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
        scheduler.add_job(send_qotd, CronTrigger(hour=12, minute=0), args=[bot])
        scheduler.start()
        bot.scheduler_started = True
        logger.info("Scheduler started successfully")
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
