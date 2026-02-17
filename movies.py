import discord
from discord.ext import commands
from discord import ui
import logging
import os
import json
import random

logger = logging.getLogger(__name__)


async def load_movie_data():
    """Load movie list from JSON file."""
    logger.info("Loading movie data")
    movie_path = os.path.join(os.path.dirname(__file__), "movie_list.json")
    
    if os.path.exists(movie_path):
        with open(movie_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("movies", [])
    else:
        return []


async def save_movie_data(movies):
    """Save movie list to JSON file."""
    logger.info("Saving movie data")
    movie_path = os.path.join(os.path.dirname(__file__), "movie_list.json")
    
    with open(movie_path, "w", encoding="utf-8") as f:
        json.dump({"movies": movies}, f, ensure_ascii=False, indent=2)


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
        
        # Load movie list
        movies = await load_movie_data()
        
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
        
        # Choose random movie
        chosen_movie = random.choice(movies)
        
        # Remove from list
        movies.remove(chosen_movie)
        await save_movie_data(movies)
        
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
                    description=f"Join us for movie night!\n\n🎥 **Movie:** {chosen_movie}\n🍿 Get your snacks ready!",
                    start_time=event_start,
                    end_time=event_end,
                    entity_type=discord.EntityType.external,
                    location=movie_channel_name,
                    privacy_level=discord.PrivacyLevel.guild_only
                )
                logger.info(f"Created event '{event.name}' in guild '{guild.name}' (ID: {event.id})")
            except Exception as e:
                logger.error(f"Failed to create event in guild '{guild.name}': {e}")
            
            # Send message with event link
            if channel:
                try:
                    message = (
                        f"🎬 **Movie Selected:** {chosen_movie}\n\n"
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
        
        logger.info(f"Chose movie: {chosen_movie}. {len(movies)} remaining in list.")
        
    except Exception as e:
        logger.error(f"Error in choose_random_movie: {e}")


# Command definitions
@discord.app_commands.command(name="movie-add", description="Add a movie to the movie night list")
@discord.app_commands.describe(movie="The name of the movie to add")
async def movie_add(interaction: discord.Interaction, movie: str):
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Load current movie list
        movies = await load_movie_data()
        
        # Check if movie already exists (case-insensitive)
        if any(m.lower() == movie.lower() for m in movies):
            await interaction.followup.send(f"❌ '{movie}' is already in the list!", ephemeral=True)
            return
        
        # Add movie to list
        movies.append(movie)
        await save_movie_data(movies)
        
        await interaction.followup.send(f"✅ Added '{movie}' to the movie list! ({len(movies)} movies total)", ephemeral=True)
        logger.info(f"User {interaction.user.display_name} added movie: {movie}")
        
    except Exception as e:
        logger.error(f"Error in addmovie command: {e}")
        try:
            await interaction.followup.send("Sorry, I'm having trouble adding the movie right now. Please try again later.")
        except:
            logger.error("Failed to send error message to user")


@discord.app_commands.command(name="movie-choose", description="Choose a random movie from the list (Authorized roles only)")
async def movie_choose(interaction: discord.Interaction):
    # Check if user has any authorized role
    from general import check_authorized
    has_authorized_role = check_authorized(interaction.user.roles)
    
    if not has_authorized_role:
        await interaction.response.send_message("Sorry, you don't have permission to use this command!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
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
        movies_list = await load_movie_data()
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
        
        await save_movie_data(movies_list)
        
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
        movies_list = await load_movie_data()
        for movie_name in movies_to_remove:
            if movie_name in movies_list:
                movies_list.remove(movie_name)
        
        await save_movie_data(movies_list)
        
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
        movies_list = await load_movie_data()
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
        movies_list = await load_movie_data()
        
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
        movies_list = await load_movie_data()
        view = MovieListView(interaction, has_authorized_role)
        content = view.build_movie_list_text(movies_list)
        
        await interaction.response.send_message(content, view=view, ephemeral=True)
        view.message = await interaction.original_response()
        
    except Exception as e:
        logger.error(f"Error in movie-list command: {e}")
        await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)
