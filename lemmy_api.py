import aiohttp
import asyncio
import logging
import re
from datetime import datetime
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Questions should have more than this many words (lowered automatically if nothing else is available)
MIN_WORDS = 5

# Posts need at least this many points (upvotes minus downvotes), which filters out trolling and low-effort posts
MIN_SCORE = 20

# Instance used when a community is given without one, e.g. "asklemmy"
DEFAULT_INSTANCE = "lemmy.world"

DEFAULT_COMMUNITIES = [
    "asklemmy@lemmy.world",
    "nostupidquestions@lemmy.world",
    "asklemmy@lemmy.ml"
]

HEADERS = {
    'User-Agent': 'qotd-discord-bot/1.0',
    'Accept': 'application/json'
}

TIMEOUT = aiohttp.ClientTimeout(total=20)

# Posts whose title or text contains any of these are skipped. Words match whole words only,
# a * at the end also matches longer words ("sex*" catches "sexual"). Add words here if unwanted questions get through.
BLOCKED_WORDS = [
    # Sexual
    "sex*", "porn*", "nude*", "naked", "penis*", "vagina*", "dick", "dicks", "cock", "cocks", "boob*", "tits",
    "horny", "orgasm*", "masturbat*", "fetish*", "kink*", "erotic*", "onlyfans", "hooker*", "prostitut*",
    "genital*", "virgin", "virginity", "age of consent", "pedo", "pedos", "pedophil*", "paedophil*", "incest*",
    "rape*", "raping", "rapist*", "molest*", "grope*", "groping",
    # Violence and self-harm
    "suicid*", "kill myself", "self harm", "self-harm", "murder*", "gore", "tortur*", "massacre*",
    "mass shooting*", "school shooting*",
    # Hate and politics
    "nazi*", "hitler", "genocid*", "racis*", "trump", "biden", "election*", "republican*", "democrat*",
    "israel*", "gaza", "palestin*", "ukrain*", "russia*", "putin", "abortion*", "militia*",
    # Drugs
    "cocaine", "heroin", "meth",
]

# Posts about Lemmy itself or its servers
META_WORDS = ["lemmy*", "fediverse", "mastodon", "kbin", "mbin", "piefed", "federat*", "reddit*", "upvot*", "downvot*", "mods"]


def build_word_pattern(words):
    parts = [re.escape(word[:-1]) + r'\w*' if word.endswith('*') else re.escape(word) for word in words]
    return r'\b(' + '|'.join(parts) + r')\b'


BLOCKED_PATTERN = re.compile(build_word_pattern(BLOCKED_WORDS), re.IGNORECASE)
# Also catches server names like "is mander.xyz down?"
META_PATTERN = re.compile(build_word_pattern(META_WORDS) + r'|\b[\w-]+\.(xyz|world|ml|social|com|org|net|io|zip|ca)\b', re.IGNORECASE)


def clean_question_text(title):
    """
    Clean question text by removing common prefixes.

    Args:
        title: Original question title

    Returns:
        str: Cleaned question text
    """
    # Remove "Question of the day:", "Question of the week:", "QOTD:", etc.
    patterns = [
        r'^Question of the day:?\s*',
        r'^Question of the week:?\s*',
        r'^QOTD:?\s*',
        r'^QOTW:?\s*',
        r'^Q:?\s*',
    ]

    cleaned = title
    for pattern in patterns:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE).strip()

    return cleaned


def parse_community(text):
    """
    Turn user input into a "name@instance" community.

    Accepts "asklemmy@lemmy.world", "!asklemmy@lemmy.world", "https://lemmy.world/c/asklemmy" or just "asklemmy".

    Returns:
        str: "name@instance" in lowercase, or None if the input can't be understood
    """
    text = text.strip().lower()

    if text.startswith(("http://", "https://")):
        parsed = urlparse(text)
        match = re.match(r'^/c/([\w.@-]+)', parsed.path)
        if not match or not parsed.netloc:
            return None
        # A URL like https://lemmy.world/c/asklemmy@lemmy.ml points to a community on another instance
        name = match.group(1)
        return name if "@" in name else f"{name}@{parsed.netloc}"

    text = text.removeprefix("!").removeprefix("c/")
    name, _, instance = text.partition("@")
    instance = instance or DEFAULT_INSTANCE

    if not re.fullmatch(r'\w+', name) or not re.fullmatch(r'[\w.-]+\.\w+', instance):
        return None
    return f"{name}@{instance}"


