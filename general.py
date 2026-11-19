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
            "watch_day": [5],  # 5=Saturday
            "tags": ["Holiday", "Scary", "Funny", "Animated", "Adventure", "Drama"],
            "tag_days": [
                # Only pick movies with these tags when the movie night falls in this date range (MM-DD, every year)
                {"name": "Halloween", "start": "10-01", "end": "10-31", "tags": ["Scary"]},
                {"name": "Christmas", "start": "12-01", "end": "12-31", "tags": ["Holiday"]}
            ]
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
    if not day_indices:
        return "None (off)"
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


def schedule_job(bot, job_id, func, days, time_dict):
    """(Re)create a scheduled job for the given days and time. With no days the job is removed (turned off)."""
    from apscheduler.triggers.cron import CronTrigger

    if bot.scheduler.get_job(job_id):
        bot.scheduler.remove_job(job_id)

    if not days:
        logger.info(f"Job '{job_id}' has no days selected - turned off")
        return

    days_str = ','.join(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'][i] for i in days)
    bot.scheduler.add_job(
        func,
        # The timezone must be given here, otherwise CronTrigger uses the machine's local timezone
        CronTrigger(day_of_week=days_str, hour=time_dict['hour'], minute=time_dict['minute'], timezone=bot.scheduler.timezone),
        args=[bot],
        id=job_id,
        misfire_grace_time=600  # Still run if the event loop was briefly busy at the scheduled time
    )


def apply_schedule(bot):
    """Create or update the QOTD and movie jobs from the current settings."""
    import qotd
    import movies

    settings = load_settings()
    qotd_settings = settings.get("qotd", {})
    movie_settings = settings.get("movie", {})

    qotd_time = qotd_settings.get("time", {"hour": 12, "minute": 0})
    qotd_days = qotd_settings.get("days", [0, 1, 2, 3, 4, 5, 6])
    schedule_job(bot, bot.qotd_job_id, qotd.send_qotd, qotd_days, qotd_time)

    # Movie job uses notification time/day for when to select the movie
    movie_notification_time = movie_settings.get("notification_time", {"hour": 12, "minute": 0})
    movie_notification_days = movie_settings.get("notification_day", [4])
    schedule_job(bot, bot.movie_job_id, movies.choose_random_movie, movie_notification_days, movie_notification_time)

    logger.info(f"Schedule applied: QOTD ({format_days(qotd_days)} at {format_time(qotd_time)}), Movie Notification ({format_days(movie_notification_days)} at {format_time(movie_notification_time)})")


def reload_scheduler(bot):
    """Reload scheduler jobs with updated settings."""
    try:
        if not bot.scheduler or not bot.scheduler_started:
            logger.error("Scheduler not initialized")
            return False

        apply_schedule(bot)
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
                label="No days (turn off)",
                value="none",
                default=not current_days
            )
        ] + [
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
            max_values=len(options),
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        # "No days" overrides any days selected alongside it
        if "none" in self.values:
            selected_days = []
        else:
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


# Modal for adding Lemmy communities
class AddCommunityModal(ui.Modal, title="Add Communities"):
    def __init__(self, settings_view):
        super().__init__()
        self.settings_view = settings_view

        self.community_input = ui.TextInput(
            label="Community Name(s)",
            placeholder="e.g. asklemmy@lemmy.world (separate multiple with commas)",
            style=discord.TextStyle.short,
            required=True,
            max_length=500
        )
        self.add_item(self.community_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        from lemmy_api import parse_community, test_community, DEFAULT_COMMUNITIES

        # Load current settings
        settings = load_settings()
        communities = settings.get("qotd", {}).get("communities", list(DEFAULT_COMMUNITIES))

        added_communities = []
        already_exists = []
        invalid_communities = []

        # Parse comma-separated community names
        for text in self.community_input.value.split(','):
            if not text.strip():
                continue
            community = parse_community(text)
            if not community:
                invalid_communities.append(f"{text.strip()} (use the format name@instance, e.g. asklemmy@lemmy.world)")
            elif community in communities:
                already_exists.append(community)
            else:
                # Test if community is valid/accessible
                success, message = await test_community(community)
                if success:
                    communities.append(community)
                    added_communities.append(community)
                else:
                    invalid_communities.append(f"{community} ({message})")

        response_parts = []

        if added_communities:
            settings.setdefault("qotd", {})["communities"] = communities
            save_settings(settings)
            response_parts.append(f"✅ Added {len(added_communities)} community(s): {', '.join(added_communities)}")
            await self.settings_view.refresh_message(interaction)

        if already_exists:
            response_parts.append(f"⚠️ Already existed: {', '.join(already_exists)}")

        if invalid_communities:
            response_parts.append(f"❌ Invalid/inaccessible: {', '.join(invalid_communities)}")

        if response_parts:
            await interaction.followup.send("\n\n".join(response_parts), ephemeral=True)
        else:
            await interaction.followup.send("❌ No valid communities provided.", ephemeral=True)


# Select menu for removing communities
class RemoveCommunitySelect(ui.Select):
    def __init__(self, communities, settings_view):
        self.settings_view = settings_view

        options = [
            discord.SelectOption(
                label=community,
                value=community
            )
            for community in communities[:25]  # Discord limit
        ]

        super().__init__(
            placeholder="Select communities to remove",
            min_values=1,
            max_values=min(len(options), 25),
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        from lemmy_api import DEFAULT_COMMUNITIES

        # Load current settings
        settings = load_settings()
        communities = settings.get("qotd", {}).get("communities", list(DEFAULT_COMMUNITIES))

        # Remove selected communities
        communities = [c for c in communities if c not in self.values]

        # Ensure at least one community remains
        if not communities:
            await interaction.followup.send(
                "❌ Cannot remove all communities! At least one community must remain.",
                ephemeral=True
            )
            return

        settings.setdefault("qotd", {})["communities"] = communities
        save_settings(settings)

        removed_count = len(self.values)
        await interaction.followup.send(
            f"✅ Removed {removed_count} community(s)!\n\nRemaining communities: {len(communities)}",
            ephemeral=True
        )

        # Refresh the main settings message
        await self.settings_view.refresh_message(interaction)

        # Edit the message to remove the select menu
        try:
            await interaction.message.edit(content="✅ Communities removed successfully!", view=None)
        except:
            pass


# View for remove community selection
class RemoveCommunityView(ui.View):
    def __init__(self, communities, settings_view):
        super().__init__(timeout=120)
        self.add_item(RemoveCommunitySelect(communities, settings_view))


# View for managing communities
class ManageCommunitiesView(ui.View):
    def __init__(self, settings_view):
        super().__init__(timeout=120)
        self.settings_view = settings_view

    @ui.button(label="Add Communities", style=discord.ButtonStyle.success, emoji="➕", custom_id="add_communities")
    async def add_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(AddCommunityModal(self.settings_view))

    @ui.button(label="Remove Communities", style=discord.ButtonStyle.danger, emoji="➖", custom_id="remove_communities")
    async def remove_button(self, interaction: discord.Interaction, button: ui.Button):
        from lemmy_api import DEFAULT_COMMUNITIES
        settings = load_settings()
        communities = settings.get("qotd", {}).get("communities", list(DEFAULT_COMMUNITIES))

        if not communities:
            await interaction.response.send_message(
                "No communities to remove!",
                ephemeral=True
            )
            return

        view = RemoveCommunityView(communities, self.settings_view)
        await interaction.response.send_message(
            "Select communities to remove:",
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


# Modal for adding movie tags
class AddTagModal(ui.Modal, title="Add Movie Tags"):
    def __init__(self, settings_view):
        super().__init__()
        self.settings_view = settings_view

        self.tag_input = ui.TextInput(
            label="Tag Name(s)",
            placeholder="Enter tag names (separate multiple with commas)",
            style=discord.TextStyle.short,
            required=True,
            max_length=500
        )
        self.add_item(self.tag_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        from movies import get_tags

        settings = load_settings()
        tags = list(get_tags(settings))

        added_tags = []
        already_exists = []
        hit_limit = False
        for name in (t.strip()[:50] for t in self.tag_input.value.split(',')):
            if not name:
                continue
            if any(t.lower() == name.lower() for t in tags):
                already_exists.append(name)
            elif len(tags) >= 25:  # Discord select menus can show at most 25 tags
                hit_limit = True
            else:
                tags.append(name)
                added_tags.append(name)

        response_parts = []
        if added_tags:
            settings["movie"]["tags"] = tags
            save_settings(settings)
            response_parts.append(f"✅ Added {len(added_tags)} tag(s): {', '.join(added_tags)}")
            await self.settings_view.refresh_message(interaction)
        if already_exists:
            response_parts.append(f"⚠️ Already existed: {', '.join(already_exists)}")
        if hit_limit:
            response_parts.append("⚠️ Some tags were not added: the maximum is 25 tags.")

        await interaction.followup.send("\n\n".join(response_parts) or "❌ No valid tags provided.", ephemeral=True)


# Select menu for removing movie tags
class RemoveTagSelect(ui.Select):
    def __init__(self, tags, settings_view):
        self.settings_view = settings_view

        options = [discord.SelectOption(label=tag, value=tag) for tag in tags[:25]]

        super().__init__(
            placeholder="Select tags to remove",
            min_values=1,
            max_values=len(options),
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        from movies import get_tags, get_tag_days, load_movie_data, save_movie_data

        removed = {v.lower() for v in self.values}

        # Remove the tags from settings and from tag days; tag days left without tags are removed
        settings = load_settings()
        settings["movie"]["tags"] = [t for t in get_tags(settings) if t.lower() not in removed]
        kept_tag_days = []
        removed_tag_days = []
        for tag_day in get_tag_days(settings):
            tag_day["tags"] = [t for t in tag_day.get("tags", []) if t.lower() not in removed]
            if tag_day["tags"]:
                kept_tag_days.append(tag_day)
            else:
                removed_tag_days.append(tag_day.get("name", "Unnamed"))
        settings["movie"]["tag_days"] = kept_tag_days
        save_settings(settings)

        # Remove the tags from all movies
        movies_list, metadata = await load_movie_data()
        untagged = 0
        for movie in movies_list:
            kept = [t for t in movie["tags"] if t.lower() not in removed]
            if len(kept) != len(movie["tags"]):
                movie["tags"] = kept
                untagged += 1
        await save_movie_data(movies_list, metadata)

        content = f"✅ Removed {len(self.values)} tag(s): {', '.join(self.values)}\n• Removed from {untagged} movie(s)"
        if removed_tag_days:
            content += f"\n• Tag days removed because they had no tags left: {', '.join(removed_tag_days)}"
        await interaction.response.edit_message(content=content, view=None)
        await self.settings_view.refresh_message(interaction)


# View for remove tag selection
class RemoveTagView(ui.View):
    def __init__(self, tags, settings_view):
        super().__init__(timeout=120)
        self.add_item(RemoveTagSelect(tags, settings_view))


# View for managing movie tags
class ManageTagsView(ui.View):
    def __init__(self, settings_view):
        super().__init__(timeout=120)
        self.settings_view = settings_view

    @ui.button(label="Add Tags", style=discord.ButtonStyle.success, emoji="➕")
    async def add_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(AddTagModal(self.settings_view))

    @ui.button(label="Remove Tags", style=discord.ButtonStyle.danger, emoji="➖")
    async def remove_button(self, interaction: discord.Interaction, button: ui.Button):
        from movies import get_tags
        tags = get_tags(load_settings())

        if not tags:
            await interaction.response.send_message("No tags to remove!", ephemeral=True)
            return

        await interaction.response.send_message(
            "Select tags to remove (they will also be removed from all movies and tag days):",
            view=RemoveTagView(tags, self.settings_view),
            ephemeral=True
        )


# Modal for adding a tag day
class AddTagDayModal(ui.Modal, title="Add Tag Day"):
    def __init__(self, settings_view):
        super().__init__()
        self.settings_view = settings_view

        self.name_input = ui.TextInput(label="Name", placeholder="e.g., Halloween", required=True, max_length=50)
        self.start_input = ui.TextInput(label="Start date (MM-DD)", placeholder="e.g., 10-01", required=True, max_length=5)
        self.end_input = ui.TextInput(label="End date (MM-DD, leave empty for one day)", placeholder="e.g., 10-31", required=False, max_length=5)
        self.tags_input = ui.TextInput(label="Tags (separate multiple with commas)", placeholder="e.g., Scary", required=True, max_length=500)
        for item in (self.name_input, self.start_input, self.end_input, self.tags_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        from movies import get_tags, parse_month_day, parse_tag_list, format_tag_day

        start = self.start_input.value.strip()
        end = self.end_input.value.strip() or start
        if not parse_month_day(start) or not parse_month_day(end):
            await interaction.response.send_message("❌ Invalid date. Use MM-DD, e.g. `10-31` for October 31st.", ephemeral=True)
            return

        settings = load_settings()
        all_tags = get_tags(settings)

        tags, unknown = parse_tag_list(self.tags_input.value, all_tags)

        if unknown or not tags:
            await interaction.response.send_message(
                f"❌ Unknown tag(s): {', '.join(unknown) or 'none given'}\n\nAvailable tags: {', '.join(all_tags) or 'none (add some first)'}",
                ephemeral=True
            )
            return

        tag_day = {"name": self.name_input.value.strip(), "start": start, "end": end, "tags": tags}
        settings["movie"].setdefault("tag_days", []).append(tag_day)
        save_settings(settings)

        await interaction.response.send_message(f"✅ Added tag day: {format_tag_day(tag_day)}", ephemeral=True)
        await self.settings_view.refresh_message(interaction)


# Select menu for removing tag days
class RemoveTagDaySelect(ui.Select):
    def __init__(self, tag_days, settings_view):
        from movies import format_tag_day
        self.settings_view = settings_view

        options = [
            discord.SelectOption(label=tag_day.get("name", "Unnamed")[:100], value=str(i), description=format_tag_day(tag_day)[:100])
            for i, tag_day in enumerate(tag_days[:25])
        ]

        super().__init__(
            placeholder="Select tag days to remove",
            min_values=1,
            max_values=len(options),
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        from movies import get_tag_days

        settings = load_settings()
        tag_days = get_tag_days(settings)
        selected = {int(v) for v in self.values}
        removed = [td.get("name", "Unnamed") for i, td in enumerate(tag_days) if i in selected]
        settings["movie"]["tag_days"] = [td for i, td in enumerate(tag_days) if i not in selected]
        save_settings(settings)

        await interaction.response.edit_message(content=f"✅ Removed tag day(s): {', '.join(removed)}", view=None)
        await self.settings_view.refresh_message(interaction)


# View for remove tag day selection
class RemoveTagDayView(ui.View):
    def __init__(self, tag_days, settings_view):
        super().__init__(timeout=120)
        self.add_item(RemoveTagDaySelect(tag_days, settings_view))


# View for managing tag days
class ManageTagDaysView(ui.View):
    def __init__(self, settings_view):
        super().__init__(timeout=120)
        self.settings_view = settings_view

    @ui.button(label="Add Tag Day", style=discord.ButtonStyle.success, emoji="➕")
    async def add_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(AddTagDayModal(self.settings_view))

    @ui.button(label="Remove Tag Days", style=discord.ButtonStyle.danger, emoji="➖")
    async def remove_button(self, interaction: discord.Interaction, button: ui.Button):
        from movies import get_tag_days
        tag_days = get_tag_days(load_settings())

        if not tag_days:
            await interaction.response.send_message("No tag days to remove!", ephemeral=True)
            return

        await interaction.response.send_message(
            "Select tag days to remove:",
            view=RemoveTagDayView(tag_days, self.settings_view),
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
        from lemmy_api import DEFAULT_COMMUNITIES
        communities = qotd_settings.get("communities", DEFAULT_COMMUNITIES)
        
        text = "**⚙️ Bot Settings**\n\n"
        text += "**QOTD Settings:**\n"
        text += f"• Channel: `{qotd_settings.get('channel', 'Not set')}`\n"
        text += f"• Time: `{format_time(qotd_settings.get('time', {'hour': 12, 'minute': 0}))}` (Europe/Oslo)\n"
        text += f"• Days: `{format_days(qotd_settings.get('days', []))}`\n"
        text += f"• Lemmy Communities: `{', '.join(communities)}`\n\n"
        
        text += "**Movie Settings:**\n"
        text += f"• Channel: `{movie_settings.get('channel', 'Not set')}`\n"
        text += f"• Notification Time: `{format_time(movie_settings.get('notification_time', {'hour': 12, 'minute': 0}))}` (Europe/Oslo)\n"
        text += f"• Notification Day: `{format_days(movie_settings.get('notification_day', []))}`\n"
        text += f"• Watch Time: `{format_time(movie_settings.get('watch_time', {'hour': 19, 'minute': 0}))}` (Europe/Oslo)\n"
        text += f"• Watch Day: `{format_days(movie_settings.get('watch_day', []))}`\n"

        from movies import get_tags, get_tag_days, format_tag_day
        tags = get_tags(settings)
        tag_days = get_tag_days(settings)
        text += f"• Tags: `{', '.join(tags) if tags else 'None'}`\n"
        if tag_days:
            text += "• Tag Days (only movies with these tags are picked):\n"
            text += "".join(f"  - `{format_tag_day(td)}`\n" for td in tag_days)
        else:
            text += "• Tag Days: `None`\n"
        text += "\n"
        
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
    
    @ui.button(label="Manage Communities", style=discord.ButtonStyle.primary, emoji="📋", custom_id="manage_communities")
    async def manage_communities_button(self, interaction: discord.Interaction, button: ui.Button):
        from lemmy_api import DEFAULT_COMMUNITIES
        settings = load_settings()
        communities = settings.get("qotd", {}).get("communities", DEFAULT_COMMUNITIES)

        community_list = "\n".join([f"• {community}" for community in communities])

        view = ManageCommunitiesView(self)
        await interaction.response.send_message(
            f"**QOTD Lemmy Communities ({len(communities)}):**\n{community_list}\n\nUse the buttons to add or remove communities.",
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
    
    @ui.button(label="Movie Tags", style=discord.ButtonStyle.success, emoji="🏷️", row=2)
    async def movie_tags_button(self, interaction: discord.Interaction, button: ui.Button):
        from movies import get_tags
        tags = get_tags(load_settings())
        tag_list = "\n".join(f"• `{tag}`" for tag in tags) or "No tags yet."

        await interaction.response.send_message(
            f"**Movie Tags ({len(tags)}):**\n{tag_list}\n\nUse the buttons to add or remove tags. Tag movies from `/movie-list`.",
            view=ManageTagsView(self),
            ephemeral=True
        )

    @ui.button(label="Tag Days", style=discord.ButtonStyle.success, emoji="🎃", row=2)
    async def tag_days_button(self, interaction: discord.Interaction, button: ui.Button):
        from movies import get_tag_days, format_tag_day
        tag_days = get_tag_days(load_settings())
        tag_day_list = "\n".join(f"• `{format_tag_day(td)}`" for td in tag_days) or "No tag days yet."

        await interaction.response.send_message(
            f"**Tag Days ({len(tag_days)}):**\n{tag_day_list}\n\n"
            f"When a movie night falls within a tag day, only movies with at least one of its tags are picked. "
            f"Dates are MM-DD and repeat every year.",
            view=ManageTagDaysView(self),
            ephemeral=True
        )

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
