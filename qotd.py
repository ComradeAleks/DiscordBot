import discord
from discord.ext import commands
from discord import ui
import logging
import os
import json
import datetime
import random
import gc
import asyncio
import re

logger = logging.getLogger(__name__)


def load_qotd_prompt():
    """Load the QOTD prompt template from file."""
    prompt_path = os.path.join(os.path.dirname(__file__), "qotd_prompt.txt")
    
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    else:
        logger.error("QOTD prompt file not found!")
        return None


def save_qotd_prompt(prompt_text):
    """Save the QOTD prompt template to file."""
    prompt_path = os.path.join(os.path.dirname(__file__), "qotd_prompt.txt")
    
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(prompt_text)
    
    logger.info("QOTD prompt saved successfully")


def load_qotd_styles():
    """Load the QOTD styles list from file."""
    styles_path = os.path.join(os.path.dirname(__file__), "qotd_styles.json")
    
    if os.path.exists(styles_path):
        with open(styles_path, "r", encoding="utf-8") as f:
            return json.load(f)
    else:
        logger.error("QOTD styles file not found!")
        # Return default styles as fallback
        return [
            "funny", "nostalgic", "weird", "food-related", "social", 
            "pop-culture", "personal", "random thought", "hypothetical scenario",
            "preference-based", "sensory (sound, smell, taste)", "childhood memory",
            "embarrassing moment", "unpopular opinion", "creative"
        ]


def save_qotd_styles(styles_list):
    """Save the QOTD styles list to file."""
    styles_path = os.path.join(os.path.dirname(__file__), "qotd_styles.json")
    
    with open(styles_path, "w", encoding="utf-8") as f:
        json.dump(styles_list, f, indent=2, ensure_ascii=False)
    
    logger.info("QOTD styles saved successfully")


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
                
                success_count = 0
                for guild in bot.guilds:
                    try:
                        channel = discord.utils.get(guild.channels, name=qotd_channel_name)
                        if channel:
                            await send_message_with_retry(channel, bot.pending_qotd)
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
                response = await generate_new_qotd()
                logger.info(f"Generated new QOTD: {response[:50]}...")
                
                bot.last_qotd = response
                bot.last_qotd_messages = []
                
                success_count = 0
                for guild in bot.guilds:
                    try:
                        channel = discord.utils.get(guild.channels, name=qotd_channel_name)
                        if channel:
                            await send_message_with_retry(channel, response)
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
                    bot.pending_qotd = response
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
            response = bot.pending_qotd
            bot.pending_qotd = None
            bot.pending_qotd_date = None
        else:
            logger.info("Generating new QOTD")
            response = await generate_new_qotd()
        
        bot.last_qotd = response
        
        # Clear previous message references
        bot.last_qotd_messages = []
        
        # Send to all guilds - but fail fast if there are connection issues
        success_count = 0
        for guild in bot.guilds:
            try:
                channel = discord.utils.get(guild.channels, name=qotd_channel_name)
                if channel:
                    await send_message_with_retry(channel, response)
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
            bot.pending_qotd = response
            bot.pending_qotd_date = today
        
    except Exception as e:
        logger.error(f"Error in send_qotd: {e}")


