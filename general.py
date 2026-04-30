import discord
from discord.ext import commands
from discord import ui
import logging
import os
import json

logger = logging.getLogger(__name__)


def load_settings():
    """Load bot settings from JSON file."""
    settings_path = os.path.join(os.path.dirname(__file__), "settings.json")
    
    # Default settings
    default_settings = {
        "qotd": {
            "channel": "❓qotd",
            "time": {"hour": 12, "minute": 0},
            "days": [0, 1, 2, 3, 4, 5, 6]  # 0=Monday, 6=Sunday
        },
        "movie": {
            "channel": "movie-night",
            "notification_time": {"hour": 12, "minute": 0},
            "notification_day": [4],  # 4=Friday
            "watch_time": {"hour": 19, "minute": 0},
            "watch_day": [5]  # 5=Saturday
        },
        "authorized_roles": ["comrade"]  # Roles that can use settings (case-insensitive)
    }
    
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load settings: {e}")
            return default_settings
    else:
        # Create default settings file
        save_settings(default_settings)
        return default_settings


def save_settings(settings):
    """Save bot settings to JSON file."""
    settings_path = os.path.join(os.path.dirname(__file__), "settings.json")
    
    try:
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
        logger.info("Settings saved successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")
        return False


def format_days(day_indices):
    """Format day indices to readable string."""
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    if len(day_indices) == 7:
        return "Every day"
    return ", ".join([day_names[i] for i in sorted(day_indices)])


def format_time(time_dict):
    """Format time dict to readable string."""
    return f"{time_dict['hour']:02d}:{time_dict['minute']:02d}"


def check_authorized(user_roles):
    """Check if user has any authorized role."""
    settings = load_settings()
    authorized_roles = settings.get("authorized_roles", ["comrade"])
    
    # Convert to lowercase for case-insensitive comparison
    authorized_roles_lower = [r.lower() for r in authorized_roles]
    user_roles_lower = [role.name.lower() for role in user_roles]
    
    return any(role in authorized_roles_lower for role in user_roles_lower)


