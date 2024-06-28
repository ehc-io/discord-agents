import discord
import re
import os
from playwright.async_api import async_playwright

import base64
import vertexai
from vertexai.generative_models import GenerativeModel, Part, FinishReason
import vertexai.preview.generative_models as generative_models

import requests
import os
from urllib.parse import urlparse

PROJECT_ID = os.getenv('VERTEX_IA_PROJECT') 
REGION = os.getenv('VERTEX_IA_REGION')
DEPENDENCIES_FOLDER ='/mnt/common' 
INBOX_CHANNEL = 'podcaster-transcriber'
url_pattern = re.compile(r'https?://\S+')
DOWNLOAD_FOLDER = 'downloads'

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN_PODCASTS')

PROMPT = """
# ANALYSIS INSTRUCTIONS:
- Purpose: Your job is to provide a summary of the podcast episode presented to you, following the guidelines bellow.
# OUTPUT format (mandatory):
- ## Participants: (provide the name of the participants of the episode, provide Twitter handles, websites, and Linkedin profiles if available)
- ## Summary: (Short summary - around 150 words - of the podcast episode)
- ## Quotes: (Up to 5 most important quotes extracted from the podcast episode, including the owner of the quote)
- ## Q&A: (Point out the most interesting or provocative questions/answers pairs (up to 5) that the podcast episode presents)
- Do not include any other topics, neither title, notices or notes, only the sections requested eariler.
- Please disregard from the content any adverstisements or sponsored products or services.
"""

def encode_mp3_to_base64(file_path):
    try:
        with open(file_path, "rb") as mp3_file:
            mp3_content = mp3_file.read()
            base64_encoded = base64.b64encode(mp3_content).decode('utf-8')
        return base64_encoded
    except FileNotFoundError:
        print(f"File not found: {file_path}")
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None

def slugify(filename):
  filename = re.sub(r'[^a-zA-Z0-9_\s]', '', filename)  # Remove special characters
  filename = filename.replace(' ', '_')  # Replace spaces with underscores
  filename = filename.replace('-', '_')  # Replace dashes with underscores
  if not filename.endswith('.mp3'):
    filename += '.mp3'  # Enforce .mp3 extension
  return filename

async def send_long_message(channel, message):
    """Sends a long message to a Discord channel, splitting it if necessary."""
    if len(message) <= 2000:
        await channel.send(message)
        return

    chunks = [message[i:i + 2000] for i in range(0, len(message), 2000)]
    for chunk in chunks:
        await channel.send(chunk)

async def extract_podcast_info(url):
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(url)

        try:
            podcast_title = await page.evaluate("document.querySelector('meta[property=\"og:title\"]').content")
            podcast_title = podcast_title.split(' - ')[1].strip()

            episode_title = await page.evaluate("document.querySelector('meta[property=\"og:title\"]').content")
            episode_title = episode_title.split(' - ')[0].strip()

            download_url = await page.evaluate("document.querySelector('a.download-button').href")
            release_date = await page.evaluate("document.querySelector('#episode_date').textContent")
            release_date = release_date.strip()

            result = {
                'podcast_title': podcast_title,
                'episode_title': episode_title,
                'download_url': download_url,
                'release_date': release_date,
            }
            return result
        except Exception as e:
            print(f"Error extracting podcast information: {e}")
            return None
        finally:
            await browser.close()

def download_podcast(url, download_folder):
    try:
        response = requests.get(url, allow_redirects=True, stream=True)
        response.raise_for_status()

        # Get the final URL after potential redirects
        final_url = response.url

        # Extract filename from URL or Content-Disposition header
        content_disposition = response.headers.get('Content-Disposition')
        if content_disposition:
            filename = re.findall("filename=(.+)", content_disposition)[0].strip('"')
        else:
            filename = os.path.basename(urlparse(final_url).path)

        file_path = os.path.join(download_folder, filename)

        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        print(f"Successfully downloaded: {filename}")
        return file_path

    except requests.RequestException as e:
        print(f"Error downloading podcast: {e}")
        return None
    
def generate_summary(audio_file):
    vertexai.init(project=PROJECT_ID, location=REGION)
    model = GenerativeModel(
    "gemini-1.5-flash-001",
    )
    audio_file_encoded = encode_mp3_to_base64(audio_file)
    audio_data = Part.from_data(mime_type="audio/mpeg", data=base64.b64decode(audio_file_encoded))
    generation_config = {
    "max_output_tokens": 8192,
    "temperature": 1,
    "top_p": 0.95,
    }
    
    safety_settings = {
        generative_models.HarmCategory.HARM_CATEGORY_HATE_SPEECH: generative_models.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
        generative_models.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: generative_models.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
        generative_models.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: generative_models.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
        generative_models.HarmCategory.HARM_CATEGORY_HARASSMENT: generative_models.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    }
    
    response = model.generate_content(
        [ audio_data, PROMPT],
        generation_config=generation_config,
        safety_settings=safety_settings,
        stream=False,
    )
    output = response.candidates[0].content.parts[0].text
    
    return output
 
####################################################
#
#
# Define the bot's intents
intents = discord.Intents.default()
intents.message_content = True

# Create the bot instance
client = discord.Client(intents=intents)

# Ensure the download folder exists
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

@client.event
async def on_ready():
    print(f'Podcast Summarizer has started.')

@client.event
async def on_message(message):
    # Ignore messages from the bot itself
    if message.author == client.user:
        return

    # Check if the message is in the specified channel
    if message.channel.name == INBOX_CHANNEL and message.author.name == "0xedk":
        # Find all URLs in the message
        urls = url_pattern.findall(message.content)
        if urls:
            for url in urls:
                result = await extract_podcast_info(url)
                if result:
                    download = download_podcast(result["download_url"], DOWNLOAD_FOLDER)
                    if download:
                        await message.channel.send(f'\n\nPodcast Title: {result["podcast_title"]}\nEpisode: {result["episode_title"]}\nRelease Date: {result["release_date"]}')
                        summary = generate_summary(download)
                        if len(summary) > 2000:
                            # summary = summary[:2000]
                            await send_long_message(message.channel, f'\n{summary}')
                            # await message.channel.send(f'\n{summary}')
                        try:
                            os.remove(download)
                        except Exception as e:
                            print(f"Error removing file: {download} - {e}")
                else:
                    await message.channel.send(f'Failed to download content')

client.run(DISCORD_TOKEN)

