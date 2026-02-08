import asyncio
import os
import logging
from pathlib import Path
from telegram import Bot
from telegram.error import TelegramError, RetryAfter, TimedOut
from telegram.request import HTTPXRequest

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TelegramBotAgent:
    """
    Agent responsible for sending Telegram messages
    """

    def __init__(self, token=None):
        """
        Initialize Telegram bot

        Args:
            token (str, optional): Telegram bot token
        """
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        if not self.token:
            raise ValueError("Telegram bot token is required. Provide via environment variable or parameter.")

        # Configure request with longer timeouts
        request = HTTPXRequest(
            connection_pool_size=8,
            connect_timeout=30.0,
            read_timeout=30.0,
            write_timeout=30.0,
            pool_timeout=30.0
        )

        self.bot = Bot(token=self.token, request=request)

    async def send_message(self, chat_id, message, parse_mode="Markdown", retry_count=0, max_retries=3):
        """
        Send message to Telegram channel

        Args:
            chat_id (str): Telegram channel ID
            message (str): Message to send
            parse_mode (str): Parse mode ("Markdown", "MarkdownV2", "HTML", None)
            retry_count (int): Current retry count
            max_retries (int): Maximum retry attempts

        Returns:
            bool: Transmission success status
        """
        try:
            # Attempt to send with specified parse_mode
            result = await self.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=parse_mode
            )
            logger.info(f"Message sent successfully ({parse_mode}): {chat_id}")
            # Firebase Bridge - save metadata + push notification
            try:
                from firebase_bridge import notify
                await notify(
                    message=message,
                    telegram_message_id=result.message_id,
                    channel_id=chat_id,
                )
            except Exception as e:
                logger.debug(f"Firebase bridge: {e}")
            return True
        except RetryAfter as e:
            # Rate limit hit, wait and retry
            wait_time = e.retry_after + 1
            logger.warning(f"Rate limit hit. Waiting {wait_time} seconds before retry...")
            await asyncio.sleep(wait_time)
            if retry_count < max_retries:
                return await self.send_message(chat_id, message, parse_mode, retry_count + 1, max_retries)
            else:
                logger.error(f"Max retries reached after rate limit")
                return False
        except TimedOut as e:
            # Timeout occurred, retry with exponential backoff
            if retry_count < max_retries:
                wait_time = 2 ** retry_count  # 1, 2, 4 seconds
                logger.warning(f"Timeout occurred. Retrying in {wait_time} seconds... (attempt {retry_count + 1}/{max_retries})")
                await asyncio.sleep(wait_time)
                return await self.send_message(chat_id, message, parse_mode, retry_count + 1, max_retries)
            else:
                logger.error(f"Max retries reached after timeout")
                return False
        except TelegramError as e:
            logger.error(f"Telegram message send failed ({parse_mode}): {e}")
            # If error occurs, retry with plain text (for parse errors)
            if parse_mode and "parse" in str(e).lower():
                try:
                    logger.info("Retrying with plain text.")
                    result = await self.bot.send_message(
                        chat_id=chat_id,
                        text=message
                    )
                    logger.info(f"Message sent successfully (plain text): {chat_id}")
                    try:
                        from firebase_bridge import notify
                        await notify(
                            message=message,
                            telegram_message_id=result.message_id,
                            channel_id=chat_id,
                        )
                    except Exception as e:
                        logger.debug(f"Firebase bridge: {e}")
                    return True
                except TelegramError as e2:
                    logger.error(f"Plain text message send also failed: {e2}")
                    return False
            return False

    async def send_document(self, chat_id, document_path, caption=None, retry_count=0, max_retries=3):
        """
        Send file to Telegram channel

        Args:
            chat_id (str): Telegram channel ID
            document_path (str): File path to send
            caption (str, optional): File description
            retry_count (int): Current retry count
            max_retries (int): Maximum retry attempts

        Returns:
            bool: Transmission success status
        """
        try:
            with open(document_path, 'rb') as document:
                result = await self.bot.send_document(
                    chat_id=chat_id,
                    document=document,
                    caption=caption,
                    parse_mode="Markdown"  # Markdown format support
                )
            logger.info(f"File sent successfully: {document_path}")
            try:
                from firebase_bridge import notify
                telegram_link = f"https://t.me/{os.environ.get('TELEGRAM_CHANNEL_USERNAME', 'stock_ai_agent')}/{result.message_id}"
                await notify(
                    message=caption or str(document_path),
                    telegram_message_id=result.message_id,
                    channel_id=chat_id,
                    has_pdf=True,
                    pdf_telegram_link=telegram_link,
                )
            except Exception as e:
                logger.debug(f"Firebase bridge: {e}")
            return True
        except RetryAfter as e:
            # Rate limit hit, wait and retry
            wait_time = e.retry_after + 1
            logger.warning(f"Rate limit hit. Waiting {wait_time} seconds before retry...")
            await asyncio.sleep(wait_time)
            if retry_count < max_retries:
                return await self.send_document(chat_id, document_path, caption, retry_count + 1, max_retries)
            else:
                logger.error(f"Max retries reached after rate limit")
                return False
        except TimedOut as e:
            # Timeout occurred, retry with exponential backoff
            if retry_count < max_retries:
                wait_time = 2 ** retry_count  # 1, 2, 4 seconds
                logger.warning(f"Timeout occurred. Retrying in {wait_time} seconds... (attempt {retry_count + 1}/{max_retries})")
                await asyncio.sleep(wait_time)
                return await self.send_document(chat_id, document_path, caption, retry_count + 1, max_retries)
            else:
                logger.error(f"Max retries reached after timeout")
                return False
        except TelegramError as e:
            logger.error(f"Telegram file send failed: {e}")
            return False

    async def process_messages_directory(self, directory, chat_id, sent_dir=None):
        """
        Process and send all Telegram message files in directory

        Args:
            directory (str): Directory containing Telegram message files
            chat_id (str): Telegram channel ID
            sent_dir (str, optional): Directory to move sent files to

        Returns:
            int: Number of successfully sent messages
        """
        success_count = 0
        dir_path = Path(directory)

        if not dir_path.exists() or not dir_path.is_dir():
            logger.error(f"Message directory does not exist: {directory}")
            return success_count

        # Find Telegram message files (.txt files only)
        message_files = list(dir_path.glob("*_telegram.txt"))

        if not message_files:
            logger.warning(f"No message files to send: {directory}")
            return success_count

        logger.info(f"Processing {len(message_files)} message files.")

        # Create sent_dir directory (if specified)
        if sent_dir:
            sent_path = Path(sent_dir)
            sent_path.mkdir(exist_ok=True)

        # Process each message file
        for msg_file in message_files:
            try:
                # Read file
                with open(msg_file, 'r', encoding='utf-8') as file:
                    message = file.read()

                # Send message
                logger.info(f"Sending message: {msg_file.name}")
                success = await self.send_message(chat_id, message)

                if success:
                    success_count += 1

                    # Move or mark as sent after transmission
                    if sent_dir:
                        # Move sent files to sent folder
                        msg_file.rename(Path(sent_dir) / msg_file.name)
                        logger.info(f"Sent and moved: {msg_file.name}")
                    else:
                        # If sent_dir not specified, mark with file name change
                        new_name = msg_file.with_name(f"{msg_file.stem}_sent{msg_file.suffix}")
                        msg_file.rename(new_name)
                        logger.info(f"Sent and renamed: {new_name.name}")

                # Delay to prevent Telegram API rate limits
                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Error processing {msg_file.name}: {e}")

        logger.info(f"Total {success_count} messages sent successfully.")
        return success_count