def reload_scheduler(bot):
    """Reload scheduler jobs with updated settings."""
    try:
        from apscheduler.triggers.cron import CronTrigger
        import qotd
        import movies
        
        if not bot.scheduler or not bot.scheduler_started:
            logger.error("Scheduler not initialized")
            return False
        
        # Load current settings
        settings = load_settings()
        qotd_settings = settings.get("qotd", {})
        movie_settings = settings.get("movie", {})
        
        # Update QOTD job
        qotd_time = qotd_settings.get("time", {"hour": 12, "minute": 0})
        qotd_days = qotd_settings.get("days", [0, 1, 2, 3, 4, 5, 6])
        qotd_days_str = ','.join(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'][i] for i in qotd_days)
        
        bot.scheduler.reschedule_job(
            bot.qotd_job_id,
            trigger=CronTrigger(day_of_week=qotd_days_str, hour=qotd_time['hour'], minute=qotd_time['minute'])
        )
        
        # Update Movie job (use notification time/day for when to select the movie)
        movie_notification_time = movie_settings.get("notification_time", {"hour": 12, "minute": 0})
        movie_notification_days = movie_settings.get("notification_day", [4])
        movie_notification_days_str = ','.join(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'][i] for i in movie_notification_days)
        
        bot.scheduler.reschedule_job(
            bot.movie_job_id,
            trigger=CronTrigger(day_of_week=movie_notification_days_str, hour=movie_notification_time['hour'], minute=movie_notification_time['minute'])
        )
        
        logger.info(f"Scheduler reloaded: QOTD ({format_days(qotd_days)} at {format_time(qotd_time)}), Movie Notification ({format_days(movie_notification_days)} at {format_time(movie_notification_time)})")
        return True
        
    except Exception as e:
        logger.error(f"Failed to reload scheduler: {e}")
        return False


# Modal for channel search/input
class ChannelSearchModal(ui.Modal, title="Search for Channel"):
    def __init__(self, category, guild, current_channel, settings_view):
        super().__init__()
        self.category = category
        self.guild = guild
        self.settings_view = settings_view
        
        self.channel_input = ui.TextInput(
            label="Channel Name",
            placeholder="Type channel name (partial match works)",
            default=current_channel,
            required=True,
            max_length=100
        )
        self.add_item(self.channel_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        search_term = self.channel_input.value.strip().lower()
        if search_term.startswith('#'):
            search_term = search_term[1:]
        
        # Find exact match first
        exact_match = None
        for channel in self.guild.text_channels:
            if channel.name.lower() == search_term:
                exact_match = channel
                break
        
        if exact_match:
            # Update settings with exact match
            settings = load_settings()
            settings[self.category]["channel"] = exact_match.name
            
            if save_settings(settings):
                await interaction.followup.send(
                    f"✅ Updated **{self.category.upper()} channel** to: `{exact_match.name}`",
                    ephemeral=True
                )
                # Refresh the main settings message
                await self.settings_view.refresh_message(interaction)
            else:
                await interaction.followup.send("❌ Failed to save settings.", ephemeral=True)
        else:
            # Find partial matches
            matches = []
            for channel in self.guild.text_channels:
                if search_term in channel.name.lower():
                    matches.append(channel)
            
            if matches:
                # Show matching channels
                matches = matches[:25]  # Limit to 25
                view = ChannelMatchView(self.category, matches, self.settings_view)
                match_list = "\n".join([f"• #{ch.name}" for ch in matches[:10]])
                if len(matches) > 10:
                    match_list += f"\n• ... and {len(matches) - 10} more"
                
                await interaction.followup.send(
                    f"Found {len(matches)} matching channel(s):\n{match_list}\n\nSelect one below:",
                    view=view,
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    f"❌ No channels found matching '{self.channel_input.value}'\n\nTry a different search term.",
                    ephemeral=True
                )


# Select menu for channel matches
class ChannelMatchSelect(ui.Select):
    def __init__(self, category, matches, settings_view):
        self.category = category
        self.settings_view = settings_view
        
        options = [
            discord.SelectOption(
                label=f"#{channel.name}",
                value=channel.name,
                description=f"ID: {channel.id}"
            )
            for channel in matches
        ]
        
        super().__init__(
            placeholder="Select a channel from matches",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        # Update settings
        settings = load_settings()
        settings[self.category]["channel"] = self.values[0]
        
        if save_settings(settings):
            await interaction.followup.send(
                f"✅ Updated **{self.category.upper()} channel** to: `{self.values[0]}`",
                ephemeral=True
            )
            # Refresh the main settings message
            await self.settings_view.refresh_message(interaction)
            
            # Edit the message to remove the select menu
            try:
                await interaction.message.edit(content="✅ Channel updated successfully!", view=None)
            except:
                pass
        else:
            await interaction.followup.send("❌ Failed to save settings.", ephemeral=True)


# View for channel match selection
class ChannelMatchView(ui.View):
    def __init__(self, category, matches, settings_view):
        super().__init__(timeout=120)
        self.add_item(ChannelMatchSelect(category, matches, settings_view))
# Modal for time input
class TimeModal(ui.Modal, title="Change Time"):
    def __init__(self, category, current_value, settings_view):
        super().__init__()
        self.category = category
        self.settings_view = settings_view
        
        self.time_input = ui.TextInput(
            label="Time (HH:MM format)",
            placeholder="e.g., 14:30",
            default=format_time(current_value),
            required=True,
            max_length=5
        )
        self.add_item(self.time_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        import re
        
        # Parse time format
        time_match = re.match(r'^(\d{1,2}):(\d{2})$', self.time_input.value)
        if not time_match:
            await interaction.followup.send("❌ Invalid time format. Use HH:MM (e.g., 14:30)", ephemeral=True)
            return
        
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
        
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            await interaction.followup.send("❌ Invalid time. Hour must be 0-23, minute must be 0-59.", ephemeral=True)
            return
        
        # Update settings
        settings = load_settings()
        
        # Handle different category types
        if self.category == "movie_notification":
            settings["movie"]["notification_time"] = {"hour": hour, "minute": minute}
        elif self.category == "movie_watch":
            settings["movie"]["watch_time"] = {"hour": hour, "minute": minute}
        else:
            settings[self.category]["time"] = {"hour": hour, "minute": minute}
        
        if save_settings(settings):
            # Reload scheduler
            bot = interaction.client
            scheduler_reloaded = reload_scheduler(bot)
            
            response = f"✅ Updated **{self.category.upper()} time** to: `{self.time_input.value}`"
            if scheduler_reloaded:
                response += "\n✅ Scheduler updated automatically!"
            else:
                response += "\n⚠️ Failed to update scheduler. You may need to restart the bot."
            
            await interaction.followup.send(response, ephemeral=True)
            # Refresh the main settings message
            await self.settings_view.refresh_message(interaction)
        else:
            await interaction.followup.send("❌ Failed to save settings.", ephemeral=True)


# Select menu for day selection
class DaySelect(ui.Select):
    def __init__(self, category, current_days, settings_view):
        self.category = category
        self.settings_view = settings_view
        
        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        
        options = [
            discord.SelectOption(
                label=day,
                value=str(i),
                default=(i in current_days)
            )
            for i, day in enumerate(day_names)
        ]
        
        super().__init__(
            placeholder="Select days of the week",
            min_values=1,
            max_values=7,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        # Convert selected values to integers
        selected_days = [int(v) for v in self.values]
        
        # Update settings
        settings = load_settings()
        
        # Handle different category types
        if self.category == "movie_notification":
            settings["movie"]["notification_day"] = sorted(selected_days)
        elif self.category == "movie_watch":
            settings["movie"]["watch_day"] = sorted(selected_days)
        else:
            settings[self.category]["days"] = sorted(selected_days)
        
        if save_settings(settings):
            # Reload scheduler
            bot = interaction.client
            scheduler_reloaded = reload_scheduler(bot)
            
            response = f"✅ Updated **{self.category.upper()} days** to: `{format_days(selected_days)}`"
            if scheduler_reloaded:
                response += "\n✅ Scheduler updated automatically!"
            else:
                response += "\n⚠️ Failed to update scheduler. You may need to restart the bot."
            
            await interaction.followup.send(response, ephemeral=True)
            # Refresh the main settings message
            await self.settings_view.refresh_message(interaction)
            
            # Edit the message to remove the select menu (can't delete ephemeral messages)
            try:
                await interaction.message.edit(content="✅ Days updated successfully!", view=None)
            except:
                pass
        else:
            await interaction.followup.send("❌ Failed to save settings.", ephemeral=True)


# View for day selection
class DaySelectView(ui.View):
    def __init__(self, category, current_days, settings_view):
        super().__init__(timeout=120)
        self.add_item(DaySelect(category, current_days, settings_view))


# Modal for adding authorized roles
class AddRoleModal(ui.Modal, title="Add Authorized Roles"):
    def __init__(self, settings_view):
        super().__init__()
        self.settings_view = settings_view
        
        self.role_input = ui.TextInput(
            label="Role Name(s)",
            placeholder="Enter role names (separate multiple with commas)",
            style=discord.TextStyle.short,
            required=True,
            max_length=500
        )
        self.add_item(self.role_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        # Parse comma-separated role names
        role_names = [r.strip() for r in self.role_input.value.split(',')]
        
        # Load current settings
        settings = load_settings()
        authorized_roles = settings.get("authorized_roles", [])
        
        added_roles = []
        already_exists = []
        
        for name in role_names:
            if not name:
                continue
            # Check if already exists (case-insensitive)
            if any(r.lower() == name.lower() for r in authorized_roles):
                already_exists.append(name)
            else:
                authorized_roles.append(name)
                added_roles.append(name)
        
        if added_roles:
            settings["authorized_roles"] = authorized_roles
            save_settings(settings)
            
            response = f"✅ Added {len(added_roles)} role(s): {', '.join(added_roles)}"
            if already_exists:
                response += f"\n⚠️ Already existed: {', '.join(already_exists)}"
            
            await interaction.followup.send(response, ephemeral=True)
            await self.settings_view.refresh_message(interaction)
        else:
            if already_exists:
                await interaction.followup.send(
                    f"❌ All roles already exist: {', '.join(already_exists)}",
                    ephemeral=True
                )
            else:
                await interaction.followup.send("❌ No valid roles provided.", ephemeral=True)


# Select menu for removing authorized roles
class RemoveRoleSelect(ui.Select):
    def __init__(self, roles, settings_view):
        self.settings_view = settings_view
        
        options = [
            discord.SelectOption(
                label=role,
                value=role
            )
            for role in roles[:25]  # Discord limit
        ]
        
        super().__init__(
            placeholder="Select roles to remove",
            min_values=1,
            max_values=min(len(options), 25),
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        # Load current settings
        settings = load_settings()
        authorized_roles = settings.get("authorized_roles", [])
        
        # Remove selected roles (case-insensitive)
        for role_to_remove in self.values:
            authorized_roles = [r for r in authorized_roles if r.lower() != role_to_remove.lower()]
        
        # Ensure at least one role remains
        if not authorized_roles:
            await interaction.followup.send(
                "❌ Cannot remove all roles! At least one authorized role must remain.",
                ephemeral=True
            )
            return
        
        settings["authorized_roles"] = authorized_roles
        save_settings(settings)
        
        removed_count = len(self.values)
        await interaction.followup.send(
            f"✅ Removed {removed_count} role(s)!\n\nRemaining roles: {len(authorized_roles)}",
            ephemeral=True
        )
        
        # Refresh the main settings message
        await self.settings_view.refresh_message(interaction)
        
        # Edit the message to remove the select menu
        try:
            await interaction.message.edit(content="✅ Roles removed successfully!", view=None)
        except:
            pass


# View for remove role selection
class RemoveRoleView(ui.View):
    def __init__(self, roles, settings_view):
        super().__init__(timeout=120)
        self.add_item(RemoveRoleSelect(roles, settings_view))


# Modal for adding subreddits
class AddSubredditModal(ui.Modal, title="Add Subreddits"):
    def __init__(self, settings_view):
        super().__init__()
        self.settings_view = settings_view
        
        self.subreddit_input = ui.TextInput(
            label="Subreddit Name(s)",
            placeholder="Enter subreddit names (separate multiple with commas)",
            style=discord.TextStyle.short,
            required=True,
            max_length=500
        )
        self.add_item(self.subreddit_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        # Parse comma-separated subreddit names
        subreddit_names = [r.strip().removeprefix('r/').removeprefix('/r/') for r in self.subreddit_input.value.split(',')]
        
        # Load current settings
        settings = load_settings()
        subreddits = settings.get("qotd", {}).get("subreddits", [])
        
        added_subreddits = []
        already_exists = []
        invalid_subreddits = []
        
        for name in subreddit_names:
            if not name:
                continue
            # Check if already exists (case-insensitive)
            if any(s.lower() == name.lower() for s in subreddits):
                already_exists.append(name)
            else:
                # Test if subreddit is valid/accessible
                from reddit_api import test_subreddit
                success, message = await test_subreddit(name)
                if success:
                    subreddits.append(name)
                    added_subreddits.append(name)
                else:
                    invalid_subreddits.append(f"{name} ({message})")
        
        response_parts = []
        
        if added_subreddits:
            settings["qotd"]["subreddits"] = subreddits
            save_settings(settings)
            response_parts.append(f"✅ Added {len(added_subreddits)} subreddit(s): {', '.join('r/' + s for s in added_subreddits)}")
            await self.settings_view.refresh_message(interaction)
        
        if already_exists:
            response_parts.append(f"⚠️ Already existed: {', '.join('r/' + s for s in already_exists)}")
        
        if invalid_subreddits:
            response_parts.append(f"❌ Invalid/inaccessible: {', '.join(invalid_subreddits)}")
        
        if response_parts:
            await interaction.followup.send("\n\n".join(response_parts), ephemeral=True)
        else:
            await interaction.followup.send("❌ No valid subreddits provided.", ephemeral=True)


# Select menu for removing subreddits
class RemoveSubredditSelect(ui.Select):
    def __init__(self, subreddits, settings_view):
        self.settings_view = settings_view
        
        options = [
            discord.SelectOption(
                label=f"r/{subreddit}",
                value=subreddit
            )
            for subreddit in subreddits[:25]  # Discord limit
        ]
        
        super().__init__(
            placeholder="Select subreddits to remove",
            min_values=1,
            max_values=min(len(options), 25),
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        # Load current settings
        settings = load_settings()
        subreddits = settings.get("qotd", {}).get("subreddits", [])
        
        # Remove selected subreddits (case-insensitive)
        for subreddit_to_remove in self.values:
            subreddits = [s for s in subreddits if s.lower() != subreddit_to_remove.lower()]
        
        # Ensure at least one subreddit remains
        if not subreddits:
            await interaction.followup.send(
                "❌ Cannot remove all subreddits! At least one subreddit must remain.",
                ephemeral=True
            )
            return
        
        settings["qotd"]["subreddits"] = subreddits
        save_settings(settings)
        
        removed_count = len(self.values)
        await interaction.followup.send(
            f"✅ Removed {removed_count} subreddit(s)!\n\nRemaining subreddits: {len(subreddits)}",
            ephemeral=True
        )
        
        # Refresh the main settings message
        await self.settings_view.refresh_message(interaction)
        
        # Edit the message to remove the select menu
        try:
            await interaction.message.edit(content="✅ Subreddits removed successfully!", view=None)
        except:
            pass


# View for remove subreddit selection
class RemoveSubredditView(ui.View):
    def __init__(self, subreddits, settings_view):
        super().__init__(timeout=120)
        self.add_item(RemoveSubredditSelect(subreddits, settings_view))


# View for managing subreddits
class ManageSubredditsView(ui.View):
    def __init__(self, settings_view):
        super().__init__(timeout=120)
        self.settings_view = settings_view
    
    @ui.button(label="Add Subreddits", style=discord.ButtonStyle.success, emoji="➕", custom_id="add_subreddits")
    async def add_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(AddSubredditModal(self.settings_view))
    
    @ui.button(label="Remove Subreddits", style=discord.ButtonStyle.danger, emoji="➖", custom_id="remove_subreddits")
    async def remove_button(self, interaction: discord.Interaction, button: ui.Button):
        settings = load_settings()
        subreddits = settings.get("qotd", {}).get("subreddits", [])
        
        if not subreddits:
            await interaction.response.send_message(
                "No subreddits to remove!",
                ephemeral=True
            )
            return
        
        view = RemoveSubredditView(subreddits, self.settings_view)
        await interaction.response.send_message(
            "Select subreddits to remove:",
            view=view,
            ephemeral=True
        )


# View for managing authorized roles
class ManageRolesView(ui.View):
    def __init__(self, settings_view):
        super().__init__(timeout=120)
        self.settings_view = settings_view
    
    @ui.button(label="Add Roles", style=discord.ButtonStyle.success, emoji="➕", custom_id="add_auth_roles")
    async def add_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(AddRoleModal(self.settings_view))
    
    @ui.button(label="Remove Roles", style=discord.ButtonStyle.danger, emoji="➖", custom_id="remove_auth_roles")
    async def remove_button(self, interaction: discord.Interaction, button: ui.Button):
        settings = load_settings()
        authorized_roles = settings.get("authorized_roles", [])
        
        if not authorized_roles:
            await interaction.response.send_message(
                "No authorized roles to remove!",
                ephemeral=True
            )
            return
        
        view = RemoveRoleView(authorized_roles, self.settings_view)
        await interaction.response.send_message(
            "Select roles to remove:",
            view=view,
            ephemeral=True
        )


# Main settings view with buttons
class SettingsView(ui.View):
    def __init__(self, original_interaction):
        super().__init__(timeout=300)
        self.original_interaction = original_interaction
        self.message = None
    
    async def refresh_message(self, interaction: discord.Interaction):
        """Refresh the settings display."""
        settings = load_settings()
        content = self.build_settings_text(settings)
        
        try:
            await self.message.edit(content=content, view=self)
        except:
            pass
    
    def build_settings_text(self, settings):
        """Build the settings display text."""
        qotd_settings = settings.get("qotd", {})
        movie_settings = settings.get("movie", {})
        authorized_roles = settings.get("authorized_roles", ["comrade"])
        subreddits = qotd_settings.get("subreddits", ["randomquestions", "randomquestion"])
        
        text = "**⚙️ Bot Settings**\n\n"
        text += "**QOTD Settings:**\n"
        text += f"• Channel: `{qotd_settings.get('channel', 'Not set')}`\n"
        text += f"• Time: `{format_time(qotd_settings.get('time', {'hour': 12, 'minute': 0}))}` (Europe/Oslo)\n"
        text += f"• Days: `{format_days(qotd_settings.get('days', []))}`\n"
        text += f"• Subreddits: `{', '.join('r/' + s for s in subreddits)}`\n\n"
        
        text += "**Movie Settings:**\n"
        text += f"• Channel: `{movie_settings.get('channel', 'Not set')}`\n"
        text += f"• Notification Time: `{format_time(movie_settings.get('notification_time', {'hour': 12, 'minute': 0}))}` (Europe/Oslo)\n"
        text += f"• Notification Day: `{format_days(movie_settings.get('notification_day', []))}`\n"
        text += f"• Watch Time: `{format_time(movie_settings.get('watch_time', {'hour': 19, 'minute': 0}))}` (Europe/Oslo)\n"
        text += f"• Watch Day: `{format_days(movie_settings.get('watch_day', []))}`\n\n"
        
        text += "**Authorized Roles:**\n"
        text += f"• Roles: `{', '.join(authorized_roles)}`\n\n"
        
        text += "*Use the buttons below to change settings.*"
        
        return text
    
    @ui.button(label="QOTD Channel", style=discord.ButtonStyle.primary, emoji="📺")
    async def qotd_channel_button(self, interaction: discord.Interaction, button: ui.Button):
        settings = load_settings()
        current = settings.get("qotd", {}).get("channel", "❓qotd")
        await interaction.response.send_modal(ChannelSearchModal("qotd", interaction.guild, current, self))
    
    @ui.button(label="QOTD Time", style=discord.ButtonStyle.primary, emoji="⏰")
    async def qotd_time_button(self, interaction: discord.Interaction, button: ui.Button):
        settings = load_settings()
        current = settings.get("qotd", {}).get("time", {"hour": 12, "minute": 0})
        await interaction.response.send_modal(TimeModal("qotd", current, self))
    
    @ui.button(label="QOTD Days", style=discord.ButtonStyle.primary, emoji="📅")
    async def qotd_days_button(self, interaction: discord.Interaction, button: ui.Button):
        settings = load_settings()
        current = settings.get("qotd", {}).get("days", [0, 1, 2, 3, 4, 5, 6])
        view = DaySelectView("qotd", current, self)
        await interaction.response.send_message("Select the days for QOTD:", view=view, ephemeral=True)
    
    @ui.button(label="Manage Subreddits", style=discord.ButtonStyle.primary, emoji="📋", custom_id="manage_subreddits")
    async def manage_subreddits_button(self, interaction: discord.Interaction, button: ui.Button):
        settings = load_settings()
        subreddits = settings.get("qotd", {}).get("subreddits", [])
        
        subreddit_list = "\n".join([f"• r/{sub}" for sub in subreddits])
        
        view = ManageSubredditsView(self)
        await interaction.response.send_message(
            f"**QOTD Subreddits ({len(subreddits)}):**\n{subreddit_list}\n\nUse the buttons to add or remove subreddits.",
            view=view,
            ephemeral=True
        )
    
    @ui.button(label="Movie Channel", style=discord.ButtonStyle.success, emoji="📺")
    async def movie_channel_button(self, interaction: discord.Interaction, button: ui.Button):
        settings = load_settings()
        current = settings.get("movie", {}).get("channel", "movie-night")
        await interaction.response.send_modal(ChannelSearchModal("movie", interaction.guild, current, self))
    
    @ui.button(label="Movie Notification Time", style=discord.ButtonStyle.success, emoji="⏰")
    async def movie_notification_time_button(self, interaction: discord.Interaction, button: ui.Button):
        settings = load_settings()
        current = settings.get("movie", {}).get("notification_time", {"hour": 12, "minute": 0})
        await interaction.response.send_modal(TimeModal("movie_notification", current, self))
    
    @ui.button(label="Movie Notification Day", style=discord.ButtonStyle.success, emoji="📅")
    async def movie_notification_day_button(self, interaction: discord.Interaction, button: ui.Button):
        settings = load_settings()
        current = settings.get("movie", {}).get("notification_day", [4])
        view = DaySelectView("movie_notification", current, self)
        await interaction.response.send_message("Select the day(s) when the movie will be announced:", view=view, ephemeral=True)
    
    @ui.button(label="Movie Watch Time", style=discord.ButtonStyle.success, emoji="🕐", row=1)
    async def movie_watch_time_button(self, interaction: discord.Interaction, button: ui.Button):
        settings = load_settings()
        current = settings.get("movie", {}).get("watch_time", {"hour": 19, "minute": 0})
        await interaction.response.send_modal(TimeModal("movie_watch", current, self))
    
    @ui.button(label="Movie Watch Day", style=discord.ButtonStyle.success, emoji="📆", row=1)
    async def movie_watch_day_button(self, interaction: discord.Interaction, button: ui.Button):
        settings = load_settings()
        current = settings.get("movie", {}).get("watch_day", [5])
        view = DaySelectView("movie_watch", current, self)
        await interaction.response.send_message("Select the day(s) when the movie will be watched:", view=view, ephemeral=True)
    
    @ui.button(label="Manage Roles", style=discord.ButtonStyle.secondary, emoji="👥", row=2, custom_id="manage_auth_roles")
    async def manage_roles_button(self, interaction: discord.Interaction, button: ui.Button):
        settings = load_settings()
        authorized_roles = settings.get("authorized_roles", [])
        
        role_list = "\n".join([f"• `{role}`" for role in authorized_roles])
        
        view = ManageRolesView(self)
        await interaction.response.send_message(
            f"**Authorized Roles ({len(authorized_roles)}):**\n{role_list}\n\nUse the buttons to add or remove roles.",
            view=view,
            ephemeral=True
        )


@discord.app_commands.command(name="comrade-settings", description="View and manage bot settings (Authorized roles only)")
async def comrade_settings(interaction: discord.Interaction):
    # Check if user has any authorized role
    has_authorized_role = check_authorized(interaction.user.roles)
    
    if not has_authorized_role:
        await interaction.response.send_message("Sorry, you don't have permission to use this command!", ephemeral=True)
        return
    
    try:
        settings = load_settings()
        view = SettingsView(interaction)
        content = view.build_settings_text(settings)
        
        await interaction.response.send_message(content, view=view, ephemeral=True)
        view.message = await interaction.original_response()
        
    except Exception as e:
        logger.error(f"Error in comrade-settings command: {e}")
        await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)
