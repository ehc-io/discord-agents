import argparse
import sys
import requests
import base64
import email
from google.oauth2 import service_account
from googleapiclient.discovery import build
from email.header import decode_header
import vertexai
from vertexai.generative_models import GenerativeModel

import vertexai.preview.generative_models as generative_models
import logging
import os

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/gmail.modify']
CLIENT_SECRET_FILE = os.getenv("CLIENT_SECRET_FILE")
VERTEX_IA_CREDENTIALS_PATH = os.getenv("VERTEX_IA_CREDENTIALS_PATH")
NORD_NEWS_EMAIL = os.getenv("NORD_NEWS_EMAIL")

PROMPT = """
# INSTRUÇÕES PARA ANALISE:
Objetivo: Forneça uma análise concisa e informativa das principais pontos apresentados no conteúdo fornecido a seguir.
Formato:
A análise deverá conter os seguintes segmentos:
Resumo: Com base no conteudo apresentado, forneça um breve resumo em bullet points destacando as principais idéias do texto relacionados ao mercado financeiro.
Análise: Apresente resumo com no maximo 30 palavras, incluindo possíveis impactos e perspectivas futuras.

# INSTRUÇÕES DE SAÍDA
- Não inclua titulo na análise
- Produza listas numeradas, não marcadores.
- Não envie avisos ou notas – apenas as seções solicitadas.
- Não repita itens nas seções de saída.
- Não inicie os itens com as mesmas palavras iniciais.
- Se o conteudo for meramente uma propadanda/anuncio/campanha de marketing de produtos/serviços da NORD, não produza uma análise, apenas um resumo não mais do que 10 palavras, caracterizando como tal.
"""

class DiscordWebhook:
    """
    A class for sending messages to a Discord webhook using environment variables.
    """

    def __init__(self):
        """
        Initializes the DiscordWebhook object, fetching the webhook URL from the environment variable.
        """
        # self.webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
        self.webhook_url = "https://discord.com/api/webhooks/1256953966512701503/mPnvPxSfPeD28zYbiNp2PWNyFtCymOdhj9oyiWf83sh0YufKJDfBApFvVsmt0iQ2P06m"
        if not self.webhook_url:
            raise ValueError("DISCORD_WEBHOOK_URL environment variable not set.")

    def send_message(self, message):
        """
        Sends a message to the Discord webhook.

        Args:
            message (str): The message to send.
        """

        try:
            response = requests.post(self.webhook_url, json={"content": message})

            if response.status_code == 204:
                print("Message sent successfully!")
            else:
                raise ValueError(f"Error sending message: {response.status_code}")
        except ValueError as e:
            print(f"Error: {e}")
        except requests.exceptions.RequestException as e:
            print(f"Error sending request: {e}")

async def send_long_message(channel, message):
    """Sends a long message to a Discord channel, splitting it if necessary."""
    if len(message) <= 2000:
        await channel.send(message)
        return

    chunks = [message[i:i + 2000] for i in range(0, len(message), 2000)]
    for chunk in chunks:
        await channel.send(chunk)
             
def extract_payload(email_message):
    msg = email.message_from_string(email_message)
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == 'text/plain' or content_type == 'text/html':
                return part.get_payload(decode=True).decode('utf-8')
    else:
        return msg.get_payload(decode=True).decode('utf-8')
    return None

def decode_mime_words(s):
    return ''.join(
        word.decode(encoding or 'utf-8') if isinstance(word, bytes) else word
        for word, encoding in decode_header(s)
    )

def get_gmail_service(user_email):
    try:
        logger.debug(f"Loading credentials from: {CLIENT_SECRET_FILE}")
        credentials = service_account.Credentials.from_service_account_file(CLIENT_SECRET_FILE, scopes=SCOPES)

        logger.debug(f"Credentials loaded successfully. Scopes: {credentials.scopes}")

        logger.debug(f"Delegating credentials to: {user_email}")
        delegated_credentials = credentials.with_subject(user_email)
        logger.debug("Credentials delegated successfully")

        logger.debug("Building Gmail service")
        service = build('gmail', 'v1', credentials=delegated_credentials)
        logger.debug("Gmail service built successfully")

        return service
    except Exception as e:
        logger.error(f"Error in get_gmail_service: {str(e)}", exc_info=True)
        raise
    
def get_latest_unread_message(service, user_id, label="finance/nord"):
    query = f'label:{label} is:unread'
    messages = service.users().messages().list(userId=user_id, q=query).execute().get('messages', [])
    if messages:
        return messages[0]['id']  # Return the ID of the first unread message
    return None

def get_message_content(service, user_id, msg_id):
    message = service.users().messages().get(userId=user_id, id=msg_id, format='raw').execute()
    msg_str = base64.urlsafe_b64decode(message['raw'].encode('ASCII'))
    return msg_str.decode("utf-8")

def mark_message_as_read(service, user_id, msg_id):
    service.users().messages().modify(userId=user_id, id=msg_id, body={'removeLabelIds': ['UNREAD']}).execute()

def fetch_structured_emails(user_email, label):
    service = get_gmail_service(user_email)
    message_id = get_latest_unread_message(service, user_email, label)
    if message_id:
        msg_content = get_message_content(service, user_email, message_id)
        msg = email.message_from_string(msg_content)
        headers = msg.items()
        subject = msg.get('Subject', '')
        structured_message = {
            "title": decode_mime_words(subject),
            "content": extract_payload(msg_content)
        }
        mark_message_as_read(service, user_email, message_id)  # Mark as read after processing
        return structured_message
    return None

def generate_text(text_blob, prompt):
    vertexai.init(project="ai-1684952810", location="us-central1")
    model = GenerativeModel("gemini-1.5-flash-001")
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
    responses = model.generate_content(
        [f"{prompt} {text_blob}"],
        generation_config=generation_config,
        safety_settings=safety_settings,
        stream=True,
    )
    generated_text = ""
    for response in responses:
        generated_text += response.text
    return generated_text

def main():
    parser = argparse.ArgumentParser(description='Fetch and analyze Gmail messages.')
    parser.add_argument('--user_email', '-e', type=str, required=True, help='User email address')
    parser.add_argument('--label', '-l', type=str, required=True, help='Email label to filter')
    args = parser.parse_args()

    try:
        message = fetch_structured_emails(
            user_email=args.user_email,
            label=args.label
        )
        if message:
            title = message["title"]
            content = message["content"]
            content = generate_text(content, PROMPT)
            if title != "ALERTA - Operação Anti-Trader":
                message = f"Titulo: {title}\n{content}"
                # print(message)
                print(f"this message has: {len(message)} characters")
                print(f"this message has: {len(message.split())} words")
                webhook = DiscordWebhook()
                webhook.send_message(message)

    except Exception as e:
        logger.error(f"An error occurred: {str(e)}")
        sys.exit(1)
        
if __name__ == '__main__':
    main()