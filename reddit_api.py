import aiohttp
import logging
import asyncio
import time
import re
import random

logger = logging.getLogger(__name__)


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
    Fetch a hot question from specified subreddits that hasn't been used yet.
    Only returns questions from the past 24 hours with more than 5 words.
    
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
    
    # Try each subreddit (in random order)
    for subreddit in shuffled_subreddits:
        try:
            logger.info(f"Fetching hot posts from r/{subreddit}")
            
            # Fetch hot posts from subreddit (Reddit JSON API)
            url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit=50"
            headers = {
                'User-Agent': 'DiscordBot:QOTD:v1.0 (by /u/YourUsername)'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        logger.warning(f"Failed to fetch from r/{subreddit}: HTTP {response.status}")
                        continue
                    
                    # Check content type before parsing
                    content_type = response.headers.get('Content-Type', '')
                    if 'json' not in content_type.lower():
                        response_text = await response.text()
                        logger.warning(f"Non-JSON response from r/{subreddit}: {response_text[:200]}")
                        continue
                    
                    try:
                        data = await response.json()
                    except Exception as json_error:
                        response_text = await response.text()
                        logger.error(f"JSON parse error for r/{subreddit}: {json_error}. Response: {response_text[:200]}")
                        continue
                    
                    posts = data.get('data', {}).get('children', [])
                    
                    if not posts:
                        logger.warning(f"No posts found in r/{subreddit}")
                        continue
                    
                    logger.info(f"Found {len(posts)} posts in r/{subreddit}")
                    
                    # Look through hot posts for an unused question
                    for post in posts:
                        post_data = post.get('data', {})
                        title = post_data.get('title', '').strip()
                        created_utc = post_data.get('created_utc', 0)
                        author = post_data.get('author', '[deleted]')
                        permalink = post_data.get('permalink', '')
                        post_url = f"https://reddit.com{permalink}"
                        
                        # Check for image data
                        post_hint = post_data.get('post_hint', '')
                        url = post_data.get('url', '')
                        is_spoiler = post_data.get('spoiler', False)
                        is_nsfw = post_data.get('over_18', False)
                        
                        # Skip posts with spoilered or NSFW images
                        if post_hint == 'image' and (is_spoiler or is_nsfw):
                            logger.debug(f"Skipping post with spoilered/NSFW image: {title[:50]}...")
                            continue
                        
                        # Get image URL if it's a clean image
                        image_url = None
                        if post_hint == 'image' and not is_spoiler and not is_nsfw:
                            image_url = url
                        
                        # Skip if empty
                        if not title:
                            continue
                        
                        # Clean the question text
                        cleaned_title = clean_question_text(title)
                        
                        # Skip if already used (compare cleaned versions)
                        if cleaned_title.lower() in used_set:
                            continue
                        
                        # Skip if older than 24 hours
                        if created_utc < twenty_four_hours_ago:
                            logger.debug(f"Skipping old post: {cleaned_title[:50]}...")
                            continue
                        
                        # Skip if 5 words or less
                        word_count = len(cleaned_title.split())
                        if word_count <= 5:
                            logger.debug(f"Skipping short question ({word_count} words): {cleaned_title[:50]}...")
                            continue
                        
                        # Found a valid unused question!
                        logger.info(f"Found unused question from r/{subreddit}: {cleaned_title[:50]}... ({word_count} words)")
                        result = {
                            'title': cleaned_title,
                            'author': author,
                            'subreddit': subreddit,
                            'url': post_url,
                            'created_utc': created_utc
                        }
                        
                        # Add image URL if present
                        if image_url:
                            result['image_url'] = image_url
                            logger.info(f"Post includes image: {image_url}")
                        
                        return result
                    
                    logger.info(f"No valid unused questions from r/{subreddit} (within 24h, >5 words)")
        
        except Exception as e:
            logger.error(f"Error fetching from r/{subreddit}: {e}")
            continue
        
        # Small delay between subreddit requests to avoid rate limiting
        await asyncio.sleep(1)
    
    logger.error("Could not find any unused questions from any subreddit")
    return None


async def test_subreddit(subreddit_name):
    """
    Test if a subreddit exists and is accessible.
    
    Args:
        subreddit_name: Name of subreddit to test
    
    Returns:
        tuple: (bool: success, str: message)
    """
    try:
        url = f"https://www.reddit.com/r/{subreddit_name}/hot.json?limit=1"
        headers = {
            'User-Agent': 'DiscordBot:QOTD:v1.0 (by /u/YourUsername)'
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 404:
                    return False, f"Subreddit r/{subreddit_name} does not exist"
                elif response.status == 403:
                    return False, f"Subreddit r/{subreddit_name} is private or banned"
                elif response.status != 200:
                    return False, f"Failed to access r/{subreddit_name}: HTTP {response.status}"
                
                data = await response.json()
                posts = data.get('data', {}).get('children', [])
                
                if not posts:
                    return False, f"Subreddit r/{subreddit_name} exists but has no posts"
                
                return True, f"Subreddit r/{subreddit_name} is accessible"
    
    except Exception as e:
        logger.error(f"Error testing subreddit r/{subreddit_name}: {e}")
        return False, f"Error accessing r/{subreddit_name}: {str(e)}"