async def main():
    """
    Main function
    """
    import argparse

    parser = argparse.ArgumentParser(description="Send Telegram message files to Telegram channel.")
    parser.add_argument("--dir", default="telegram_messages", help="Directory containing Telegram message files")
    parser.add_argument("--token", help="Telegram bot token (can also be set via environment variable)")
    parser.add_argument("--chat-id", help="Telegram channel ID (can also be set via environment variable)")
    parser.add_argument("--sent-dir", help="Directory to move sent files to")
    parser.add_argument("--file", help="Send specific message file only")

    args = parser.parse_args()

    # Check channel ID
    chat_id = args.chat_id or os.environ.get("TELEGRAM_CHANNEL_ID")
    if not chat_id:
        logger.error("Telegram channel ID is required. Provide via environment variable or --chat-id parameter.")
        return

    # Initialize Telegram bot agent
    bot_agent = TelegramBotAgent(token=args.token)

    # Process specific file only
    if args.file:
        file_path = args.file
        if not os.path.exists(file_path):
            logger.error(f"Specified message file does not exist: {file_path}")
            return

        try:
            # Read file
            with open(file_path, 'r', encoding='utf-8') as file:
                message = file.read()

            # Send message
            logger.info(f"Sending message: {os.path.basename(file_path)}")
            success = await bot_agent.send_message(chat_id, message)

            if success:
                logger.info(f"Message sent successfully: {os.path.basename(file_path)}")
        except Exception as e:
            logger.error(f"Error sending message: {e}")
    else:
        # Process all messages in directory
        await bot_agent.process_messages_directory(args.dir, chat_id, args.sent_dir)

if __name__ == "__main__":
    asyncio.run(main())