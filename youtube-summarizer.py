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
from collections import deque

PROJECT_ID = os.getenv('VERTEX_IA_PROJECT') 
REGION = os.getenv('VERTEX_IA_REGION')
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN_YOUTUBES')
YOUTUBE_DATA_API_KEY = os.getenv('YOUTUBE_DATA_API_KEY')
INBOX_CHANNEL = 'youtube-summarizer'
url_pattern = re.compile(r'https?://(?:www\.)?youtube\.com/watch\?v=[\w-]+')


PROMPT_SUMMARY = """
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

PROMPT_QA = """
Using the context, conversation history, and video transcript provided to you, respond to the following question with the best of your knowledge, using only information provided to you:
"""

class VideoContext:
    def __init__(self):
        self.reset()

    def reset(self):
        self.url = None
        self.transcript = None
        self.title = None
        self.channel = None
        self.release_date = None
        self.conversation_history = deque(maxlen=10)
        self.summary = None

def format_timestamp(timestamp_str):
    try:
        dt_object = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%SZ")
        formatted_date = dt_object.strftime("%b, %dth %Y") 
        return formatted_date
    except ValueError:
        return None

def get_youtube_video_info(api_key, video_url):
    try:
        video_id = video_url.split("v=")[1].split("&")[0]
    except IndexError:
        return None

    try:
        youtube = build("youtube", "v3", developerKey=api_key)
        response = youtube.videos().list(part="snippet", id=video_id).execute()
        video_data = response['items'][0]['snippet']
        title = video_data['title']
        channel = video_data['channelTitle']
        release_date = video_data['publishedAt']
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
        [transcript, PROMPT_SUMMARY],
        generation_config=generation_config,
        stream=False,
    )
    return response.text

def generate_qa(transcript, conversation_history, question):
    vertexai.init(project=PROJECT_ID, location=REGION)
    model = GenerativeModel("gemini-1.5-pro")
    generation_config = {
        "max_output_tokens": 8192,
        "temperature": 0.7,
        "top_p": 0.95,
    }
    context = "\n".join([f"Q: {q}\nA: {a}" for q, a in conversation_history])
    full_context = f"""
    ---
    Full Transcript: 
    {transcript}
    ---
    Conversation History:
    {context}
    ---
    Current Question:
    {question}
    ---
    """
    prompt = f"{PROMPT_QA}\n\nContext: {full_context}"
    response = model.generate_content(
        [prompt],
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

# Global dictionary to store VideoContext objects for each channel
channel_contexts = {}

@client.event
async def on_ready():
    print(f'Youtube summarizer is ready!')

@client.event
async def on_message(message):
    global channel_contexts

    if message.author == client.user:
        return

    if message.channel.name == INBOX_CHANNEL:
        channel_id = message.channel.id
        if channel_id not in channel_contexts:
            channel_contexts[channel_id] = VideoContext()

        video_context = channel_contexts[channel_id]

        urls = url_pattern.findall(message.content)

        if urls:
            # YouTube URL request
            video_context.reset()  # Reset context only for new YouTube URL
            url = urls[0]
            video_id = extract_video_id(url)
            transcript = get_video_transcript(video_id)
            result = json.loads(get_youtube_video_info(YOUTUBE_DATA_API_KEY, url))

            # Update video context
            video_context.url = url
            video_context.transcript = transcript
            video_context.title = result.get("title", "N/A").strip()
            video_context.channel = result.get("channel", "N/A")
            video_context.release_date = format_timestamp(result.get("release_date"))

            if transcript:
                summary = generate_summary(transcript)
                video_context.summary = summary
                videodata = f"""**Title:** {video_context.title}\n**Channel:** {video_context.channel}\n**Released:** {video_context.release_date}"""
                await send_long_message(message.channel, f'{videodata}\n{summary}')
            else:
                await message.channel.send(f'Failed to get transcript for video!')

        elif message.content.startswith('/ask'):
            # Q&A request
            question = message.content[len('/ask '):].strip()

            if video_context.url and video_context.transcript:
                qa_response = generate_qa(video_context.transcript, video_context.conversation_history, question)
                await send_long_message(message.channel, qa_response)
                video_context.conversation_history.append((question, qa_response))
            else:
                await message.channel.send(f'No recent video information found. Please submit a YouTube URL first.')

        else:
            await message.channel.send(f'OK!')
            pass

client.run(DISCORD_TOKEN)