def split_community(community):
    """Split "name@instance" into (name, instance)."""
    name, _, instance = community.partition("@")
    return name, instance or DEFAULT_INSTANCE


async def fetch_json(session, url, params, attempts=3):
    """GET a Lemmy API URL, retrying on network errors. Raises the last error if every attempt fails.

    Returns:
        tuple: (int: HTTP status, dict: response data or None)
    """
    for attempt in range(1, attempts + 1):
        try:
            async with session.get(url, params=params) as response:
                if response.status != 200:
                    return response.status, None
                return response.status, await response.json()
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as e:
            if attempt == attempts:
                raise
            logger.warning(f"Request to {url} failed ({e!r}), retrying (attempt {attempt}/{attempts})")
            await asyncio.sleep(3)


def find_image_url(post):
    """Return the post's image if it links to one."""
    for url in (post.get('url'), post.get('thumbnail_url')):
        if url and any(url.lower().split('?')[0].endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
            return url
    return None


async def get_lemmy_question(community_list, used_questions):
    """
    Fetch a question from the given Lemmy communities that hasn't been used yet.
    The top posts of the past day from all communities are searched first, then the top posts of the past week.
    The highest scoring question that passes the filters is picked. Questions with more than MIN_WORDS words
    are preferred; if there are none, the longest shorter question is used instead.

    Args:
        community_list: List of communities as "name@instance"
        used_questions: Set or list of question texts that have already been used

    Returns:
        dict: Question data with 'title', 'description', 'author', 'community', 'url', 'created_utc' (and 'image_url'),
              or None if no unused questions found
    """
    if not community_list:
        logger.error("No communities provided")
        return None

    # Create set for faster lookups (compare cleaned versions)
    used_set = set(clean_question_text(q).lower().strip() for q in used_questions)

    # Unused questions with MIN_WORDS words or less, as (word_count, score, question data)
    short_candidates = []

    async with aiohttp.ClientSession(headers=HEADERS, timeout=TIMEOUT) as session:
        # Search the top posts of the day in every community first, then the week if none of them had a usable question
        for sort in ("TopDay", "TopWeek"):
            # Valid questions as (score, question data)
            candidates = []

            for community in community_list:
                name, instance = split_community(community)
                try:
                    logger.info(f"Fetching {sort} posts from {community}")

                    # Ask the community's own instance, which has the most accurate vote counts
                    url = f"https://{instance}/api/v3/post/list"
                    params = {"community_name": name, "sort": sort, "type_": "All", "limit": 50}

                    # Track filtering statistics
                    stats = {
                        'total': 0,
                        'removed': 0,
                        'nsfw': 0,
                        'already_used': 0,
                        'low_score': 0,
                        'not_question': 0,
                        'blocked': 0,
                        'meta': 0,
                        'too_short': 0
                    }

                    status, data = await fetch_json(session, url, params)
                    if status != 200:
                        logger.warning(f"Failed to fetch from {community}: HTTP {status}")
                        continue

                    posts = data.get('posts', [])
                    if not posts:
                        logger.warning(f"No posts found in {community}")
                        continue

                    logger.info(f"Found {len(posts)} posts in {community}")

                    for post_view in posts:
                        stats['total'] += 1
                        post = post_view['post']
                        score = post_view.get('counts', {}).get('score', 0)

                        # Skip deleted, removed and pinned posts (pinned posts are usually community rules)
                        if post.get('deleted') or post.get('removed') or post.get('featured_community') or post.get('featured_local'):
                            stats['removed'] += 1
                            continue

                        if post.get('nsfw') or post_view.get('community', {}).get('nsfw'):
                            stats['nsfw'] += 1
                            continue

                        title = (post.get('name') or "").strip()
                        cleaned_title = clean_question_text(title)

                        # Skip if already used (compare cleaned versions)
                        if not cleaned_title or cleaned_title.lower() in used_set:
                            stats['already_used'] += 1
                            continue

                        if score < MIN_SCORE:
                            stats['low_score'] += 1
                            continue

                        # Only actual questions
                        if '?' not in cleaned_title:
                            stats['not_question'] += 1
                            continue

                        body = (post.get('body') or "").strip()
                        if BLOCKED_PATTERN.search(cleaned_title) or BLOCKED_PATTERN.search(body):
                            logger.debug(f"Skipping blocked question: {cleaned_title[:50]}...")
                            stats['blocked'] += 1
                            continue

                        if META_PATTERN.search(cleaned_title):
                            logger.debug(f"Skipping question about Lemmy itself: {cleaned_title[:50]}...")
                            stats['meta'] += 1
                            continue

                        try:
                            created_utc = datetime.fromisoformat(post['published'].replace('Z', '+00:00')).timestamp()
                        except (KeyError, ValueError):
                            created_utc = None

                        result = {
                            'title': cleaned_title,
                            'description': body if len(body) > 10 else "",
                            'author': post_view.get('creator', {}).get('name', '[deleted]'),
                            'community': community,
                            'url': post.get('ap_id') or f"https://{instance}/post/{post['id']}",
                            'created_utc': created_utc
                        }

                        image_url = find_image_url(post)
                        if image_url:
                            result['image_url'] = image_url

                        word_count = len(cleaned_title.split())

                        # Short questions are kept as a fallback in case no longer question is found
                        if word_count <= MIN_WORDS:
                            stats['too_short'] += 1
                            short_candidates.append((word_count, score, result))
                            continue

                        candidates.append((score, result))

                    logger.info(f"{community} ({sort}) filtering stats - Total: {stats['total']}, Skipped: removed/pinned={stats['removed']}, nsfw={stats['nsfw']}, used={stats['already_used']}, low_score={stats['low_score']}, not_question={stats['not_question']}, blocked={stats['blocked']}, meta={stats['meta']}, short={stats['too_short']}")

                except Exception as e:
                    logger.error(f"Error fetching from {community}: {e!r}")
                    continue

            if candidates:
                score, result = max(candidates, key=lambda candidate: candidate[0])
                logger.info(f"Found unused question from {result['community']}: {result['title'][:50]}... ({score} points)")
                log_question_extras(result)
                return result

            logger.warning(f"No valid questions found in {sort} posts")

    # No question was long enough: lower the word limit step by step, i.e. take the longest short question
    if short_candidates:
        word_count, score, result = max(short_candidates, key=lambda candidate: (candidate[0], candidate[1]))
        logger.warning(f"No unused questions with more than {MIN_WORDS} words. Lowered the limit and using a {word_count}-word question: {result['title'][:50]}")
        log_question_extras(result)
        return result

    logger.error("Could not find any unused questions from any community")
    return None


def log_question_extras(result):
    if 'image_url' in result:
        logger.info(f"Post includes image: {result['image_url']}")
    if result['description']:
        logger.info(f"Post includes description: {result['description'][:100]}...")


async def test_community(community):
    """
    Test if a community exists and is accessible.

    Args:
        community: Community as "name@instance"

    Returns:
        tuple: (bool: success, str: message)
    """
    name, instance = split_community(community)

    try:
        async with aiohttp.ClientSession(headers=HEADERS, timeout=TIMEOUT) as session:
            status, data = await fetch_json(session, f"https://{instance}/api/v3/community", {"name": name})
        if status == 404:
            return (False, f"❌ {community} does not exist.")
        if status != 200:
            return (False, f"❌ Cannot access {community}: HTTP {status}")

        community_view = data.get('community_view', {})
        if community_view.get('community', {}).get('nsfw'):
            return (False, f"❌ {community} is an NSFW community.")

        counts = community_view.get('counts', {})
        return (True, f"✅ {community} is accessible! ({counts.get('users_active_day', '?')} active users today)")

    except aiohttp.ContentTypeError:
        return (False, f"❌ {instance} doesn't look like a Lemmy server.")
    except Exception as e:
        return (False, f"❌ Error testing {community}: {str(e)}")
