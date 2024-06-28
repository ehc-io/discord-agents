import discord
import re
import os
from youtube_transcript_api import YouTubeTranscriptApi
from urllib.parse import urlparse, parse_qs

from datetime import datetime

import json
from googleapiclient.discovery import build

import vertexai
from vertexai.generative_models import GenerativeModel, Part

PROJECT_ID = os.getenv('VERTEX_IA_PROJECT') 
REGION = os.getenv('VERTEX_IA_REGION')
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN_YOUTUBES')
YOUTUBE_DATA_API_KEY = os.getenv('YOUTUBE_DATA_API_KEY')
INBOX_CHANNEL = 'youtube-summarizer'
url_pattern = re.compile(r'https?://(?:www\.)?youtube\.com/watch\?v=[\w-]+')

PROMPT = """
# ANALYSIS INSTRUCTIONS:
Purpose: Your job is to provide a summary of the YouTube video presented to you, following the guidelines below.

# OUTPUT format (mandatory):
- ## Participants: (provide the name of the participants of the video, provide Twitter handles, websites, and Linkedin profiles if available)
- ## Summary: (Short summary - around 150 words - of the youtube video)
- ## Quotes: (Up to 5 most important quotes extracted from the youtube video, including the owner of the quote)
- ## Q&A: (Point out the most interesting or provocative questions/answers pairs (up to 5) that the youtube video presents)
- Do not include any other topics, neither title, notices or notes, only the sections requested eariler.
- Please disregard from the content any adverstisements or sponsored products or services.
"""

def format_timestamp(timestamp_str):
  try:
    dt_object = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%SZ")
    formatted_date = dt_object.strftime("%b, %dth %Y") 
    return formatted_date
  except ValueError:
    return None

def get_youtube_video_info(api_key, video_url):
  try:
    # Extract video ID from URL
    video_id = video_url.split("v=")[1].split("&")[0]
  except IndexError:
    return None

  try:
    # Build YouTube API service
    youtube = build("youtube", "v3", developerKey=api_key)

    # Make API request
    response = youtube.videos().list(part="snippet", id=video_id).execute()

    # Extract information
    video_data = response['items'][0]['snippet']
    title = video_data['title']
    channel = video_data['channelTitle']
    release_date = video_data['publishedAt']

    # Construct JSON object
    output = {
        "title": title,
        "channel": channel,
        "release_date": release_date
    }

    return json.dumps(output)
  except Exception as e:
    print(f"Error: {e}")
    return None

def extract_video_id(url):
    query = urlparse(url).query
    return parse_qs(query)['v'][0]

def get_video_transcript(video_id):
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id)
        return ' '.join([entry['text'] for entry in transcript])
    except Exception as e:
        print(f"Error getting transcript: {e}")
        return None

def generate_summary(transcript):
    vertexai.init(project=PROJECT_ID, location=REGION)
    model = GenerativeModel("gemini-1.5-pro")

    generation_config = {
        "max_output_tokens": 8192,
        "temperature": 0.7,
        "top_p": 0.95,
    }

    response = model.generate_content(
        [transcript, PROMPT],
        generation_config=generation_config,
        stream=False,
    )

    return response.text

async def send_long_message(channel, message):
    if len(message) <= 2000:
        await channel.send(message)
        return

    chunks = [message[i:i + 2000] for i in range(0, len(message), 2000)]
    for chunk in chunks:
        await channel.send(chunk)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f'Youtube summarizer is ready!')

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.channel.name == INBOX_CHANNEL:
        urls = url_pattern.findall(message.content)
        if urls:
            for url in urls:
                video_id = extract_video_id(url)
                transcript = get_video_transcript(video_id)
                result = json.loads(get_youtube_video_info(YOUTUBE_DATA_API_KEY, url))
                title = result.get("title", "N/A").strip()
                channel = result.get("channel", "N/A")
                release_date = format_timestamp(result.get("release_date"))
                if transcript:
                    summary = generate_summary(transcript)
                    videodata = f"""**Title:** {title}\n**Channel:** {channel}\n**Released:** {release_date}"""
                    await send_long_message(message.channel, f'{videodata}\n{summary}')
                else:
                    await message.channel.send(f'Failed to get transcript for video!')

client.run(DISCORD_TOKEN)