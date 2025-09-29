import os
import openai
import asyncio
import logging
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv('SECRET_KEY')
client = openai.OpenAI(api_key=OPENAI_API_KEY)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def get_chatgpt_response_with_retry(prompt, max_retries=None):
    """
    Get ChatGPT response with automatic retry every 10 seconds on failure.
    Will retry forever if max_retries is None.
    """
    retry_count = 0
    
    while True:
        try:
            logger.info(f"Attempting ChatGPT API call (attempt {retry_count + 1})")
            response = client.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
                timeout=30  # 30 second timeout
            )
            logger.info("ChatGPT API call successful")
            return response.choices[0].message.content
            
        except Exception as e:
            retry_count += 1
            logger.error(f"ChatGPT API error (attempt {retry_count}): {e}")
            
            if max_retries is not None and retry_count >= max_retries:
                logger.error(f"Max retries ({max_retries}) reached for ChatGPT API")
                raise e
            
            logger.info("Retrying ChatGPT API call in 10 seconds...")
            await asyncio.sleep(10)

def get_chatgpt_response(prompt):
    """
    Synchronous wrapper for backward compatibility.
    For new code, prefer using get_chatgpt_response_with_retry directly.
    """
    return asyncio.run(get_chatgpt_response_with_retry(prompt))

# Example usage:
# print(get_chatgpt_response("hello, how are you"))
