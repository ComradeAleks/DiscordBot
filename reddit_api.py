import aiohttp
import logging
import time
import re
import random
import asyncio
import xml.etree.ElementTree as ET
from html import unescape
from datetime import datetime

logger = logging.getLogger(__name__)

# Questions should have more than this many words (lowered automatically if nothing else is available)
MIN_WORDS = 5


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


async def get_reddit_question(subreddit_list, used_questions):
    """
    Fetch a question from specified subreddits that hasn't been used yet.
    Hot posts from all subreddits are searched first, then new posts.
    Only returns questions from the past 24 hours. Questions with more than MIN_WORDS words
    are preferred; if there are none, the longest shorter question is used instead.

    Args:
        subreddit_list: List of subreddit names to fetch from
        used_questions: Set or list of question texts that have already been used
    
    Returns:
        dict: Question data with 'title', 'author', 'subreddit', 'url', 'created_utc' or None if no unused questions found
    """
    if not subreddit_list:
        logger.error("No subreddits provided")
        return None
    
    # Create set for faster lookups (compare cleaned versions)
    used_set = set(clean_question_text(q).lower().strip() for q in used_questions)
    
    # Calculate 24 hours ago timestamp
    twenty_four_hours_ago = time.time() - (24 * 60 * 60)
    
    # Shuffle subreddit list to randomize selection order
    shuffled_subreddits = subreddit_list.copy()
    random.shuffle(shuffled_subreddits)

    # Unused questions with MIN_WORDS words or less, as (word_count, question data)
    short_candidates = []
    
    # Create persistent session with browser-like headers
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
        'Accept': 'application/rss+xml, application/xml, text/xml, */*',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    }
    
    async with aiohttp.ClientSession(headers=headers) as session:
        # Search hot posts in every subreddit first, then new posts if none of them had a usable question
        for sort in ("hot", "new"):
            # Try each subreddit (in random order)
            for subreddit_name in shuffled_subreddits:
                try:
                    logger.info(f"Fetching {sort} posts from r/{subreddit_name}")
                
                    # Use Reddit RSS feed (less likely to be blocked)
                    feed_path = "" if sort == "hot" else "new/"
                    url = f"https://www.reddit.com/r/{subreddit_name}/{feed_path}.rss?limit=50"
                
                    # Track filtering statistics
                    stats = {
                        'total': 0,
                        'empty_title': 0,
                        'already_used': 0,
                        'too_old': 0,
                        'too_short': 0,
                        'nsfw_image': 0
                    }
                
                    # Fetch RSS feed from Reddit
                    async with session.get(url) as response:
                        if response.status != 200:
                            logger.warning(f"Failed to fetch from r/{subreddit_name}: HTTP {response.status}")
                            await asyncio.sleep(2)  # Rate limit delay
                            continue
                    
                        try:
                            rss_text = await response.text()
                            root = ET.fromstring(rss_text)
                        except Exception as parse_error:
                            logger.error(f"RSS parse error for r/{subreddit_name}: {parse_error}")
                            await asyncio.sleep(2)
                            continue
                    
                        # Find all entries in the feed
                        # RSS uses atom namespace
                        ns = {'atom': 'http://www.w3.org/2005/Atom'}
                        entries = root.findall('.//atom:entry', ns)
                    
                        if not entries:
                            logger.warning(f"No posts found in r/{subreddit_name}")
                            await asyncio.sleep(2)
                            continue
                    
                        logger.info(f"Found {len(entries)} posts in r/{subreddit_name}")
                    
                        # Process each entry
                        for entry in entries:
                            stats['total'] += 1
                        
                            # Extract post data from RSS entry
                            title_elem = entry.find('atom:title', ns)
                            title = unescape(title_elem.text) if title_elem is not None and title_elem.text else ""
                        
                            content_elem = entry.find('atom:content', ns)
                            content_html = content_elem.text if content_elem is not None and content_elem.text else ""
                        
                            # Extract selftext from HTML content (RSS includes HTML)
                            # The content has format like: <!-- SC_OFF --><div>selftext here</div><!-- SC_ON -->
                            selftext = ""
                            if content_html:
                                # Simple extraction - remove HTML tags
                                import re as regex
                                text_only = regex.sub(r'<[^>]+>', '', content_html)
                                text_only = regex.sub(r'<!-- .+? -->', '', text_only)
                                text_only = unescape(text_only.strip())
                            
                                # Filter out RSS feed metadata (not actual post content)
                                # Remove patterns like: "submitted by /u/username [link] [comments]"
                                text_only = regex.sub(r'submitted by /u/\S+\s*\[link\]\s*\[comments\]', '', text_only, flags=regex.IGNORECASE)
                                text_only = regex.sub(r'\[link\]\s*\[comments\]', '', text_only, flags=regex.IGNORECASE)
                                text_only = text_only.strip()
                            
                                # Only use if there's actual content left (more than just whitespace)
                                if text_only and len(text_only) > 10:  # At least 10 chars of real content
                                    selftext = text_only
                        
                            link_elem = entry.find('atom:link', ns)
                            post_url = link_elem.get('href') if link_elem is not None else ""
                        
                            author_elem = entry.find('atom:author/atom:name', ns)
                            author = author_elem.text if author_elem is not None and author_elem.text else '[deleted]'
                            # Remove /u/ prefix if present
                            if author.startswith('/u/'):
                                author = author[3:]
                        
                            updated_elem = entry.find('atom:updated', ns)
                            if updated_elem is not None and updated_elem.text:
                                # Parse ISO format date like "2026-06-01T10:30:00+00:00"
                                try:
                                    dt = datetime.fromisoformat(updated_elem.text.replace('Z', '+00:00'))
                                    created_utc = dt.timestamp()
                                except:
                                    created_utc = time.time()  # Fallback to now
                            else:
                                created_utc = time.time()
                        
                            # Check for images in the content
                            has_image = False
                            image_url = None
                            if content_html:
                                # Look for image URLs in the content
                                img_match = regex.search(r'<img[^>]+src="([^"]+)"', content_html)
                                if img_match:
                                    potential_url = unescape(img_match.group(1))
                                    if any(ext in potential_url.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
                                        has_image = True
                                        image_url = potential_url
                        
                            # Check for NSFW/spoiler tags in title or category
                            category_elem = entry.find('atom:category', ns)
                            category_term = category_elem.get('term', '').lower() if category_elem is not None else ""
                            is_nsfw = 'nsfw' in title.lower() or 'nsfw' in category_term
                            is_spoiler = 'spoiler' in title.lower() or 'spoiler' in category_term
                        
                            # Skip posts with spoilered or NSFW images
                            if has_image and (is_spoiler or is_nsfw):
                                logger.debug(f"Skipping post with spoilered/NSFW image: {title[:50]}...")
                                stats['nsfw_image'] += 1
                                continue
                        
                            # Skip if empty title
                            if not title:
                                stats['empty_title'] += 1
                                continue
                        
                            # Clean the question text
                            cleaned_title = clean_question_text(title)
                        
                            # Skip if already used (compare cleaned versions)
                            if cleaned_title.lower() in used_set:
                                stats['already_used'] += 1
                                continue
                        
                            # Skip if older than 24 hours
                            if created_utc < twenty_four_hours_ago:
                                logger.debug(f"Skipping old post: {cleaned_title[:50]}...")
                                stats['too_old'] += 1
                                continue
                        
                            word_count = len(cleaned_title.split())
                            result = {
                                'title': cleaned_title,
                                'description': selftext,
                                'author': author,
                                'subreddit': subreddit_name,
                                'url': post_url,
                                'created_utc': created_utc
                            }
                        
                            # Add image URL if present
                            if image_url and not is_spoiler and not is_nsfw:
                                result['image_url'] = image_url

                            # Short questions are kept as a fallback in case no longer question is found
                            if word_count <= MIN_WORDS:
                                logger.debug(f"Skipping short question ({word_count} words): {cleaned_title[:50]}...")
                                stats['too_short'] += 1
                                short_candidates.append((word_count, result))
                                continue

                            # Found a valid unused question!
                            logger.info(f"Found unused question from r/{subreddit_name}: {cleaned_title[:50]}... ({word_count} words)")
                            logger.info(f"r/{subreddit_name} filtering stats - Total: {stats['total']}, Skipped: empty={stats['empty_title']}, used={stats['already_used']}, old={stats['too_old']}, short={stats['too_short']}, nsfw_image={stats['nsfw_image']}")
                            log_question_extras(result)
                            return result
                    
                        logger.warning(f"r/{subreddit_name} ({sort}) - No valid questions found. Stats - Total: {stats['total']}, Skipped: empty={stats['empty_title']}, used={stats['already_used']}, old={stats['too_old']}, short={stats['too_short']}, nsfw_image={stats['nsfw_image']}")
            
                except Exception as e:
                    logger.error(f"Error fetching from r/{subreddit_name}: {e}")
                    await asyncio.sleep(2)
                    continue
            
                # Delay between subreddit requests
                await asyncio.sleep(1)
        
        # No question was long enough: lower the word limit step by step, i.e. take the longest short question
        if short_candidates:
            word_count, result = max(short_candidates, key=lambda candidate: candidate[0])
            logger.warning(f"No unused questions with more than {MIN_WORDS} words. Lowered the limit and using a {word_count}-word question: {result['title'][:50]}")
            log_question_extras(result)
            return result

        logger.error("Could not find any unused questions from any subreddit")
        return None


def log_question_extras(result):
    if 'image_url' in result:
        logger.info(f"Post includes image: {result['image_url']}")
    if result['description']:
        logger.info(f"Post includes description: {result['description'][:100]}...")


async def test_subreddit(subreddit_name):
    """
    Test if a subreddit exists and is accessible.
    
    Args:
        subreddit_name: Name of subreddit to test
    
    Returns:
        tuple: (bool: success, str: message)
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
        'Accept': 'application/rss+xml, application/xml, text/xml, */*',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    }
    
    try:
        url = f"https://www.reddit.com/r/{subreddit_name}/.rss?limit=1"
        
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url) as response:
                if response.status == 200:
                    try:
                        rss_text = await response.text()
                        root = ET.fromstring(rss_text)
                        ns = {'atom': 'http://www.w3.org/2005/Atom'}
                        entries = root.findall('.//atom:entry', ns)
                        if entries:
                            return (True, f"✅ r/{subreddit_name} is accessible!")
                        else:
                            return (True, f"⚠️ r/{subreddit_name} exists but appears to be empty.")
                    except:
                        return (False, f"❌ Cannot parse RSS feed from r/{subreddit_name}")
                elif response.status == 403:
                    return (False, f"❌ r/{subreddit_name} is private or forbidden.")
                elif response.status == 404:
                    return (False, f"❌ r/{subreddit_name} does not exist.")
                else:
                    return (False, f"❌ Cannot access r/{subreddit_name}: HTTP {response.status}")
                    
    except Exception as e:
        return (False, f"❌ Error testing r/{subreddit_name}: {str(e)}")
