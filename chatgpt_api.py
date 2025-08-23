import os
import openai
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv('SECRET_KEY')
client = openai.OpenAI(api_key=OPENAI_API_KEY)

def get_chatgpt_response(prompt):
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content

# Example usage:
# print(get_chatgpt_response("hello, how are you"))