async def send_message_with_retry(channel, message, max_retries=1):
    """Send a Discord message with minimal retry to avoid flooding during connection issues."""
    try:
        # Test connection health first
        if not channel.guild.me or not channel.permissions_for(channel.guild.me).send_messages:
            logger.error(f"No permission to send messages to #{channel.name}")
            raise discord.Forbidden(None, "No permission to send messages")
            
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
    - history: List of [question, rating_percentage, upvotes, downvotes] tuples
        Format: [["Question text", 75.5, 3, 1]] where percentage = (upvotes - downvotes) / (upvotes + downvotes) * 100
        Range: -100% (all downvotes) to 100% (all upvotes), 0% if no reactions
    - highest_rated: List of top-rated questions (percentage > 10) - max 15
    - lowest_rated: List of low-rated questions (percentage <= 10) - max 15
    - last_qotd_date: Date when the last QOTD was sent (for startup checks)
    """
    logger.info("Loading question data")
    
    history_path = os.path.join(os.path.dirname(__file__), "qotd_history.json")
    
    last_qotd_date = None
    
    if os.path.exists(history_path):
        with open(history_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            raw_history = data.get("history", [])
            
            # Normalize history to new format [question, percentage, upvotes, downvotes]
            history = []
            for item in raw_history:
                if len(item) == 2:  # Old format: [question, percentage]
                    history.append([item[0], item[1], 0, 0])  # Add placeholder vote counts
                elif len(item) == 4:  # New format: [question, percentage, upvotes, downvotes]
                    history.append(item)
                else:
                    history.append([item[0], None, 0, 0])  # Fallback
            
            highest_rated = data.get("highest_rated", [])
            lowest_rated = data.get("lowest_rated", [])
            last_qotd_date_str = data.get("last_qotd_date")
            
            # Parse the last_qotd_date string
            if last_qotd_date_str:
                try:
                    last_qotd_date = datetime.datetime.strptime(last_qotd_date_str, "%Y-%m-%d").date()
                except ValueError:
                    logger.warning(f"Invalid date format in history file: {last_qotd_date_str}")
                    last_qotd_date = None
    else:
        history = []
        highest_rated = []
        lowest_rated = []
        last_qotd_date = None
    
    return history, highest_rated, lowest_rated, last_qotd_date


def update_rating_lists(history, highest_rated, lowest_rated):
    """Update highest/lowest rated lists based on reaction percentages with vote count tiebreaker."""
    if not history:
        return
    
    # Get the last rated question
    last_item = history[-1]
    last_question = last_item[0]
    last_percentage = last_item[1] if len(last_item) > 1 else None
    last_upvotes = last_item[2] if len(last_item) > 2 else 0
    last_downvotes = last_item[3] if len(last_item) > 3 else 0
    
    # If the last question has no percentage, do nothing
    if last_percentage is None:
        return
    
    # Check if this question is already in either list
    question_already_in_lists = False
    for item in highest_rated + lowest_rated:
        if item[0] == last_question:
            question_already_in_lists = True
            break
    
    # If already in lists, don't add again
    if question_already_in_lists:
        return
    
    # Create entry with all data
    entry = [last_question, last_percentage, last_upvotes, last_downvotes]
    
    # Check if it should go in highest_rated (percentage > 10)
    if last_percentage > 10:
        # Add to highest_rated if list isn't full or if this percentage is higher than the lowest in the list
        if len(highest_rated) < 15:
            highest_rated.append(entry)
        else:
            # Compare with worst in list
            worst = highest_rated[-1]
            worst_percentage = worst[1] if len(worst) > 1 else 0
            worst_upvotes = worst[2] if len(worst) > 2 else 0
            
            if last_percentage > worst_percentage or (last_percentage == worst_percentage and last_upvotes > worst_upvotes):
                highest_rated[-1] = entry
            
    
    # Check if it should go in lowest_rated (percentage <= 10)
    else:
        # Add to lowest_rated if list isn't full or if this percentage is lower than the highest in the list
        if len(lowest_rated) < 15:
            lowest_rated.append(entry)
        else:
            # Compare with best in list
            best = lowest_rated[-1]
            best_percentage = best[1] if len(best) > 1 else 0
            best_downvotes = best[3] if len(best) > 3 else 0
            
            if last_percentage < best_percentage or (last_percentage == best_percentage and last_downvotes > best_downvotes):
                lowest_rated[-1] = entry
            
    
    # Sort the lists with tiebreaker
    # For highest_rated: sort by percentage (desc), then by upvotes (desc)
    highest_rated.sort(key=lambda x: (-x[1], -(x[2] if len(x) > 2 else 0)))
    # For lowest_rated: sort by percentage (asc), then by downvotes (desc) 
    lowest_rated.sort(key=lambda x: (x[1], -(x[3] if len(x) > 3 else 0)))
    
    logger.info(f"Added question to rating lists: '{last_question[:50]}...' with rating {last_percentage:.1f}% ({last_upvotes}👍 {last_downvotes}👎)")
    
    return highest_rated, lowest_rated


async def save_question_data(history, highest_rated=None, lowest_rated=None, last_qotd_date=None):
    """Save question history and rating data to JSON file."""
    logger.info("Saving question data")
    
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
            last_qotd_date_str = last_qotd_date.strftime("%Y-%m-%d")
        else:
            last_qotd_date_str = last_qotd_date
    
    # Custom JSON formatting to keep arrays on single lines
    data = {
        "history": history,
        "highest_rated": highest_rated,
        "lowest_rated": lowest_rated,
        "last_qotd_date": last_qotd_date_str
    }
    
    # Build JSON with compact arrays
    lines = ["{"]
    
    # History
    lines.append('  "history": [')
    for i, item in enumerate(history):
        comma = "," if i < len(history) - 1 else ""
        lines.append(f"    {json.dumps(item, ensure_ascii=False)}{comma}")
    lines.append("  ],")
    
    # Highest rated
    lines.append('  "highest_rated": [')
    for i, item in enumerate(highest_rated):
        comma = "," if i < len(highest_rated) - 1 else ""
        lines.append(f"    {json.dumps(item, ensure_ascii=False)}{comma}")
    lines.append("  ],")
    
    # Lowest rated
    lines.append('  "lowest_rated": [')
    for i, item in enumerate(lowest_rated):
        comma = "," if i < len(lowest_rated) - 1 else ""
        lines.append(f"    {json.dumps(item, ensure_ascii=False)}{comma}")
    lines.append("  ],")
    
    # Last QOTD date
    lines.append(f'  "last_qotd_date": {json.dumps(last_qotd_date_str)}')
    lines.append("}")
    
    with open(history_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    
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
    """Generate a new QOTD using Grok."""
    from grok_api import get_grok_response_with_retry
    
    logger.info("Generating new QOTD")
    
    history, highest_rated, lowest_rated, _ = await load_question_data()
    
    # Calculate reaction score for the last question before generating a new one
    bot_instance = None
    for obj in gc.get_objects():
        if isinstance(obj, commands.Bot):
            bot_instance = obj
            break
    
    if bot_instance and bot_instance.last_qotd_messages and len(history) > 0:
        # Count reactions on the last question (excluding bot's own reactions)
        total_upvotes = 0
        total_downvotes = 0
        
        for message in bot_instance.last_qotd_messages:
            try:
                # Refresh the message to get current reactions
                refreshed_message = await message.channel.fetch_message(message.id)
                for reaction in refreshed_message.reactions:
                    if str(reaction.emoji) == "👍":
                        # Subtract 1 if bot reacted (to exclude bot's reaction)
                        count = reaction.count
                        if reaction.me:
                            count -= 1
                        total_upvotes += count
                    elif str(reaction.emoji) == "👎":
                        # Subtract 1 if bot reacted (to exclude bot's reaction)
                        count = reaction.count
                        if reaction.me:
                            count -= 1
                        total_downvotes += count
            except Exception as e:
                logger.error(f"Failed to fetch reactions for message: {e}")
        
        # Calculate percentage: (upvotes - downvotes) / (upvotes + downvotes) * 100
        total_reactions = total_upvotes + total_downvotes
        if total_reactions > 0:
            rating_percentage = ((total_upvotes - total_downvotes) / total_reactions) * 100
        else:
            rating_percentage = 0.0
        
        # Update the last question's rating
        if history:
            history[-1][1] = rating_percentage
            history[-1][2] = total_upvotes
            history[-1][3] = total_downvotes
            
            # Update rating lists
            highest_rated, lowest_rated = update_rating_lists(history, highest_rated, lowest_rated)
            
            # Save updated history with ratings
            await save_question_data(history, highest_rated, lowest_rated)
            
            logger.info(f"Updated last QOTD rating: {rating_percentage:.1f}% (👍{total_upvotes} 👎{total_downvotes})")
    
    # Extract question text with ratings for recent history (last 30)
    recent_questions_formatted = []
    for item in history[-30:]:
        question = item[0]
        rating_percentage = item[1] if len(item) > 1 else None
        if rating_percentage is not None:
            recent_questions_formatted.append(f"- {question} (Rating: {rating_percentage:+.1f}%)")
        else:
            recent_questions_formatted.append(f"- {question}")
    
    recent_questions = "\n".join(recent_questions_formatted)
    
    # Load styles from file and choose one randomly
    styles = load_qotd_styles()
    if not styles:
        raise Exception("No styles available for QOTD generation")
    chosen_style = random.choice(styles)
    
    # Load prompt template from file
    prompt_template = load_qotd_prompt()
    if not prompt_template:
        raise Exception("Failed to load QOTD prompt template")
    
    # Fill in the template variables
    prompt = prompt_template.format(
        chosen_style=chosen_style,
        recent_questions=recent_questions
    )
    
    response = await get_grok_response_with_retry(prompt)
    
    # Add new question to history with no rating data initially [question, percentage, upvotes, downvotes]
    history.append([response, None, 0, 0])
    
    # Save the updated data
    history = await save_question_data(history, highest_rated, lowest_rated)
    
    return response


# Command definitions
@discord.app_commands.command(name="qotd-repeat", description="Repeats the last QOTD")
async def qotd_repeat(interaction: discord.Interaction):
    bot = interaction.client
    if bot.last_qotd:
        await interaction.response.send_message(bot.last_qotd, ephemeral=True)
    else:
        await interaction.response.send_message("No QOTD has been sent yet!", ephemeral=True)


@discord.app_commands.command(name="qotd-new", description="Generate a new QOTD (Authorized roles only)")
async def qotd_new(interaction: discord.Interaction):
    # Check if user has any authorized role
    from general import check_authorized
    has_authorized_role = check_authorized(interaction.user.roles)
    
    if not has_authorized_role:
        await interaction.response.send_message("Sorry, you don't have permission to use this command!", ephemeral=True)
        return
    
    await interaction.response.defer()
    bot = interaction.client
    
    try:
        response = await generate_new_qotd()
        
        # Send the question as a followup
        message = await interaction.followup.send(response, wait=True)
        
        # Add reaction emojis for voting
        await message.add_reaction("👍")
        await message.add_reaction("👎")
        
        # Track the message and update bot state
        bot.last_qotd = response
        bot.last_qotd_messages = [message]  # Clear previous and add new message
        
        logger.info(f"New QOTD generated via /new command: {response[:50]}...")
        
    except Exception as e:
        logger.error(f"Error generating new QOTD: {e}")
        await interaction.followup.send("Failed to generate a new QOTD. Please try again later.", ephemeral=True)


# Modal for changing prompt
class ChangePromptModal(ui.Modal, title="Change QOTD Prompt"):
    def __init__(self, current_prompt, prompt_view):
        super().__init__()
        self.prompt_view = prompt_view
        
        # Show first 4000 chars in the text input (Discord limit)
        self.prompt_input = ui.TextInput(
            label="QOTD Generation Prompt",
            style=discord.TextStyle.paragraph,
            default=current_prompt[:4000] if len(current_prompt) <= 4000 else current_prompt[:3997] + "...",
            required=True,
            max_length=4000
        )
        self.add_item(self.prompt_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        # Save the new prompt
        save_qotd_prompt(self.prompt_input.value)
        
        await interaction.followup.send(
            "✅ QOTD prompt updated successfully!",
            ephemeral=True
        )
        
        # Refresh the main prompt message
        await self.prompt_view.refresh_message(interaction)
        
        logger.info(f"QOTD prompt changed by {interaction.user.name}")


# Main prompt view with button
class PromptView(ui.View):
    def __init__(self, original_interaction, has_authorized_role):
        super().__init__(timeout=300)
        self.original_interaction = original_interaction
        self.has_authorized_role = has_authorized_role
        self.message = None
        
        # Remove button if user doesn't have authorized role
        if not has_authorized_role:
            self.clear_items()
    
    async def refresh_message(self, interaction: discord.Interaction):
        """Refresh the prompt display."""
        current_prompt = load_qotd_prompt()
        content = self.build_prompt_text(current_prompt)
        
        try:
            await self.message.edit(content=content, view=self)
        except:
            pass
    
    def build_prompt_text(self, current_prompt):
        """Build the prompt display text."""
        if not current_prompt:
            text = "**Current QOTD Prompt:**\n\nFailed to load prompt."
        else:
            text = f"**Current QOTD Prompt:**\n```\n{current_prompt}\n```"
        
        if self.has_authorized_role:
            text += "\n\n*Use the button below to change the prompt.*"
        
        return text
    
    @ui.button(label="Change Prompt", style=discord.ButtonStyle.primary, emoji="✏️", custom_id="prompt_change_button")
    async def change_button(self, interaction: discord.Interaction, button: ui.Button):
        current_prompt = load_qotd_prompt()
        await interaction.response.send_modal(ChangePromptModal(current_prompt, self))


@discord.app_commands.command(name="qotd-prompt", description="View or change the QOTD generation prompt")
async def qotd_prompt(interaction: discord.Interaction):
    # Check if user has any authorized role
    from general import check_authorized
    has_authorized_role = check_authorized(interaction.user.roles)
    
    try:
        current_prompt = load_qotd_prompt()
        view = PromptView(interaction, has_authorized_role)
        content = view.build_prompt_text(current_prompt)
        
        await interaction.response.send_message(content, view=view, ephemeral=True)
        view.message = await interaction.original_response()
        
    except Exception as e:
        logger.error(f"Error in qotd-prompt command: {e}")
        await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)


# Modal for adding styles
class AddStyleModal(ui.Modal, title="Add QOTD Styles"):
    def __init__(self, style_view):
        super().__init__()
        self.style_view = style_view
        
        self.style_input = ui.TextInput(
            label="Style Name(s)",
            placeholder="Enter style names (separate multiple with commas)",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        self.add_item(self.style_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        # Parse comma-separated style names
        style_names = [s.strip() for s in self.style_input.value.split(',')]
        styles_list = load_qotd_styles()
        styles_to_add = []
        already_exists = []
        
        # Validate each style name
        for name in style_names:
            if not name:
                continue
            # Check if already exists (case-insensitive)
            if any(s.lower() == name.lower() for s in styles_list):
                already_exists.append(name)
            else:
                styles_to_add.append(name)
        
        if already_exists:
            exists_list = ', '.join(f"'{n}'" for n in already_exists)
            await interaction.followup.send(
                f"❌ Already exist: {exists_list}\n\nTry again with different style names.",
                ephemeral=True
            )
            return
        
        if not styles_to_add:
            await interaction.followup.send("❌ No valid styles found in your input.", ephemeral=True)
            return
        
        # Add the styles
        for style_name in styles_to_add:
            styles_list.append(style_name)
        
        save_qotd_styles(styles_list)
        
        added_count = len(styles_to_add)
        await interaction.followup.send(
            f"✅ Added {added_count} style{'s' if added_count != 1 else ''}!\n\nTotal styles: {len(styles_list)}",
            ephemeral=True
        )
        
        # Refresh the main style list message
        await self.style_view.refresh_message(interaction)
        
        logger.info(f"{added_count} styles added by {interaction.user.name}")


# Select menu for removing styles
class RemoveStyleSelect(ui.Select):
    def __init__(self, styles_list, style_view):
        self.style_view = style_view
        self.all_styles = styles_list
        
        # Show up to 25 styles
        options = [
            discord.SelectOption(
                label=style[:100],  # Discord label limit
                value=str(i),
                description=f"Style #{i+1}"
            )
            for i, style in enumerate(styles_list[:25])
        ]
        
        if not options:
            options = [discord.SelectOption(label="No styles available", value="none")]
        
        super().__init__(
            placeholder="Select style(s) to remove",
            min_values=1,
            max_values=min(len(options), 25),
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        if self.values[0] == "none":
            await interaction.followup.send("❌ No styles available.", ephemeral=True)
            return
        
        # Get selected styles
        selected_indices = [int(v) for v in self.values]
        styles_to_remove = [self.all_styles[i] for i in selected_indices]
        
        # Remove the styles
        styles_list = load_qotd_styles()
        for style_name in styles_to_remove:
            if style_name in styles_list:
                styles_list.remove(style_name)
        
        save_qotd_styles(styles_list)
        
        removed_count = len(styles_to_remove)
        await interaction.followup.send(
            f"✅ Removed {removed_count} style{'s' if removed_count != 1 else ''}!\n\nRemaining styles: {len(styles_list)}",
            ephemeral=True
        )
        
        # Refresh the main style list message
        await self.style_view.refresh_message(interaction)
        
        # Edit the message to remove the select menu
        try:
            await interaction.message.edit(content="✅ Styles removed successfully!", view=None)
        except:
            pass
        
        logger.info(f"{removed_count} styles removed by {interaction.user.name}")


# View for remove style selection
class RemoveStyleView(ui.View):
    def __init__(self, styles_list, style_view):
        super().__init__(timeout=120)
        self.add_item(RemoveStyleSelect(styles_list, style_view))


# Main style view with buttons
class StyleView(ui.View):
    def __init__(self, original_interaction, has_authorized_role):
        super().__init__(timeout=300)
        self.original_interaction = original_interaction
        self.has_authorized_role = has_authorized_role
        self.message = None
        
        # Remove buttons if user doesn't have authorized role
        if not has_authorized_role:
            self.clear_items()
    
    async def refresh_message(self, interaction: discord.Interaction):
        """Refresh the style list display."""
        styles_list = load_qotd_styles()
        content = self.build_style_text(styles_list)
        
        try:
            await self.message.edit(content=content, view=self)
        except:
            pass
    
    def build_style_text(self, styles_list):
        """Build the style list display text."""
        if not styles_list:
            text = "📝 **QOTD Styles**\n\nNo styles available!"
        else:
            styles_display = "\n".join([f"{i+1}. {s}" for i, s in enumerate(styles_list)])
            text = f"**QOTD Styles ({len(styles_list)} styles):**\n```\n{styles_display}\n```"
        
        if self.has_authorized_role:
            text += "\n\n*Use the buttons below to manage styles.*"
        
        return text
    
    @ui.button(label="Add Styles", style=discord.ButtonStyle.success, emoji="➕", custom_id="style_add_button")
    async def add_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(AddStyleModal(self))
    
    @ui.button(label="Remove Styles", style=discord.ButtonStyle.danger, emoji="➖", custom_id="style_remove_button")
    async def remove_button(self, interaction: discord.Interaction, button: ui.Button):
        styles_list = load_qotd_styles()
        
        if not styles_list:
            await interaction.response.send_message("❌ No styles available to remove!", ephemeral=True)
            return
        
        if len(styles_list) > 25:
            await interaction.response.send_message(
                f"⚠️ You have {len(styles_list)} styles, but can only view 25 at a time in the menu.\n\n"
                f"Consider removing styles in batches. Showing first 25:",
                ephemeral=True
            )
            # Still show the view
            view = RemoveStyleView(styles_list, self)
            await interaction.followup.send("Select styles to remove:", view=view, ephemeral=True)
        else:
            view = RemoveStyleView(styles_list, self)
            await interaction.response.send_message("Select style(s) to remove:", view=view, ephemeral=True)


@discord.app_commands.command(name="qotd-style", description="View or manage QOTD styles")
async def qotd_style(interaction: discord.Interaction):
    # Check if user has any authorized role
    from general import check_authorized
    has_authorized_role = check_authorized(interaction.user.roles)
    
    try:
        styles_list = load_qotd_styles()
        view = StyleView(interaction, has_authorized_role)
        content = view.build_style_text(styles_list)
        
        await interaction.response.send_message(content, view=view, ephemeral=True)
        view.message = await interaction.original_response()
        
    except Exception as e:
        logger.error(f"Error in qotd-style command: {e}")
        await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)


@discord.app_commands.command(name="qotd-ratings", description="View highest and lowest rated questions")
async def qotd_ratings(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    try:
        history, highest_rated, lowest_rated, _ = await load_question_data()
        
        # Build response
        response = "**📊 Question Ratings**\n\n"
        
        if highest_rated:
            response += "**🏆 Highest Rated Questions:**\n"
            for i, item in enumerate(highest_rated[:10], 1):
                question = item[0]
                percentage = item[1] if len(item) > 1 else 0
                upvotes = item[2] if len(item) > 2 else 0
                downvotes = item[3] if len(item) > 3 else 0
                # Truncate long questions
                q_display = question if len(question) <= 70 else question[:67] + "..."
                response += f"{i}. {q_display} `({percentage:+.1f}% | 👍{upvotes} 👎{downvotes})`\n"
        else:
            response += "**🏆 Highest Rated Questions:**\nNo rated questions yet!\n"
        
        response += "\n"
        
        if lowest_rated:
            response += "**💀 Mediocre & Worst Questions:**\n"
            for i, item in enumerate(lowest_rated[:10], 1):
                question = item[0]
                percentage = item[1] if len(item) > 1 else 0
                upvotes = item[2] if len(item) > 2 else 0
                downvotes = item[3] if len(item) > 3 else 0
                # Truncate long questions
                q_display = question if len(question) <= 70 else question[:67] + "..."
                response += f"{i}. {q_display} `({percentage:+.1f}% | 👍{upvotes} 👎{downvotes})`\n"
        else:
            response += "**💀 Mediocre & Worst Questions:**\nNo rated questions yet!\n"
        
        await interaction.followup.send(response, ephemeral=True)
        
    except Exception as e:
        logger.error(f"Error fetching ratings: {e}")
        await interaction.followup.send("Failed to fetch question ratings. Please try again later.", ephemeral=True)
