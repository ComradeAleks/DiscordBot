import discord
from discord import ui
import logging
import os
import json
import random
import time
from datetime import datetime, timedelta
import pytz

from general import load_settings, format_days, format_time, check_authorized

logger = logging.getLogger(__name__)

TIMEZONE = pytz.timezone('Europe/Oslo')
MOVIE_PATH = os.path.join(os.path.dirname(__file__), "movie_list.json")
DEFAULT_TAGS = ["Holiday", "Scary", "Funny", "Animated", "Adventure", "Drama"]

# Discord allows at most 25 options per select menu
MOVIES_PER_PICKER_PAGE = 25
# Movies shown per page in /movie-list (keeps the message under Discord's 2000 character limit)
MOVIES_PER_LIST_PAGE = 20


def normalize_movie(item):
    """Convert a stored movie to {"title": str, "tags": [str]} (old lists stored plain strings)."""
    if isinstance(item, str):
        return {"title": item, "tags": []}
    return {"title": item.get("title", ""), "tags": list(item.get("tags", []))}


async def load_movie_data():
    """Load movie list and metadata from JSON file."""
    logger.info("Loading movie data")

    if os.path.exists(MOVIE_PATH):
        with open(MOVIE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            movies = [normalize_movie(m) for m in data.get("movies", [])]
            metadata = data.get("metadata", {
                "active_movie_selection": None
            })
            return movies, metadata
    else:
        return [], {"active_movie_selection": None}


async def save_movie_data(movies, metadata=None):
    """Save movie list and metadata to JSON file."""
    logger.info("Saving movie data")

    # Load existing metadata if not provided
    if metadata is None:
        _, metadata = await load_movie_data()

    with open(MOVIE_PATH, "w", encoding="utf-8") as f:
        json.dump({"movies": movies, "metadata": metadata}, f, ensure_ascii=False, indent=2)


def find_movie(movies, title):
    """Find a movie by title (case-insensitive)."""
    return next((m for m in movies if m["title"].lower() == title.lower()), None)


def format_movie(movie):
    """Movie title with its tags, e.g. 'The Thing [Scary]'."""
    if movie["tags"]:
        return f"{movie['title']} [{', '.join(movie['tags'])}]"
    return movie["title"]


def shorten(text, max_length):
    return text if len(text) <= max_length else text[:max_length - 1] + "…"


def get_ordinal_suffix(n):
    """Get ordinal suffix for a number (1st, 2nd, 3rd, etc.)."""
    if 10 <= n % 100 <= 20:
        return 'th'
    else:
        return {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')


# Tags and tag days

def get_tags(settings):
    """All tags that can be given to movies."""
    return settings.get("movie", {}).get("tags", DEFAULT_TAGS)


def get_tag_days(settings):
    """Tag days: date ranges where the randomizer only picks movies with certain tags.

    Format: {"name": "Halloween", "start": "10-01", "end": "10-31", "tags": ["Scary"]} (dates are MM-DD, every year)
    """
    return settings.get("movie", {}).get("tag_days", [])


def parse_month_day(text):
    """Parse 'MM-DD' to a (month, day) tuple, or None if invalid."""
    try:
        # Leap year so 02-29 is accepted
        parsed = datetime.strptime(f"2000-{text.strip()}", "%Y-%m-%d")
    except (ValueError, AttributeError):
        return None
    return (parsed.month, parsed.day)


def tag_day_matches(tag_day, date):
    """Check if a date falls within a tag day's range."""
    start = parse_month_day(tag_day.get("start", ""))
    end = parse_month_day(tag_day.get("end") or tag_day.get("start", ""))
    if not start or not end:
        return False

    month_day = (date.month, date.day)
    if start <= end:
        return start <= month_day <= end
    # Range wraps over new year, e.g. 12-20 to 01-05
    return month_day >= start or month_day <= end


def get_active_tag_days(settings, date):
    return [td for td in get_tag_days(settings) if td.get("tags") and tag_day_matches(td, date)]


def format_tag_day(tag_day):
    """e.g. 'Halloween (10-01 → 10-31): Scary'"""
    start = tag_day.get("start", "?")
    end = tag_day.get("end") or start
    dates = start if end == start else f"{start} → {end}"
    return f"{tag_day.get('name', 'Unnamed')} ({dates}): {', '.join(tag_day.get('tags', []))}"


def get_selection_pool(movies, settings, date):
    """Get the movies the randomizer may pick from for a movie night on the given date.

    When tag days are active, only movies with at least one of their tags are used.
    If no movie has those tags, the whole list is used instead.

    Returns:
        tuple: (pool, active_tag_days, fell_back)
    """
    active_tag_days = get_active_tag_days(settings, date)
    if not active_tag_days:
        return movies, [], False

    wanted_tags = {tag.lower() for td in active_tag_days for tag in td["tags"]}
    pool = [m for m in movies if any(tag.lower() in wanted_tags for tag in m["tags"])]

    if not pool:
        logger.warning(f"Tag days active ({', '.join(td['name'] for td in active_tag_days)}) but no movies have their tags. Using the whole list.")
        return movies, active_tag_days, True

    return pool, active_tag_days, False


def describe_tag_filter(active_tag_days, fell_back):
    """One line for messages explaining which tag days affected the selection."""
    if not active_tag_days:
        return ""
    names = ", ".join(f"{td['name']} ({', '.join(td['tags'])})" for td in active_tag_days)
    if fell_back:
        return f"🏷️ **{names}** is active, but no movies have those tags, so the whole list was used.\n"
    return f"🏷️ **{names}** is active, so only movies with those tags were picked from.\n"


def parse_tag_list(text, all_tags):
    """Match comma-separated tag names to the existing tags (case-insensitive).

    Returns:
        tuple: (matched tags with their stored spelling, unknown names)
    """
    matched = []
    unknown = []
    for name in (t.strip() for t in text.split(",")):
        if not name:
            continue
        match = next((t for t in all_tags if t.lower() == name.lower()), None)
        if match is None:
            unknown.append(name)
        elif match not in matched:
            matched.append(match)
    return matched, unknown


def get_choose_pool(movies, settings, date, required_tags):
    """Movies /movie-choose may pick from, plus a line describing the filter.

    Tags given in the command replace the tag days: only movies with all of them are used
    (the pool is empty if none match). Without them, the tag days apply as usual.
    """
    if required_tags:
        wanted = {tag.lower() for tag in required_tags}
        pool = [m for m in movies if wanted <= {tag.lower() for tag in m["tags"]}]
        return pool, f"🏷️ Picked from movies tagged **{', '.join(required_tags)}** ({len(pool)} movies)\n"

    pool, active_tag_days, fell_back = get_selection_pool(movies, settings, date)
    return pool, describe_tag_filter(active_tag_days, fell_back)


def calculate_next_watch_datetime(settings):
    """Calculate the next movie watch datetime based on settings.

    Returns:
        datetime: Next watch datetime in Europe/Oslo timezone
    """
    now = datetime.now(TIMEZONE)

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
    watch_datetime = TIMEZONE.localize(datetime(
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

    now = datetime.now(TIMEZONE)

    # Check if watch time has passed
    watch_timestamp = active_selection.get('watch_datetime')
    if watch_timestamp:
        watch_datetime = datetime.fromtimestamp(watch_timestamp, tz=TIMEZONE)

        if now > watch_datetime:
            # Watch time has passed, clear the selection
            logger.info(f"Cleaning up expired movie selection: {active_selection.get('movie')}")
            metadata['active_movie_selection'] = None
            return movies, metadata, True

    return movies, metadata, False


def select_random_movie_internal(movies, pool):
    """Pick a random movie from the pool.

    Returns:
        tuple: (chosen_movie, chosen_index) where the index is the movie's position in the full list
    """
    chosen_movie = random.choice(pool)
    return chosen_movie, movies.index(chosen_movie)


async def create_movie_events(guilds, name, description, event_start, location):
    """Create a 2 hour external scheduled event in every guild.

    Returns:
        dict: guild id (str) -> created event
    """
    events = {}
    for guild in guilds:
        try:
            event = await guild.create_scheduled_event(
                name=name,
                description=description,
                start_time=event_start,
                end_time=event_start + timedelta(hours=2),
                entity_type=discord.EntityType.external,
                location=location,
                privacy_level=discord.PrivacyLevel.guild_only
            )
            events[str(guild.id)] = event
            logger.info(f"Created event '{event.name}' in guild '{guild.name}' (ID: {event.id})")
        except Exception as e:
            logger.error(f"Failed to create event in guild '{guild.name}': {e}")
    return events


def build_active_selection(movie, list_position, total_movies, watch_datetime, events):
    return {
        'movie': movie['title'],
        'tags': movie['tags'],
        'list_position': list_position,
        'total_movies': total_movies,
        'selected_at': time.time(),
        'watch_datetime': watch_datetime.timestamp(),
        'event_ids': {guild_id: event.id for guild_id, event in events.items()}
    }


async def choose_random_movie(bot):
    """Choose a random movie from the list and post it to movie-night channels."""
    try:
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

        if not watch_day:
            logger.info("No movie watch day set, skipping scheduled movie selection")
            return

        if not movies:
            logger.info("No movies in the list to choose from")
            # Send message to movie-night channels that there are no movies
            for guild in bot.guilds:
                channel = discord.utils.get(guild.text_channels, name=movie_channel_name)
                if channel:
                    try:
                        await channel.send("🎬 It's movie night time! But there are no movies in the list. Add some with `/movie-add`!")
                    except Exception as e:
                        logger.error(f"Failed to send no-movies message to #{channel.name}: {e}")
            return

        # Tag days are checked against the day the movie will be watched
        watch_datetime = calculate_next_watch_datetime(settings)
        pool, active_tag_days, fell_back = get_selection_pool(movies, settings, watch_datetime.date())

        chosen_movie, chosen_index = select_random_movie_internal(movies, pool)

        # Calculate position ordinal (which slot it was in the list)
        list_position = chosen_index + 1
        total_movies = len(movies)
        position_ordinal = f"{list_position}{get_ordinal_suffix(list_position)}"

        # Remove from list
        movies.remove(chosen_movie)

        events = await create_movie_events(
            bot.guilds,
            name=f"🎬 Movie Night: {chosen_movie['title']}",
            description=f"Join us for movie night!\n\n🎥 **Movie:** {chosen_movie['title']}\n📍 **List Position:** {position_ordinal} of {total_movies}\n🍿 Get your snacks ready!",
            event_start=watch_datetime,
            location=movie_channel_name
        )

        # Send message with event link
        for guild in bot.guilds:
            channel = discord.utils.get(guild.text_channels, name=movie_channel_name)
            if not channel:
                continue
            try:
                message = (
                    f"🎬 **Movie Selected:** {format_movie(chosen_movie)}\n"
                    f"📍 **Was at position:** {position_ordinal} of {total_movies}\n"
                    f"{describe_tag_filter(active_tag_days, fell_back)}\n"
                    f"📅 **Watch Day:** {watch_day_str}\n"
                    f"🕐 **Watch Time:** {watch_time_str}\n"
                )

                event = events.get(str(guild.id))
                if event:
                    message += f"\n📌 **Event:** {event.url}\n"

                message += "\n🍿 See you there!"

                await channel.send(message)
                logger.info(f"Sent movie choice '{chosen_movie['title']}' to #{channel.name}")
            except Exception as e:
                logger.error(f"Failed to send movie choice to #{channel.name}: {e}")

        # Save active selection and event IDs
        metadata['active_movie_selection'] = build_active_selection(chosen_movie, list_position, total_movies, watch_datetime, events)
        await save_movie_data(movies, metadata)

        logger.info(f"Chose movie from position {position_ordinal} of {total_movies}: {chosen_movie['title']}. {len(movies)} remaining in list. Watch time: {watch_datetime}")

    except Exception as e:
        logger.error(f"Error in choose_random_movie: {e}")


# Command definitions
@discord.app_commands.command(name="movie-add", description="Add a movie to the movie night list")
@discord.app_commands.describe(movie="The name of the movie to add")
async def movie_add(interaction: discord.Interaction, movie: str):
    await interaction.response.defer(ephemeral=True)

    try:
        movie = movie.strip()

        # Load current movie list
        movies, metadata = await load_movie_data()

        # Check if movie already exists (case-insensitive)
        if find_movie(movies, movie):
            await interaction.followup.send(f"❌ '{movie}' is already in the list!", ephemeral=True)
            return

        # Add movie to list
        movies.append({"title": movie, "tags": []})
        await save_movie_data(movies, metadata)

        content = f"✅ Added '{movie}' to the movie list! ({len(movies)} movies total)"
        tag_view = MovieTagView([movie], None)
        if tag_view.has_tags:
            await interaction.followup.send(f"{content}\n\nOptionally pick tags for it below:", view=tag_view, ephemeral=True)
        else:
            await interaction.followup.send(content, ephemeral=True)
        logger.info(f"User {interaction.user.display_name} added movie: {movie}")

    except Exception as e:
        logger.error(f"Error in movie-add command: {e}")
        try:
            await interaction.followup.send("Sorry, I'm having trouble adding the movie right now. Please try again later.")
        except:
            logger.error("Failed to send error message to user")


@discord.app_commands.command(name="movie-choose", description="Choose a random movie from the list (Authorized roles only)")
@discord.app_commands.describe(
    mode="movienight = scheduled movie night | now = immediate movie night (starts in 5 min)",
    tags="Only pick movies that have ALL of these tags, separated by commas (e.g. Scary, Funny)"
)
@discord.app_commands.choices(mode=[
    discord.app_commands.Choice(name="movienight", value="movienight"),
    discord.app_commands.Choice(name="now", value="now")
])
async def movie_choose(interaction: discord.Interaction, mode: discord.app_commands.Choice[str], tags: str = None):
    # Check if user has any authorized role
    has_authorized_role = check_authorized(interaction.user.roles)

    if not has_authorized_role:
        await interaction.response.send_message("Sorry, you don't have permission to use this command!", ephemeral=True)
        return

    await interaction.response.defer()

    try:
        settings = load_settings()
        movie_channel_name = settings.get("movie", {}).get("channel", "movie-night")

        # Load movie list and metadata
        movies, metadata = await load_movie_data()

        if not movies:
            await interaction.followup.send("❌ The movie list is empty! Add some movies with `/movie-add` first.")
            return

        required_tags, unknown_tags = parse_tag_list(tags or "", get_tags(settings))
        if unknown_tags:
            await interaction.followup.send(
                f"❌ Unknown tag(s): {', '.join(unknown_tags)}\n\nAvailable tags: {', '.join(get_tags(settings))}"
            )
            return

        # Handle different modes
        if mode.value == "now":
            # NOW mode: Quick movie night starting in 5 minutes, removes movie but doesn't affect metadata
            event_start = datetime.now(TIMEZONE) + timedelta(minutes=5)
            pool, filter_line = get_choose_pool(movies, settings, event_start.date(), required_tags)
            if not pool:
                await interaction.followup.send(f"❌ No movies have all of these tags: {', '.join(required_tags)}")
                return

            # Select a random movie
            chosen_movie, chosen_index = select_random_movie_internal(movies, pool)
            list_position = chosen_index + 1
            total_movies = len(movies)
            position_ordinal = f"{list_position}{get_ordinal_suffix(list_position)}"

            # Remove movie from list and save (but don't modify metadata)
            movies.remove(chosen_movie)
            await save_movie_data(movies, metadata)

            events = await create_movie_events(
                interaction.client.guilds,
                name=f"🎬 Movie Night NOW: {chosen_movie['title']}",
                description=f"Impromptu movie night!\n\n🎥 **Movie:** {chosen_movie['title']}\n⏰ **Starting in 5 minutes!**\n🍿 Get ready!",
                event_start=event_start,
                location=movie_channel_name
            )

            created_guilds = [guild.name for guild in interaction.client.guilds if str(guild.id) in events]
            guilds_text = ", ".join(created_guilds) if created_guilds else "No guilds"
            await interaction.followup.send(
                f"🎬 **Immediate Movie Night!**\n"
                f"🎥 **Movie:** {format_movie(chosen_movie)}\n"
                f"📍 **Was at position:** {position_ordinal} of {total_movies}\n"
                f"{filter_line}"
                f"⏰ **Starting:** <t:{int(event_start.timestamp())}:R>\n\n"
                f"🎭 Events created in: {guilds_text}\n"
                f"({len(movies)} movies remaining in list)"
            )
            logger.info(f"User {interaction.user.display_name} started immediate movie night: {chosen_movie['title']}")

        else:  # mode.value == "movienight"
            # MOVIENIGHT mode: Normal scheduled movie night (current behavior)
            if not settings.get("movie", {}).get("watch_day", [5]):
                await interaction.followup.send("❌ No movie watch day is set. Pick one in `/comrade-settings`, or use `now` mode.")
                return

            # Clean up expired selection if exists
            movies, metadata, was_cleaned = await cleanup_expired_movie_selection(movies, metadata, settings)
            if was_cleaned:
                await save_movie_data(movies, metadata)

            # Re-add previous movie to list if active selection exists
            active_selection = metadata.get('active_movie_selection')
            if active_selection:
                previous_movie = active_selection.get('movie')
                if previous_movie and not find_movie(movies, previous_movie):
                    movies.append({"title": previous_movie, "tags": active_selection.get('tags', [])})
                    logger.info(f"Re-added active movie to list: {previous_movie}")

            # Tag days are checked against the day the movie will be watched
            watch_datetime = calculate_next_watch_datetime(settings)
            pool, filter_line = get_choose_pool(movies, settings, watch_datetime.date(), required_tags)
            if not pool:
                # Nothing has been changed yet, so the current selection and its events are kept
                await interaction.followup.send(f"❌ No movies have all of these tags: {', '.join(required_tags)}")
                return

            if active_selection:
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

            chosen_movie, chosen_index = select_random_movie_internal(movies, pool)

            # Calculate position ordinal (which slot it was in the list)
            list_position = chosen_index + 1
            total_movies = len(movies)
            position_ordinal = f"{list_position}{get_ordinal_suffix(list_position)}"

            events = await create_movie_events(
                interaction.client.guilds,
                name=f"🎬 Movie Night: {chosen_movie['title']}",
                description=f"Join us for movie night!\n\n🎥 **Movie:** {chosen_movie['title']}\n📍 **List Position:** {position_ordinal} of {total_movies}\n🍿 Get your snacks ready!",
                event_start=watch_datetime,
                location=movie_channel_name
            )

            # Remove chosen movie from list and save active selection
            movies.remove(chosen_movie)
            metadata['active_movie_selection'] = build_active_selection(chosen_movie, list_position, total_movies, watch_datetime, events)
            await save_movie_data(movies, metadata)

            await interaction.followup.send(
                f"🎬 **Movie Selected:** {format_movie(chosen_movie)}\n"
                f"📍 **Was at position:** {position_ordinal} of {total_movies}\n"
                f"{filter_line}\n"
                f"🍿 Enjoy the show!\n\n"
                f"({len(movies)} movies remaining in list)"
            )
            logger.info(f"User {interaction.user.display_name} chose movie from position {position_ordinal}: {chosen_movie['title']}")

    except Exception as e:
        logger.error(f"Error in movie-choose command: {e}")
        try:
            await interaction.followup.send("Sorry, I'm having trouble choosing a movie right now. Please try again later.")
        except:
            logger.error("Failed to send error message to user")


@movie_choose.autocomplete("tags")
async def movie_choose_tags_autocomplete(interaction: discord.Interaction, current: str):
    """Suggest tags for the last comma-separated entry, keeping the ones already typed."""
    all_tags = get_tags(load_settings())
    parts = [p.strip() for p in current.split(",")]
    typed, partial = [p for p in parts[:-1] if p], parts[-1].lower()
    typed_lower = {p.lower() for p in typed}
    prefix = ", ".join(typed)

    choices = []
    for tag in all_tags:
        if tag.lower() in typed_lower or not tag.lower().startswith(partial):
            continue
        value = f"{prefix}, {tag}" if prefix else tag
        choices.append(discord.app_commands.Choice(name=value, value=value))
    return choices[:25]


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
            if find_movie(movies_list, name):
                already_exists.append(name)
            elif not any(n.lower() == name.lower() for n in movies_to_add):
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
            movies_list.append({"title": movie_name, "tags": []})

        await save_movie_data(movies_list, metadata)

        added_count = len(movies_to_add)
        content = f"✅ Added {added_count} movie{'s' if added_count != 1 else ''}!\n\nTotal movies: {len(movies_list)}"
        tag_view = MovieTagView(movies_to_add, self.movie_list_view)
        if tag_view.has_tags:
            await interaction.followup.send(f"{content}\n\n{tag_view.build_content()}", view=tag_view, ephemeral=True)
        else:
            await interaction.followup.send(content, ephemeral=True)

        # Refresh the main movie list message
        await self.movie_list_view.refresh_message(interaction)


# Select menu for picking the tags of one or more movies
class MovieTagSelect(ui.Select):
    def __init__(self, titles, all_tags, current_tags, movie_list_view):
        self.titles = titles
        self.all_tags = all_tags
        self.movie_list_view = movie_list_view

        options = [
            discord.SelectOption(label=tag, value=tag, default=(tag.lower() in current_tags))
            for tag in all_tags[:25]
        ]

        super().__init__(
            placeholder="Select tags (deselect all for no tags)",
            min_values=0,
            max_values=len(options),
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        # Keep tags in the same order as the tag list in settings
        selected = [tag for tag in self.all_tags if tag in self.values]

        movies_list, metadata = await load_movie_data()
        updated = []
        for title in self.titles:
            movie = find_movie(movies_list, title)
            if movie:
                movie["tags"] = selected
                updated.append(movie["title"])
        await save_movie_data(movies_list, metadata)

        tags_text = ", ".join(selected) if selected else "no tags"
        if len(updated) == 1:
            content = f"✅ **{updated[0]}** now has: {tags_text}"
        else:
            content = f"✅ Updated {len(updated)} movies to: {tags_text}"
        await interaction.response.edit_message(content=content, view=None)

        if self.movie_list_view:
            await self.movie_list_view.refresh_message(interaction)


# View for tagging one or more movies
class MovieTagView(ui.View):
    def __init__(self, titles, movie_list_view, current_movies=None):
        super().__init__(timeout=300)
        self.titles = titles

        all_tags = get_tags(load_settings())
        self.has_tags = bool(all_tags)

        # Pre-select the tags that all of the movies already share
        current_tags = set()
        if current_movies:
            current_tags = set.intersection(*({t.lower() for t in m["tags"]} for m in current_movies))

        if self.has_tags:
            self.add_item(MovieTagSelect(titles, all_tags, current_tags, movie_list_view))

    def build_content(self):
        if len(self.titles) == 1:
            return f"Pick the tags for **{self.titles[0]}**:"
        names = ", ".join(self.titles[:10])
        if len(self.titles) > 10:
            names += f" and {len(self.titles) - 10} more"
        return f"Pick the tags for these {len(self.titles)} movies (replaces their current tags): {names}"


# Select menu for picking movies from one page of the list (to remove or tag them)
class MoviePickerSelect(ui.Select):
    def __init__(self, page_movies, picker_view):
        self.picker_view = picker_view
        self.page_movies = page_movies  # List of (list index, movie)

        options = [
            discord.SelectOption(
                label=movie["title"][:100],  # Discord label limit
                value=str(i),
                description=(f"Movie #{i+1}" + (f" • {', '.join(movie['tags'])}" if movie["tags"] else ""))[:100]
            )
            for i, movie in page_movies
        ]

        placeholder = "Select movie(s) to remove" if picker_view.mode == "remove" else "Select movie(s) to tag"
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=len(options),
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        # Get selected movies by title (the list may have changed since the menu was shown)
        titles_by_index = {i: movie["title"] for i, movie in self.page_movies}
        selected_titles = [titles_by_index[int(v)] for v in self.values]
        movie_list_view = self.picker_view.movie_list_view

        movies_list, metadata = await load_movie_data()
        selected_movies = [m for m in (find_movie(movies_list, t) for t in selected_titles) if m]

        if not selected_movies:
            await interaction.response.edit_message(content="❌ Those movies are no longer in the list.", view=None)
            return

        if self.picker_view.mode == "remove":
            for movie in selected_movies:
                movies_list.remove(movie)
            await save_movie_data(movies_list, metadata)

            removed_count = len(selected_movies)
            await interaction.response.edit_message(
                content=f"✅ Removed {removed_count} movie{'s' if removed_count != 1 else ''}!\n\nRemaining movies: {len(movies_list)}",
                view=None
            )
            await movie_list_view.refresh_message(interaction)
        else:
            tag_view = MovieTagView([m["title"] for m in selected_movies], movie_list_view, selected_movies)
            if not tag_view.has_tags:
                await interaction.response.edit_message(content="❌ No tags exist yet. Add some in `/comrade-settings`.", view=None)
                return
            await interaction.response.edit_message(content=tag_view.build_content(), view=tag_view)


# View for picking movies, with page buttons when the list is longer than one menu
class MoviePickerView(ui.View):
    def __init__(self, movies_list, movie_list_view, mode, page=0):
        super().__init__(timeout=120)
        self.movies_list = movies_list
        self.movie_list_view = movie_list_view
        self.mode = mode  # "remove" or "tag"
        self.page_count = max(1, -(-len(movies_list) // MOVIES_PER_PICKER_PAGE))
        self.page = max(0, min(page, self.page_count - 1))

        start = self.page * MOVIES_PER_PICKER_PAGE
        page_movies = list(enumerate(movies_list))[start:start + MOVIES_PER_PICKER_PAGE]
        self.add_item(MoviePickerSelect(page_movies, self))

        if self.page_count > 1:
            self.prev_button.disabled = self.page == 0
            self.next_button.disabled = self.page == self.page_count - 1
        else:
            self.remove_item(self.prev_button)
            self.remove_item(self.next_button)

    def build_content(self):
        action = "remove" if self.mode == "remove" else "tag"
        if self.page_count == 1:
            return f"Select movie(s) to {action}:"
        start = self.page * MOVIES_PER_PICKER_PAGE
        end = min(start + MOVIES_PER_PICKER_PAGE, len(self.movies_list))
        return f"Select movie(s) to {action} (page {self.page + 1}/{self.page_count}, movies {start + 1}-{end} of {len(self.movies_list)}):"

    async def show_page(self, interaction: discord.Interaction, page):
        # Reload so the page reflects movies added or removed in the meantime
        movies_list, _ = await load_movie_data()
        if not movies_list:
            await interaction.response.edit_message(content="❌ The movie list is empty!", view=None)
            return
        view = MoviePickerView(movies_list, self.movie_list_view, self.mode, page)
        await interaction.response.edit_message(content=view.build_content(), view=view)

    @ui.button(label="Previous", style=discord.ButtonStyle.secondary, emoji="◀️", row=1)
    async def prev_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.show_page(interaction, self.page - 1)

    @ui.button(label="Next", style=discord.ButtonStyle.secondary, emoji="▶️", row=1)
    async def next_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.show_page(interaction, self.page + 1)


# Main movie list view with buttons
class MovieListView(ui.View):
    def __init__(self, original_interaction, has_authorized_role):
        super().__init__(timeout=300)
        self.original_interaction = original_interaction
        self.has_authorized_role = has_authorized_role
        self.message = None
        self.page = 0
        self.page_count = 1

    def update_items(self, movies_list):
        """Show the buttons that apply to this user and the current number of pages."""
        self.page_count = max(1, -(-len(movies_list) // MOVIES_PER_LIST_PAGE))
        self.page = max(0, min(self.page, self.page_count - 1))

        self.clear_items()
        self.add_item(self.add_button)
        if self.has_authorized_role:
            self.add_item(self.tag_button)
            self.add_item(self.remove_button)
        if self.page_count > 1:
            self.prev_button.disabled = self.page == 0
            self.next_button.disabled = self.page == self.page_count - 1
            self.add_item(self.prev_button)
            self.add_item(self.next_button)

    async def refresh_message(self, interaction: discord.Interaction = None):
        """Refresh the movie list display."""
        movies_list, _ = await load_movie_data()
        self.update_items(movies_list)
        content = self.build_movie_list_text(movies_list)

        try:
            await self.message.edit(content=content, view=self)
        except:
            pass

    def build_movie_list_text(self, movies_list):
        """Build the movie list display text for the current page."""
        if not movies_list:
            text = "📽️ **Movie List**\n\nThe movie list is empty!"
        else:
            start = self.page * MOVIES_PER_LIST_PAGE
            page_movies = list(enumerate(movies_list))[start:start + MOVIES_PER_LIST_PAGE]
            movies_display = "\n".join(shorten(f"{i+1}. {format_movie(m)}", 70) for i, m in page_movies)

            text = f"**🎬 Movie List ({len(movies_list)} movies)**"
            if self.page_count > 1:
                text += f" - page {self.page + 1}/{self.page_count}"
            text += f"\n```\n{movies_display}\n```"

        # Show whether a tag day affects the next movie night
        settings = load_settings()
        if settings.get("movie", {}).get("watch_day", [5]):
            watch_date = calculate_next_watch_datetime(settings).date()
            active_tag_days = get_active_tag_days(settings, watch_date)
            if active_tag_days:
                names = ", ".join(f"{td['name']} ({', '.join(td['tags'])})" for td in active_tag_days)
                text += f"\n🏷️ Next movie night ({watch_date:%d.%m}) only picks movies tagged: **{names}**\n"

        text += "\n*Use the buttons below to add movies.*"
        if self.has_authorized_role:
            text += " Authorized users can also tag and remove movies."

        return text

    async def show_picker(self, interaction: discord.Interaction, mode):
        movies_list, _ = await load_movie_data()

        if not movies_list:
            await interaction.response.send_message("❌ The movie list is empty!", ephemeral=True)
            return

        view = MoviePickerView(movies_list, self, mode)
        await interaction.response.send_message(view.build_content(), view=view, ephemeral=True)

    @ui.button(label="Add Movies", style=discord.ButtonStyle.success, emoji="➕", row=0)
    async def add_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(AddMovieModal(self))

    @ui.button(label="Edit Tags", style=discord.ButtonStyle.primary, emoji="🏷️", row=0)
    async def tag_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.show_picker(interaction, "tag")

    @ui.button(label="Remove Movies", style=discord.ButtonStyle.danger, emoji="➖", row=0)
    async def remove_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.show_picker(interaction, "remove")

    @ui.button(label="Previous", style=discord.ButtonStyle.secondary, emoji="◀️", row=1)
    async def prev_button(self, interaction: discord.Interaction, button: ui.Button):
        self.page -= 1
        await self.show_current_page(interaction)

    @ui.button(label="Next", style=discord.ButtonStyle.secondary, emoji="▶️", row=1)
    async def next_button(self, interaction: discord.Interaction, button: ui.Button):
        self.page += 1
        await self.show_current_page(interaction)

    async def show_current_page(self, interaction: discord.Interaction):
        movies_list, _ = await load_movie_data()
        self.update_items(movies_list)
        await interaction.response.edit_message(content=self.build_movie_list_text(movies_list), view=self)


@discord.app_commands.command(name="movie-list", description="View or manage the movie list")
async def movie_list(interaction: discord.Interaction):
    # Check if user has any authorized role
    has_authorized_role = check_authorized(interaction.user.roles)

    try:
        movies_list, _ = await load_movie_data()
        view = MovieListView(interaction, has_authorized_role)
        view.update_items(movies_list)
        content = view.build_movie_list_text(movies_list)

        await interaction.response.send_message(content, view=view, ephemeral=True)
        view.message = await interaction.original_response()

    except Exception as e:
        logger.error(f"Error in movie-list command: {e}")
        await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)


@discord.app_commands.command(name="movie-test", description="Test movie selection without affecting the list or events (Authorized roles only)")
async def movie_test(interaction: discord.Interaction):
    # Check if user has any authorized role
    has_authorized_role = check_authorized(interaction.user.roles)

    if not has_authorized_role:
        await interaction.response.send_message("Sorry, you don't have permission to use this command!", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        settings = load_settings()

        # Load movie list and metadata
        movies, metadata = await load_movie_data()

        if not movies:
            await interaction.followup.send("❌ The movie list is empty! Add some movies with `/movie-add` first.", ephemeral=True)
            return

        # Check active selection
        active_selection = metadata.get('active_movie_selection')

        # Calculate next watch time and the movies tag days allow for it
        next_watch_datetime = calculate_next_watch_datetime(settings)
        scheduled_pool, scheduled_tag_days, scheduled_fell_back = get_selection_pool(movies, settings, next_watch_datetime.date())

        # Simulate scheduled selection (checks for active, cleans expired)
        scheduled_movie, scheduled_index = select_random_movie_internal(movies, scheduled_pool)
        scheduled_ordinal = f"{scheduled_index + 1}{get_ordinal_suffix(scheduled_index + 1)}"

        # Simulate manual selection - movienight mode (with previous movie re-add if active exists)
        movienight_movies = list(movies)
        would_readd = False
        if active_selection:
            previous_movie = active_selection.get('movie')
            if previous_movie and not find_movie(movies, previous_movie):
                movienight_movies.append({"title": previous_movie, "tags": active_selection.get('tags', [])})
                would_readd = True

        movienight_pool, _, _ = get_selection_pool(movienight_movies, settings, next_watch_datetime.date())
        movienight_movie, movienight_index = select_random_movie_internal(movienight_movies, movienight_pool)
        movienight_ordinal = f"{movienight_index + 1}{get_ordinal_suffix(movienight_index + 1)}"

        # Simulate now mode (no changes to list or metadata)
        now_start = datetime.now(TIMEZONE) + timedelta(minutes=5)
        now_pool, now_tag_days, now_fell_back = get_selection_pool(movies, settings, now_start.date())
        now_movie, now_index = select_random_movie_internal(movies, now_pool)
        now_ordinal = f"{now_index + 1}{get_ordinal_suffix(now_index + 1)}"

        def pool_line(pool, tag_days, fell_back):
            if not tag_days:
                return f"• Selection pool: all {len(pool)} movies (no tag day active)\n"
            line = f"• Tag day: {', '.join(format_tag_day(td) for td in tag_days)}\n"
            if fell_back:
                return line + f"• ⚠️ No movies have those tags, would use all {len(pool)} movies\n"
            return line + f"• Selection pool: {len(pool)} tagged movies\n"

        # Build simulation report
        report = "🎬 **Movie Selection Test**\n\n"

        # Current state
        report += f"**Current State:**\n"
        report += f"• Movies in list: {len(movies)}\n"
        if active_selection:
            watch_dt = datetime.fromtimestamp(active_selection.get('watch_datetime'), tz=TIMEZONE)
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
            report += pool_line(scheduled_pool, scheduled_tag_days, scheduled_fell_back)
            report += f"• Would select: **{format_movie(scheduled_movie)}**\n"
            report += f"• List position: {scheduled_ordinal} of {len(movies)}\n"
            report += f"• Would create active selection with watch time: {next_watch_datetime.strftime('%Y-%m-%d %H:%M')}\n"
            report += f"• Would remove movie from list and create new events in all guilds\n\n"

        # Manual selection simulation
        report += f"**👤 Manual Selection - MOVIENIGHT mode (/movie-choose movienight):**\n"
        if would_readd:
            report += f"• Would re-add active movie: **{active_selection.get('movie')}**\n"
            report += f"• Would delete {len(active_selection.get('event_ids', {}))} previous event(s)\n"
        report += pool_line(movienight_pool, scheduled_tag_days, scheduled_fell_back)
        report += f"• Would select: **{format_movie(movienight_movie)}**\n"
        report += f"• List position: {movienight_ordinal} of {len(movienight_movies)}\n"
        report += f"• Would remove selected movie from list and create new events in all guilds\n\n"

        # Now mode simulation
        report += f"**⚡ Immediate Selection - NOW mode (/movie-choose now):**\n"
        report += pool_line(now_pool, now_tag_days, now_fell_back)
        report += f"• Would select: **{format_movie(now_movie)}**\n"
        report += f"• List position: {now_ordinal} of {len(movies)}\n"
        report += f"• Event starts: {now_start.strftime('%Y-%m-%d %H:%M')} (in 5 minutes)\n"
        report += f"• Would **REMOVE** movie from list, but **NOT** modify the active selection or existing events\n\n"

        report += f"**Note:** This is a simulation only. No changes were made to the movie list, metadata, or events."

        await interaction.followup.send(report, ephemeral=True)
        logger.info(f"User {interaction.user.display_name} ran movie-test simulation")

    except Exception as e:
        logger.error(f"Error in movie-test command: {e}")
        await interaction.followup.send(f"Sorry, I encountered an error during the test: {str(e)}", ephemeral=True)


async def cleanup_movies_on_startup():
    """Clean up expired movie selections on bot startup."""
    try:
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
