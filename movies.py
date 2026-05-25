import discord
from discord.ext import commands
from discord import ui
import logging
import os
import json
import random

logger = logging.getLogger(__name__)


async def load_movie_data():
    """Load movie list and metadata from JSON file."""
    logger.info("Loading movie data")
    movie_path = os.path.join(os.path.dirname(__file__), "movie_list.json")
    
    if os.path.exists(movie_path):
        with open(movie_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            movies = data.get("movies", [])
            metadata = data.get("metadata", {
                "active_movie_selection": None
            })
            return movies, metadata
    else:
        return [], {"active_movie_selection": None}


async def save_movie_data(movies, metadata=None):
    """Save movie list and metadata to JSON file."""
    logger.info("Saving movie data")
    movie_path = os.path.join(os.path.dirname(__file__), "movie_list.json")
    
    # Load existing metadata if not provided
    if metadata is None:
        _, metadata = await load_movie_data()
    
    with open(movie_path, "w", encoding="utf-8") as f:
        json.dump({"movies": movies, "metadata": metadata}, f, ensure_ascii=False, indent=2)


def get_ordinal_suffix(n):
    """Get ordinal suffix for a number (1st, 2nd, 3rd, etc.)."""
    if 10 <= n % 100 <= 20:
        return 'th'
    else:
        return {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')


def calculate_next_watch_datetime(settings):
    """Calculate the next movie watch datetime based on settings.
    
    Returns:
        datetime: Next watch datetime in Europe/Oslo timezone
    """
    import pytz
    from datetime import datetime, timedelta
    
    timezone = pytz.timezone('Europe/Oslo')
    now = datetime.now(timezone)
    
    watch_days = settings.get("movie", {}).get("watch_day", [5])  # Default to Saturday
    watch_time = settings.get("movie", {}).get("watch_time", {"hour": 19, "minute": 0})
    
    current_weekday = now.weekday()  # 0=Monday, 6=Sunday
    
    # Find the soonest watch day
    days_ahead_options = []
    for target_weekday in watch_days:
        days_ahead = target_weekday - current_weekday
        
        # Check if it's today but the time hasn't passed yet
        if days_ahead == 0:
            watch_datetime_today = now.replace(
                hour=watch_time['hour'],
                minute=watch_time['minute'],
                second=0,
                microsecond=0
            )
            if now < watch_datetime_today:
                # Today, but time hasn't passed yet
                days_ahead_options.append(0)
            else:
                # Today but time has passed, so next week
                days_ahead_options.append(7)
        elif days_ahead < 0:
            # Day already passed this week, so next week
            days_ahead_options.append(days_ahead + 7)
        else:
            # Day is coming up this week
            days_ahead_options.append(days_ahead)
    
    # Get the minimum (soonest)
    min_days_ahead = min(days_ahead_options) if days_ahead_options else 7
    
    event_date = now + timedelta(days=min_days_ahead)
    watch_datetime = timezone.localize(datetime(
        event_date.year,
        event_date.month,
        event_date.day,
        watch_time['hour'],
        watch_time['minute'],
        0
    ))
    
    return watch_datetime


async def cleanup_expired_movie_selection(movies, metadata, settings):
    """Check if active movie selection has expired and clean it up.
    
    Returns:
        tuple: (movies, metadata, was_cleaned)
    """
    active_selection = metadata.get('active_movie_selection')
    
    if not active_selection:
        return movies, metadata, False
    
    import pytz
    from datetime import datetime
    
    timezone = pytz.timezone('Europe/Oslo')
    now = datetime.now(timezone)
    
    # Check if watch time has passed
    watch_timestamp = active_selection.get('watch_datetime')
    if watch_timestamp:
        watch_datetime = datetime.fromtimestamp(watch_timestamp, tz=timezone)
        
        if now > watch_datetime:
            # Watch time has passed, clear the selection
            logger.info(f"Cleaning up expired movie selection: {active_selection.get('movie')}")
            metadata['active_movie_selection'] = None
            return movies, metadata, True
    
    return movies, metadata, False


async def select_random_movie_internal(movies):
    """Shared function to select a random movie from the list.
    
    Returns:
        tuple: (chosen_movie, chosen_index)
    """
    # Ensure proper randomization
    import time
    random.seed(time.time())
    
    # Choose random movie using random index
    chosen_index = random.randint(0, len(movies) - 1)
    chosen_movie = movies[chosen_index]
    
    return chosen_movie, chosen_index


async def choose_random_movie(bot):
    """Choose a random movie from the list and post it to movie-night channels."""
    try:
        from general import load_settings, format_days, format_time
        settings = load_settings()
        movie_channel_name = settings.get("movie", {}).get("channel", "movie-night")
        
        # Get watch day and time
        watch_day = settings.get("movie", {}).get("watch_day", [5])
        watch_time = settings.get("movie", {}).get("watch_time", {"hour": 19, "minute": 0})
        
        watch_day_str = format_days(watch_day)
        watch_time_str = format_time(watch_time)
        
        # Load movie list and metadata
        movies, metadata = await load_movie_data()
        
        # Clean up expired selection if exists
        movies, metadata, was_cleaned = await cleanup_expired_movie_selection(movies, metadata, settings)
        if was_cleaned:
            await save_movie_data(movies, metadata)
        
        # Check if there's an active movie selection that hasn't expired
        active_selection = metadata.get('active_movie_selection')
        if active_selection:
            logger.info(f"Active movie selection exists ({active_selection.get('movie')}), skipping scheduled post")
            return
        
        if not movies:
            logger.info("No movies in the list to choose from")
            # Send message to movie-night channels that there are no movies
            for guild in bot.guilds:
                channel = discord.utils.get(guild.text_channels, name=movie_channel_name)
                if channel:
                    try:
                        await channel.send("🎬 It's movie night time! But there are no movies in the list. Add some with `/addmovie`!")
                    except Exception as e:
                        logger.error(f"Failed to send no-movies message to #{channel.name}: {e}")
            return
        
        # Use shared selection function
        chosen_movie, chosen_index = await select_random_movie_internal(movies)
        
        # Calculate position ordinal (which slot it was in the list)
        list_position = chosen_index + 1
        total_movies = len(movies)
        position_ordinal = f"{list_position}{get_ordinal_suffix(list_position)}"
        
        # Calculate next watch datetime
        watch_datetime = calculate_next_watch_datetime(settings)
        
        # Remove from list
        movies.remove(chosen_movie)
        
        # Update metadata with active selection
        new_event_ids = {}
        
        # Calculate event start time based on watch_day and watch_time
        import pytz
        from datetime import datetime, timedelta
        
        timezone = pytz.timezone('Europe/Oslo')
        now = datetime.now(timezone)
        
        # Find the next occurrence of watch_day
        current_weekday = now.weekday()  # 0=Monday, 6=Sunday
        target_weekday = watch_day[0] if watch_day else 5  # Default to Saturday
        
        days_ahead = target_weekday - current_weekday
        if days_ahead <= 0:  # Target day already happened this week or is today
            days_ahead += 7
        
        event_date = now + timedelta(days=days_ahead)
        event_start = timezone.localize(datetime(
            event_date.year, 
            event_date.month, 
            event_date.day, 
            watch_time['hour'], 
            watch_time['minute']
        ))
        
        # Event duration: 2 hours
        event_end = event_start + timedelta(hours=2)
        
        # Send to all movie-night channels and create events
        for guild in bot.guilds:
            channel = discord.utils.get(guild.text_channels, name=movie_channel_name)
            event = None
            
            # Create Discord scheduled event first
            try:
                event = await guild.create_scheduled_event(
                    name=f"🎬 Movie Night: {chosen_movie}",
                    description=f"Join us for movie night!\n\n🎥 **Movie:** {chosen_movie}\n📍 **List Position:** {position_ordinal} of {total_movies}\n🍿 Get your snacks ready!",
                    start_time=event_start,
                    end_time=event_end,
                    entity_type=discord.EntityType.external,
                    location=movie_channel_name,
                    privacy_level=discord.PrivacyLevel.guild_only
                )
                new_event_ids[str(guild.id)] = event.id
                logger.info(f"Created event '{event.name}' in guild '{guild.name}' (ID: {event.id})")
            except Exception as e:
                logger.error(f"Failed to create event in guild '{guild.name}': {e}")
            
            # Send message with event link
            if channel:
                try:
                    message = (
                        f"🎬 **Movie Selected:** {chosen_movie}\n"
                        f"📍 **Was at position:** {position_ordinal} of {total_movies}\n\n"
                        f"📅 **Watch Day:** {watch_day_str}\n"
                        f"🕐 **Watch Time:** {watch_time_str}\n"
                    )
                    
                    if event:
                        message += f"\n📌 **Event:** {event.url}\n"
                    
                    message += "\n🍿 See you there!"
                    
                    await channel.send(message)
                    logger.info(f"Sent movie choice '{chosen_movie}' to #{channel.name}")
                except Exception as e:
                    logger.error(f"Failed to send movie choice to #{channel.name}: {e}")
        
        # Save active selection and event IDs
        import time
        metadata['active_movie_selection'] = {
            'movie': chosen_movie,
            'list_position': list_position,
            'total_movies': total_movies,
            'selected_at': time.time(),
            'watch_datetime': watch_datetime.timestamp(),
            'event_ids': new_event_ids
        }
        await save_movie_data(movies, metadata)
        
        logger.info(f"Chose movie from position {position_ordinal} of {total_movies}: {chosen_movie}. {len(movies)} remaining in list. Watch time: {watch_datetime}")
        
    except Exception as e:
        logger.error(f"Error in choose_random_movie: {e}")


# Command definitions
@discord.app_commands.command(name="movie-add", description="Add a movie to the movie night list")
@discord.app_commands.describe(movie="The name of the movie to add")
async def movie_add(interaction: discord.Interaction, movie: str):
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Load current movie list
        movies, metadata = await load_movie_data()
        
        # Check if movie already exists (case-insensitive)
        if any(m.lower() == movie.lower() for m in movies):
            await interaction.followup.send(f"❌ '{movie}' is already in the list!", ephemeral=True)
            return
        
        # Add movie to list
        movies.append(movie)
        await save_movie_data(movies, metadata)
        
        await interaction.followup.send(f"✅ Added '{movie}' to the movie list! ({len(movies)} movies total)", ephemeral=True)
        logger.info(f"User {interaction.user.display_name} added movie: {movie}")
        
    except Exception as e:
        logger.error(f"Error in addmovie command: {e}")
        try:
            await interaction.followup.send("Sorry, I'm having trouble adding the movie right now. Please try again later.")
        except:
            logger.error("Failed to send error message to user")


@discord.app_commands.command(name="movie-choose", description="Choose a random movie from the list (Authorized roles only)")
@discord.app_commands.describe(mode="movienight = scheduled movie night | now = immediate movie night (starts in 5 min)")
@discord.app_commands.choices(mode=[
    discord.app_commands.Choice(name="movienight", value="movienight"),
    discord.app_commands.Choice(name="now", value="now")
])
async def movie_choose(interaction: discord.Interaction, mode: discord.app_commands.Choice[str]):
    # Check if user has any authorized role
    from general import check_authorized
    has_authorized_role = check_authorized(interaction.user.roles)
    
    if not has_authorized_role:
        await interaction.response.send_message("Sorry, you don't have permission to use this command!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        from general import load_settings
        settings = load_settings()
        
        # Load movie list and metadata
        movies, metadata = await load_movie_data()
        
        if not movies:
            await interaction.followup.send("❌ The movie list is empty! Add some movies with `/addmovie` first.")
            return
        
        # Handle different modes
        if mode.value == "now":
            # NOW mode: Quick movie night starting in 5 minutes, removes movie but doesn't affect metadata
            from datetime import datetime, timedelta
            import pytz
            
            # Select a random movie
            chosen_movie, chosen_index = await select_random_movie_internal(movies)
            list_position = chosen_index + 1
            total_movies = len(movies)
            position_ordinal = f"{list_position}{get_ordinal_suffix(list_position)}"
            
            # Remove movie from list and save (but don't modify metadata)
            movies.remove(chosen_movie)
            await save_movie_data(movies, metadata)
            
            # Event starts in 5 minutes
            timezone = pytz.timezone('Europe/Oslo')
            event_start = datetime.now(timezone) + timedelta(minutes=5)
            event_end = event_start + timedelta(hours=2)
            
            movie_channel_name = settings.get("movie", {}).get("channel", "movie-night")
            
            # Create events
            created_guilds = []
            for guild in interaction.client.guilds:
                try:
                    event = await guild.create_scheduled_event(
                        name=f"🎬 Movie Night NOW: {chosen_movie}",
                        description=f"Impromptu movie night!\n\n🎥 **Movie:** {chosen_movie}\n⏰ **Starting in 5 minutes!**\n🍿 Get ready!",
                        start_time=event_start,
                        end_time=event_end,
                        entity_type=discord.EntityType.external,
                        location=movie_channel_name,
                        privacy_level=discord.PrivacyLevel.guild_only
                    )
                    created_guilds.append(guild.name)
                    logger.info(f"Created immediate movie event in guild '{guild.name}'")
                except Exception as e:
                    logger.error(f"Failed to create immediate event in guild '{guild.name}': {e}")
            
            guilds_text = ", ".join(created_guilds) if created_guilds else "No guilds"
            await interaction.followup.send(
                f"🎬 **Immediate Movie Night!**\n"
                f"🎥 **Movie:** {chosen_movie}\n"
                f"📍 **Was at position:** {position_ordinal} of {total_movies}\n"
                f"⏰ **Starting:** <t:{int(event_start.timestamp())}:R>\n\n"
                f"🎭 Events created in: {guilds_text}\n"
                f"({len(movies)} movies remaining in list)"
            )
            logger.info(f"User {interaction.user.display_name} started immediate movie night: {chosen_movie}")
            
        else:  # mode.value == "movienight"
            # MOVIENIGHT mode: Normal scheduled movie night (current behavior)
            
            # Clean up expired selection if exists
            movies, metadata, was_cleaned = await cleanup_expired_movie_selection(movies, metadata, settings)
            if was_cleaned:
                await save_movie_data(movies, metadata)
            
            # Re-add previous movie to list if active selection exists
            active_selection = metadata.get('active_movie_selection')
            if active_selection:
                previous_movie = active_selection.get('movie')
                if previous_movie:
                    movies.append(previous_movie)
                    logger.info(f"Re-added active movie to list: {previous_movie}")
                    
                    # Delete previous events
                    previous_event_ids = active_selection.get('event_ids', {})
                    for guild_id_str, event_id in previous_event_ids.items():
                        try:
                            guild = interaction.client.get_guild(int(guild_id_str))
                            if guild:
                                try:
                                    event = await guild.fetch_scheduled_event(event_id)
                                    await event.delete()
                                    logger.info(f"Deleted previous event (ID: {event_id}) in guild '{guild.name}'")
                                except discord.NotFound:
                                    logger.warning(f"Previous event (ID: {event_id}) not found in guild '{guild.name}'")
                                except Exception as e:
                                    logger.error(f"Failed to delete previous event in guild '{guild.name}': {e}")
                        except Exception as e:
                            logger.error(f"Error processing previous event for guild {guild_id_str}: {e}")
            
            # Use shared selection function
            chosen_movie, chosen_index = await select_random_movie_internal(movies)
            
            # Calculate position ordinal (which slot it was in the list)
            list_position = chosen_index + 1
            total_movies = len(movies)
            position_ordinal = f"{list_position}{get_ordinal_suffix(list_position)}"
            
            # Calculate next watch datetime and event times
            from general import format_days, format_time
            movie_channel_name = settings.get("movie", {}).get("channel", "movie-night")
            
            watch_datetime = calculate_next_watch_datetime(settings)
            event_start = watch_datetime
            
            from datetime import timedelta
            event_end = event_start + timedelta(hours=2)
            
            new_event_ids = {}
            for guild in interaction.client.guilds:
                try:
                    event = await guild.create_scheduled_event(
                        name=f"🎬 Movie Night: {chosen_movie}",
                        description=f"Join us for movie night!\n\n🎥 **Movie:** {chosen_movie}\n📍 **List Position:** {position_ordinal} of {total_movies}\n🍿 Get your snacks ready!",
                        start_time=event_start,
                        end_time=event_end,
                        entity_type=discord.EntityType.external,
                        location=movie_channel_name,
                        privacy_level=discord.PrivacyLevel.guild_only
                    )
                    new_event_ids[str(guild.id)] = event.id
                    logger.info(f"Created new event for movie at position {list_position} in guild '{guild.name}'")
                except Exception as e:
                    logger.error(f"Failed to create event in guild '{guild.name}': {e}")
            
            # Remove chosen movie from list and save active selection
            movies.remove(chosen_movie)
            
            import time
            metadata['active_movie_selection'] = {
                'movie': chosen_movie,
                'list_position': list_position,
                'total_movies': total_movies,
                'selected_at': time.time(),
                'watch_datetime': watch_datetime.timestamp(),
                'event_ids': new_event_ids
            }
            await save_movie_data(movies, metadata)
            
            await interaction.followup.send(
                f"🎬 **Movie Selected:** {chosen_movie}\n"
                f"📍 **Was at position:** {position_ordinal} of {total_movies}\n\n"
                f"🍿 Enjoy the show!\n\n"
                f"({len(movies)} movies remaining in list)"
            )
            logger.info(f"User {interaction.user.display_name} chose movie from position {position_ordinal}: {chosen_movie}")
        
    except Exception as e:
        logger.error(f"Error in movie-choose command: {e}")
        try:
            await interaction.followup.send("Sorry, I'm having trouble choosing a movie right now. Please try again later.")
        except:
            logger.error("Failed to send error message to user")


# Modal for adding movies
class AddMovieModal(ui.Modal, title="Add Movies"):
    def __init__(self, movie_list_view):
        super().__init__()
        self.movie_list_view = movie_list_view
        
        self.movie_input = ui.TextInput(
            label="Movie Name(s)",
            placeholder="Enter movie names (separate multiple with commas)",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        self.add_item(self.movie_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        # Parse comma-separated movie names
        movie_names = [s.strip() for s in self.movie_input.value.split(',')]
        movies_list, metadata = await load_movie_data()
        movies_to_add = []
        already_exists = []
        
        # Validate each movie name
        for name in movie_names:
            if not name:
                continue
            # Check if already exists (case-insensitive)
            if any(m.lower() == name.lower() for m in movies_list):
                already_exists.append(name)
            else:
                movies_to_add.append(name)
        
        if already_exists:
            exists_list = ', '.join(f"'{n}'" for n in already_exists)
            await interaction.followup.send(
                f"❌ Already exist: {exists_list}\n\nTry again with different movie names.",
                ephemeral=True
            )
            return
        
        if not movies_to_add:
            await interaction.followup.send("❌ No valid movies found in your input.", ephemeral=True)
            return
        
        # Add the movies
        for movie_name in movies_to_add:
            movies_list.append(movie_name)
        
        await save_movie_data(movies_list, metadata)
        
        added_count = len(movies_to_add)
        await interaction.followup.send(
            f"✅ Added {added_count} movie{'s' if added_count != 1 else ''}!\n\nTotal movies: {len(movies_list)}",
            ephemeral=True
        )
        
        # Refresh the main movie list message
        await self.movie_list_view.refresh_message(interaction)


# Select menu for removing movies
class RemoveMovieSelect(ui.Select):
    def __init__(self, movies_list, movie_list_view):
        self.movie_list_view = movie_list_view
        self.all_movies = movies_list
        
        # Show up to 25 movies
        options = [
            discord.SelectOption(
                label=movie[:100],  # Discord label limit
                value=str(i),
                description=f"Movie #{i+1}"
            )
            for i, movie in enumerate(movies_list[:25])
        ]
        
        if not options:
            options = [discord.SelectOption(label="No movies available", value="none")]
        
        super().__init__(
            placeholder="Select movie(s) to remove",
            min_values=1,
            max_values=min(len(options), 25),
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        if self.values[0] == "none":
            await interaction.followup.send("❌ No movies available.", ephemeral=True)
            return
        
        # Get selected movies
        selected_indices = [int(v) for v in self.values]
        movies_to_remove = [self.all_movies[i] for i in selected_indices]
        
        # Remove the movies
        movies_list, metadata = await load_movie_data()
        for movie_name in movies_to_remove:
            if movie_name in movies_list:
                movies_list.remove(movie_name)
        
        await save_movie_data(movies_list, metadata)
        
        removed_count = len(movies_to_remove)
        await interaction.followup.send(
            f"✅ Removed {removed_count} movie{'s' if removed_count != 1 else ''}!\n\nRemaining movies: {len(movies_list)}",
            ephemeral=True
        )
        
        # Refresh the main movie list message
        await self.movie_list_view.refresh_message(interaction)
        
        # Edit the message to remove the select menu
        try:
            await interaction.message.edit(content="✅ Movies removed successfully!", view=None)
        except:
            pass


# View for remove movie selection
class RemoveMovieView(ui.View):
    def __init__(self, movies_list, movie_list_view):
        super().__init__(timeout=120)
        self.add_item(RemoveMovieSelect(movies_list, movie_list_view))


# Main movie list view with buttons
class MovieListView(ui.View):
    def __init__(self, original_interaction, has_authorized_role):
        super().__init__(timeout=300)
        self.original_interaction = original_interaction
        self.has_authorized_role = has_authorized_role
        self.message = None
        
        # Remove "Remove Movies" button if user doesn't have authorized role
        if not has_authorized_role:
            # Remove the second button (remove button)
            self.remove_item(self.remove_button)
    
    async def refresh_message(self, interaction: discord.Interaction):
        """Refresh the movie list display."""
        movies_list, _ = await load_movie_data()
        content = self.build_movie_list_text(movies_list)
        
        try:
            await self.message.edit(content=content, view=self)
        except:
            pass
    
    def build_movie_list_text(self, movies_list):
        """Build the movie list display text."""
        if not movies_list:
            text = "📽️ **Movie List**\n\nThe movie list is empty!"
        else:
            movies_display = "\n".join([f"{i+1}. {m}" for i, m in enumerate(movies_list)])
            text = f"**🎬 Movie List ({len(movies_list)} movies):**\n```\n{movies_display}\n```"
        
        text += "\n\n*Use the buttons below to add movies.*"
        if self.has_authorized_role:
            text += " Authorized users can also remove movies."
        
        return text
    
    @ui.button(label="Add Movies", style=discord.ButtonStyle.success, emoji="➕", custom_id="movie_add_button")
    async def add_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(AddMovieModal(self))
    
    @ui.button(label="Remove Movies", style=discord.ButtonStyle.danger, emoji="➖", custom_id="movie_remove_button")
    async def remove_button(self, interaction: discord.Interaction, button: ui.Button):
        movies_list, _ = await load_movie_data()
        
        if not movies_list:
            await interaction.response.send_message("❌ No movies available to remove!", ephemeral=True)
            return
        
        if len(movies_list) > 25:
            await interaction.response.send_message(
                f"⚠️ You have {len(movies_list)} movies, but can only view 25 at a time in the menu.\n\n"
                f"Consider removing movies in batches. Showing first 25:",
                ephemeral=True
            )
            # Still show the view
            view = RemoveMovieView(movies_list, self)
            await interaction.followup.send("Select movies to remove:", view=view, ephemeral=True)
        else:
            view = RemoveMovieView(movies_list, self)
            await interaction.response.send_message("Select movie(s) to remove:", view=view, ephemeral=True)


@discord.app_commands.command(name="movie-list", description="View or manage the movie list")
async def movie_list(interaction: discord.Interaction):
    # Check if user has any authorized role
    from general import check_authorized
    has_authorized_role = check_authorized(interaction.user.roles)
    
    try:
        movies_list, _ = await load_movie_data()
        view = MovieListView(interaction, has_authorized_role)
        content = view.build_movie_list_text(movies_list)
        
        await interaction.response.send_message(content, view=view, ephemeral=True)
        view.message = await interaction.original_response()
        
    except Exception as e:
        logger.error(f"Error in movie-list command: {e}")
        await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)


@discord.app_commands.command(name="movie-test", description="Test movie selection without affecting the list or events (Authorized roles only)")
async def movie_test(interaction: discord.Interaction):
    # Check if user has any authorized role
    from general import check_authorized
    has_authorized_role = check_authorized(interaction.user.roles)
    
    if not has_authorized_role:
        await interaction.response.send_message("Sorry, you don't have permission to use this command!", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        from general import load_settings, format_days, format_time
        settings = load_settings()
        
        # Load movie list and metadata
        movies, metadata = await load_movie_data()
        
        if not movies:
            await interaction.followup.send("❌ The movie list is empty! Add some movies with `/addmovie` first.", ephemeral=True)
            return
        
        # Check active selection
        active_selection = metadata.get('active_movie_selection')
        
        # Simulate scheduled selection (checks for active, cleans expired)
        scheduled_movie, scheduled_index = await select_random_movie_internal(movies.copy())
        scheduled_position = scheduled_index + 1
        scheduled_ordinal = f"{scheduled_position}{get_ordinal_suffix(scheduled_position)}"
        
        # Calculate next watch time
        next_watch_datetime = calculate_next_watch_datetime(settings)
        
        # Simulate manual selection - movienight mode (with previous movie re-add if active exists)
        movienight_movies = movies.copy()
        would_readd = False
        if active_selection:
            previous_movie = active_selection.get('movie')
            if previous_movie:
                movienight_movies.append(previous_movie)
                would_readd = True
        
        movienight_movie, movienight_index = await select_random_movie_internal(movienight_movies)
        movienight_position = movienight_index + 1
        movienight_ordinal = f"{movienight_position}{get_ordinal_suffix(movienight_position)}"
        
        # Simulate now mode (no changes to list or metadata)
        now_movie, now_index = await select_random_movie_internal(movies.copy())
        now_position = now_index + 1
        now_ordinal = f"{now_position}{get_ordinal_suffix(now_position)}"
        
        # Build simulation report
        report = "🎬 **Movie Selection Test**\n\n"
        
        # Current state
        report += f"**Current State:**\n"
        report += f"• Movies in list: {len(movies)}\n"
        if active_selection:
            import pytz
            from datetime import datetime
            timezone = pytz.timezone('Europe/Oslo')
            watch_dt = datetime.fromtimestamp(active_selection.get('watch_datetime'), tz=timezone)
            report += f"• Active movie: **{active_selection.get('movie')}** (position {active_selection.get('list_position')} of {active_selection.get('total_movies')})\n"
            report += f"• Watch time: {watch_dt.strftime('%Y-%m-%d %H:%M')}\n"
            report += f"• Event count: {len(active_selection.get('event_ids', {}))} guild(s)\n"
        else:
            report += f"• No active movie selection\n"
        report += f"• Next watch time: {next_watch_datetime.strftime('%Y-%m-%d %H:%M')}\n\n"
        
        # Scheduled selection simulation
        report += f"**📅 Scheduled Selection (choose_random_movie):**\n"
        if active_selection:
            report += f"⚠️ Would **SKIP** - active movie exists\n"
            report += f"• Active movie: {active_selection.get('movie')}\n\n"
        else:
            report += f"• Would select: **{scheduled_movie}**\n"
            report += f"• List position: {scheduled_ordinal} of {len(movies)}\n"
            report += f"• Event name: \"🎬 Movie Night: {scheduled_movie}\"\n"
            report += f"• Would create active selection with watch time: {next_watch_datetime.strftime('%Y-%m-%d %H:%M')}\n"
            report += f"• Would remove movie from list\n"
            report += f"• Would create new events in all guilds\n\n"
        
        # Manual selection simulation
        report += f"**👤 Manual Selection - MOVIENIGHT mode (/movie-choose movienight):**\n"
        if would_readd:
            report += f"• Would re-add active movie: **{active_selection.get('movie')}**\n"
            report += f"• Would delete {len(active_selection.get('event_ids', {}))} previous event(s)\n"
            report += f"• Selection pool: {len(movienight_movies)} movies ({len(movies)} + 1 re-added)\n"
        else:
            report += f"• No active movie to re-add\n"
            report += f"• Selection pool: {len(movienight_movies)} movies\n"
        report += f"• Would select: **{movienight_movie}**\n"
        report += f"• List position: {movienight_ordinal} of {len(movienight_movies)}\n"
        report += f"• Event name: \"🎬 Movie Night: {movienight_movie}\"\n"
        report += f"• Would create active selection with watch time: {next_watch_datetime.strftime('%Y-%m-%d %H:%M')}\n"
        report += f"• Would remove selected movie from list\n"
        report += f"• Would create new events in all guilds\n\n"
        
        # Now mode simulation
        from datetime import datetime, timedelta
        import pytz
        timezone = pytz.timezone('Europe/Oslo')
        now_start = datetime.now(timezone) + timedelta(minutes=5)
        report += f"**⚡ Immediate Selection - NOW mode (/movie-choose now):**\n"
        report += f"• Would select: **{now_movie}**\n"
        report += f"• List position: {now_ordinal} of {len(movies)}\n"
        report += f"• Event name: \"🎬 Movie Night NOW: {now_movie}\"\n"
        report += f"• Event starts: {now_start.strftime('%Y-%m-%d %H:%M')} (in 5 minutes)\n"
        report += f"• Would **REMOVE** movie from list\n"
        report += f"• Would **NOT** modify metadata or active selection\n"
        report += f"• Would **NOT** delete any existing events\n"
        report += f"• Would create new immediate events in all guilds\n\n"
        
        report += f"**Note:** This is a simulation only. No changes were made to the movie list, metadata, or events."
        
        await interaction.followup.send(report, ephemeral=True)
        logger.info(f"User {interaction.user.display_name} ran movie-test simulation")
        
    except Exception as e:
        logger.error(f"Error in movie-test command: {e}")
        await interaction.followup.send(f"Sorry, I encountered an error during the test: {str(e)}", ephemeral=True)


async def cleanup_movies_on_startup():
    """Clean up expired movie selections on bot startup."""
    try:
        from general import load_settings
        settings = load_settings()
        
        movies, metadata = await load_movie_data()
        movies, metadata, was_cleaned = await cleanup_expired_movie_selection(movies, metadata, settings)
        
        if was_cleaned:
            await save_movie_data(movies, metadata)
            logger.info("Cleaned up expired movie selection on startup")
        else:
            logger.info("No expired movie selection to clean up on startup")
            
    except Exception as e:
        logger.error(f"Error cleaning up movies on startup: {e}")
