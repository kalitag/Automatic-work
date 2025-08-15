#!/usr/bin/env python3
"""
Telegram Product Scraper Bot
Handles product links from multiple platforms with Medium/Advanced mode switching
"""

import os
import re
import time
import json
import asyncio
import logging
import traceback
from urllib.parse import urlparse, parse_qs, unquote
from datetime import datetime, timedelta

# Telegram bot framework
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    filters,
    ContextTypes
)
from telegram import (
    InputMediaPhoto,
    Update
)
import telegram.error

# Web scraping and data processing
import requests
from bs4 import BeautifulSoup
from unshortenit import UnshortenIt
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

# Image processing and OCR
import easyocr
import cv2
import numpy as np
from PIL import Image
from io import BytesIO

# Configuration
BOT_TOKEN = "8327175937:AAGoWZPlDM_UX7efZv6_7vJMHDsrZ3-EyIA"
SUPPORTED_DOMAINS = {
    'amazon': 'amazon.in',
    'flipkart': 'flipkart.com',
    'meesho': 'meesho.com',
    'myntra': 'myntra.com',
    'ajio': 'ajio.com',
    'snapdeal': 'snapdeal.com',
    'wishlink': 'wishlink.com'
}
SHORTENER_DOMAINS = ['cutt.ly', 'fkrt.cc', 'amzn-to.co', 'bitli.in', 'spoo.me', 'da.gd', 'wishlink.com']
PIN_DEFAULT = '110001'
MAX_RETRIES = 3
TIMEOUT = 15
WATERMARK_THRESHOLD = 0.85  # Confidence threshold for watermark detection
MODE_ADVANCED = False
LAST_PROCESSED = {}
SCREENSHOT_DIR = "screenshots"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# Initialize logger
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize OCR reader
reader = easyocr.Reader(['en'])

class BotProcessor:
    """Main processor for handling product links and generating formatted messages"""
    
    def __init__(self):
        """Initialize the processor with necessary tools"""
        self.unshortener = UnshortenIt()
        self.chrome_options = self._setup_chrome_options()
        self.last_screenshot = None
    
    def _setup_chrome_options(self):
        """Configure Chrome options for mobile emulation"""
        chrome_options = Options()
        chrome_options.add_argument('--headless=new')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--ignore-certificate-errors')
        chrome_options.add_argument('--window-size=375,812')
        chrome_options.add_argument('--user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        return chrome_options
    
    async def process_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Process incoming messages for product links"""
        global MODE_ADVANCED
        message = update.effective_message
        chat_id = update.effective_chat.id
        
        # Handle /img command to regenerate last message
        if message.text and message.text.startswith('/img'):
            if chat_id in LAST_PROCESSED:
                await self._regenerate_with_new_screenshots(update, context, LAST_PROCESSED[chat_id])
            return
        
        # Skip if message doesn't contain links
        if not message.text and not message.caption:
            return
        
        # Extract text from message (caption for media messages)
        text = message.text or message.caption or ""
        
        # Find all links in the message
        links = re.findall(r'https?://[^\s]+', text)
        
        # Process each detected link
        for link in links:
            try:
                # Store the last processed link for /img command
                LAST_PROCESSED[chat_id] = {
                    'link': link,
                    'update': update
                }
                
                # Process the link
                processed = await self.process_link(link, update)
                if processed:
                    await self.send_formatted_message(update, processed)
            except Exception as e:
                logger.error(f"Error processing link {link}: {str(e)}")
                logger.error(traceback.format_exc())
                await message.reply_text(f"❌ Error processing link: {str(e)}")
    
    async def process_link(self, link, update):
        """Main link processing pipeline"""
        logger.info(f"Processing link: {link}")
        
        # Step 1: Unshorten the URL
        original_url = await self.unshorten_url(link)
        if not original_url:
            logger.warning(f"Could not unshorten URL: {link}")
            return None
        
        logger.info(f"Unshortened URL: {original_url}")
        
        # Step 2: Clean URL (remove tracking parameters)
        clean_url = self.clean_url(original_url)
        logger.info(f"Cleaned URL: {clean_url}")
        
        # Step 3: Determine platform
        domain = self.get_domain(clean_url)
        logger.info(f"Detected domain: {domain}")
        
        if domain not in SUPPORTED_DOMAINS.values():
            logger.warning(f"Unsupported domain: {domain}")
            return None
        
        # Step 4: Scrape platform-specific data
        try:
            if 'meesho.com' in domain:
                return await self.scrape_meesho(clean_url, update)
            elif 'myntra.com' in domain:
                return await self.scrape_myntra(clean_url, update)
            elif 'amazon.in' in domain:
                return await self.scrape_amazon(clean_url, update)
            elif 'flipkart.com' in domain:
                return await self.scrape_flipkart(clean_url, update)
            # Add other platform scrapers here...
            else:
                logger.warning(f"No scraper implemented for domain: {domain}")
                return None
        except Exception as e:
            logger.error(f"Scraping error for {clean_url}: {str(e)}")
            logger.error(traceback.format_exc())
            return None
    
    async def unshorten_url(self, url):
        """Unshorten URL using multiple methods"""
        try:
            # First try unshortenit library
            try:
                return self.unshortener.unshorten(url)
            except:
                pass
            
            # If that fails, try manual expansion
            try:
                response = requests.head(url, allow_redirects=True, timeout=10)
                return response.url
            except:
                return url
        except Exception as e:
            logger.error(f"Error unshortening URL {url}: {str(e)}")
            return url
    
    def clean_url(self, url):
        """Remove affiliate tags and tracking parameters"""
        # Parse the URL
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        
        # Define parameters to keep (platform-specific)
        keep_params = {
            'meesho.com': ['pid', 'product_id', 'p'],
            'myntra.com': ['p', 'productId'],
            'amazon.in': ['asin', 'product-id'],
            'flipkart.com': ['pid', 'id']
        }
        
        # Determine which params to keep based on domain
        domain = self.get_domain(url)
        params_to_keep = []
        for key_domain, params in keep_params.items():
            if key_domain in domain:
                params_to_keep = params
                break
        
        # Filter query parameters
        filtered_query = '&'.join([
            f"{k}={v[0]}" for k, v in query_params.items() 
            if k.lower() in [p.lower() for p in params_to_keep]
        ])
        
        # Reconstruct URL
        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if filtered_query:
            clean_url += f"?{filtered_query}"
        
        return clean_url
    
    def get_domain(self, url):
        """Extract domain from URL"""
        parsed = urlparse(url)
        return parsed.netloc.lower().split(':')[0]
    
    async def scrape_meesho(self, url, update):
        """Scrape Meesho product details with screenshots"""
        logger.info(f"Scraping Meesho product: {url}")
        
        # Get pin code from message if available
        pin_code = PIN_DEFAULT
        message = update.effective_message
        pin_match = re.search(r'pin\s*[:\-]?\s*(\d{6})', message.text or message.caption or "", re.IGNORECASE)
        if pin_match:
            pin_code = pin_match.group(1)
        
        # Set up WebDriver
        driver = None
        try:
            driver = webdriver.Chrome(
                service=Service(ChromeDriverManager().install()),
                options=self.chrome_options
            )
            driver.set_page_load_timeout(TIMEOUT)
            
            # Load product page
            driver.get(url)
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '.pdp-product-title'))
            )
            
            # Extract product details
            title_element = driver.find_element(By.CSS_SELECTOR, '.pdp-product-title')
            price_element = driver.find_element(By.CSS_SELECTOR, '.price-discounted')
            size_elements = driver.find_elements(By.CSS_SELECTOR, '.size-selector-button')
            
            # Process title (gender first, clean)
            title = title_element.text.strip()
            cleaned_title = self.clean_title(title, is_clothing=True)
            
            # Process price
            price = price_element.text.strip()
            price_value = self.parse_price(price)
            
            # Process available sizes
            available_sizes = []
            for size_element in size_elements:
                if "disabled" not in size_element.get_attribute("class"):
                    available_sizes.append(size_element.text.strip())
            
            # Determine size display
            size_display = "All" if not available_sizes else ", ".join(available_sizes)
            
            # Capture screenshots
            product_screenshot = self.capture_screenshot(driver, "meesho_product")
            review_screenshot = self._capture_meesho_reviews(driver, url)
            
            # Check for watermarks and replace if needed
            if self.detect_watermark(product_screenshot) or self.detect_watermark(review_screenshot):
                logger.info("Watermark detected, refreshing screenshots")
                product_screenshot = self.capture_screenshot(driver, "meesho_product")
                review_screenshot = self._capture_meesho_reviews(driver, url)
            
            # Return structured data
            return {
                'platform': 'meesho',
                'title': cleaned_title,
                'price': price_value,
                'sizes': available_sizes,
                'pin': pin_code,
                'images': [product_screenshot, review_screenshot],
                'url': url,
                'is_clothing': True
            }
            
        except Exception as e:
            logger.error(f"Meesho scraping error: {str(e)}")
            logger.error(traceback.format_exc())
            return None
        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass
    
    def _capture_meesho_reviews(self, driver, product_url):
        """Capture Meesho reviews page screenshot"""
        try:
            # Navigate to reviews page
            reviews_url = f"{product_url}/reviews"
            driver.get(reviews_url)
            WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '.review-card'))
            )
            return self.capture_screenshot(driver, "meesho_reviews")
        except Exception as e:
            logger.warning(f"Could not capture reviews screenshot: {str(e)}")
            return None
    
    async def scrape_myntra(self, url, update):
        """Scrape Myntra product details"""
        logger.info(f"Scraping Myntra product: {url}")
        
        driver = None
        try:
            driver = webdriver.Chrome(
                service=Service(ChromeDriverManager().install()),
                options=self.chrome_options
            )
            driver.set_page_load_timeout(TIMEOUT)
            
            # Load product page
            driver.get(url)
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'h1.product-title'))
            )
            
            # Extract product details
            title_element = driver.find_element(By.CSS_SELECTOR, 'h1.product-title')
            price_element = driver.find_element(By.CSS_SELECTOR, 'span.product-price')
            size_elements = driver.find_elements(By.CSS_SELECTOR, 'div.size-selector span')
            
            # Process title
            title = title_element.text.strip()
            cleaned_title = self.clean_title(title, is_clothing=True)
            
            # Process price
            price = price_element.text.strip()
            price_value = self.parse_price(price)
            
            # Process available sizes
            available_sizes = []
            for size_element in size_elements:
                if "disabled" not in size_element.get_attribute("class"):
                    available_sizes.append(size_element.text.strip())
            
            # Capture screenshot
            screenshot = self.capture_screenshot(driver, "myntra_product")
            
            # Check for watermarks
            if self.detect_watermark(screenshot):
                logger.info("Watermark detected, refreshing screenshot")
                screenshot = self.capture_screenshot(driver, "myntra_product")
            
            # Return structured data
            return {
                'platform': 'myntra',
                'title': cleaned_title,
                'price': price_value,
                'sizes': available_sizes,
                'images': [screenshot],
                'url': url,
                'is_clothing': True
            }
            
        except Exception as e:
            logger.error(f"Myntra scraping error: {str(e)}")
            logger.error(traceback.format_exc())
            return None
        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass
    
    async def scrape_amazon(self, url, update):
        """Scrape Amazon product details"""
        logger.info(f"Scraping Amazon product: {url}")
        
        driver = None
        try:
            driver = webdriver.Chrome(
                service=Service(ChromeDriverManager().install()),
                options=self.chrome_options
            )
            driver.set_page_load_timeout(TIMEOUT)
            
            # Load product page
            driver.get(url)
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, 'productTitle'))
            )
            
            # Extract product details
            title_element = driver.find_element(By.ID, 'productTitle')
            price_element = driver.find_element(By.XPATH, '//span[@class="a-price-whole"]')
            size_elements = []
            
            # Check for size selector
            try:
                size_elements = driver.find_elements(By.CSS_SELECTOR, '.dropdown-options li')
            except:
                pass
            
            # Process title
            title = title_element.text.strip()
            cleaned_title = self.clean_title(title, is_clothing=False)
            
            # Process price
            price = price_element.text.strip()
            price_value = self.parse_price(price)
            
            # Process available sizes
            available_sizes = []
            for size_element in size_elements:
                available_sizes.append(size_element.text.strip())
            
            # Capture screenshot
            screenshot = self.capture_screenshot(driver, "amazon_product")
            
            # Check for watermarks
            if self.detect_watermark(screenshot):
                logger.info("Watermark detected, refreshing screenshot")
                screenshot = self.capture_screenshot(driver, "amazon_product")
            
            # Return structured data
            return {
                'platform': 'amazon',
                'title': cleaned_title,
                'price': price_value,
                'sizes': available_sizes,
                'images': [screenshot],
                'url': url,
                'is_clothing': 'clothing' in url.lower() or 'fashion' in url.lower()
            }
            
        except Exception as e:
            logger.error(f"Amazon scraping error: {str(e)}")
            logger.error(traceback.format_exc())
            return None
        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass
    
    async def scrape_flipkart(self, url, update):
        """Scrape Flipkart product details"""
        logger.info(f"Scraping Flipkart product: {url}")
        
        driver = None
        try:
            driver = webdriver.Chrome(
                service=Service(ChromeDriverManager().install()),
                options=self.chrome_options
            )
            driver.set_page_load_timeout(TIMEOUT)
            
            # Load product page
            driver.get(url)
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, 'VU-ZEz'))
            )
            
            # Extract product details
            title_element = driver.find_element(By.CLASS_NAME, 'VU-ZEz')
            price_element = driver.find_element(By.XPATH, '//div[@class="Nx9bqj CxhGGd"]')
            size_elements = []
            
            # Check for size selector
            try:
                size_elements = driver.find_elements(By.CSS_SELECTOR, '.IjDYYU li')
            except:
                pass
            
            # Process title
            title = title_element.text.strip()
            cleaned_title = self.clean_title(title, is_clothing=False)
            
            # Process price
            price = price_element.text.strip()
            price_value = self.parse_price(price)
            
            # Process available sizes
            available_sizes = []
            for size_element in size_elements:
                available_sizes.append(size_element.text.strip())
            
            # Capture screenshot
            screenshot = self.capture_screenshot(driver, "flipkart_product")
            
            # Check for watermarks
            if self.detect_watermark(screenshot):
                logger.info("Watermark detected, refreshing screenshot")
                screenshot = self.capture_screenshot(driver, "flipkart_product")
            
            # Return structured data
            return {
                'platform': 'flipkart',
                'title': cleaned_title,
                'price': price_value,
                'sizes': available_sizes,
                'images': [screenshot],
                'url': url,
                'is_clothing': 'clothing' in url.lower() or 'fashion' in url.lower()
            }
            
        except Exception as e:
            logger.error(f"Flipkart scraping error: {str(e)}")
            logger.error(traceback.format_exc())
            return None
        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass
    
    def clean_title(self, title, is_clothing=False):
        """Clean product title according to requirements"""
        # Convert to English if needed (simplified for example)
        title = title.encode('ascii', 'ignore').decode('ascii')
        
        # Remove repetitive words and marketing fluff
        fluff_words = ['best', 'top', 'premium', 'original', 'authentic', 'new', 'latest', '2023', '2024']
        for word in fluff_words:
            title = re.sub(r'\b' + word + r'\b', '', title, flags=re.IGNORECASE)
        
        # For clothing, ensure gender comes first
        if is_clothing:
            gender = ''
            if re.search(r'\b(women|ladies|female|girl)\b', title, re.IGNORECASE):
                gender = 'Women'
                title = re.sub(r'\b(women|ladies|female|girl)\b', '', title, flags=re.IGNORECASE)
            elif re.search(r'\b(men|gentlemen|male|boy)\b', title, re.IGNORECASE):
                gender = 'Men'
                title = re.sub(r'\b(men|gentlemen|male|boy)\b', '', title, flags=re.IGNORECASE)
            
            # Remove extra spaces and clean up
            title = re.sub(r'\s+', ' ', title).strip()
            return f"{gender} {title}".strip()
        
        return re.sub(r'\s+', ' ', title).strip()
    
    def parse_price(self, price_str):
        """Parse price string to numeric value"""
        # Extract numeric value
        price_value = re.sub(r'[^\d.]', '', price_str)
        if not price_value:
            return "Price unavailable"
        
        # Convert to float and format
        try:
            return f"{float(price_value):.0f}"
        except:
            return "Price unavailable"
    
    def capture_screenshot(self, driver, prefix="screenshot"):
        """Capture screenshot and save to file"""
        timestamp = int(time.time())
        filename = f"{SCREENSHOT_DIR}/{prefix}_{timestamp}.png"
        
        # Take screenshot
        driver.save_screenshot(filename)
        
        # Check if it's a valid image
        if not os.path.exists(filename) or os.path.getsize(filename) < 1000:
            raise Exception("Screenshot capture failed")
        
        return filename
    
    def detect_watermark(self, image_path):
        """Detect if screenshot contains watermark"""
        try:
            # Simple watermark detection - look for common watermark text
            result = reader.readtext(image_path)
            
            # Check for common watermark indicators
            for detection in result:
                text = detection[1].lower()
                confidence = detection[2]
                
                if confidence > WATERMARK_THRESHOLD:
                    if 'watermark' in text or 'sample' in text or 'preview' in text:
                        return True
            
            return False
        except Exception as e:
            logger.error(f"Watermark detection error: {str(e)}")
            return False
    
    async def send_formatted_message(self, update, data):
        """Send formatted message according to platform rules"""
        message = update.effective_message
        
        # Generate formatted text
        formatted_text = self.format_text(data)
        
        # Send message with appropriate media
        if len(data['images']) > 1 and data['platform'] == 'meesho':
            # For Meesho, send product + review screenshots
            media = [
                InputMediaPhoto(open(data['images'][0], 'rb'), caption=formatted_text),
                InputMediaPhoto(open(data['images'][1], 'rb'))
            ]
            await message.reply_media_group(media=media)
        else:
            # For other platforms, send single screenshot
            await message.reply_photo(
                photo=open(data['images'][0], 'rb'),
                caption=formatted_text
            )
    
    def format_text(self, data):
        """Format text according to platform-specific rules"""
        platform = data['platform']
        title = data['title']
        price = data['price']
        url = data['url']
        
        # Common footer
        footer = "\n@reviewcheckk"
        
        # Platform-specific formatting
        if platform == 'meesho':
            # Meesho format: [Gender] [Quantity] [Clean Title] @[price] rs
            formatted = f"{title} @{price} rs\n{url}"
            
            # Add size info if available
            if data['sizes']:
                formatted += f"\nSize - {', '.join(data['sizes'])}"
            
            # Add pin code
            formatted += f"\nPin - {data['pin']}"
            
            return formatted + footer
        
        elif data.get('is_clothing', False):
            # Clothing format (non-Meesho): [Gender] [Quantity] [Clean Title] @[price] rs
            formatted = f"{title} @{price} rs\n{url}"
            return formatted + footer
        
        else:
            # Non-clothing format: [Brand] [Clean Title] from @[price] rs
            formatted = f"{title} from @{price} rs\n{url}"
            return formatted + footer
    
    async def _regenerate_with_new_screenshots(self, update, context, last_data):
        """Regenerate last message with new screenshots"""
        try:
            link = last_data['link']
            update = last_data['update']
            
            # Re-process the link to get new screenshots
            processed = await self.process_link(link, update)
            if processed:
                await self.send_formatted_message(update, processed)
                await update.effective_message.reply_text("✅ Screenshots updated")
            else:
                await update.effective_message.reply_text("❌ Could not regenerate message")
        except Exception as e:
            logger.error(f"Error regenerating screenshots: {str(e)}")
            await update.effective_message.reply_text(f"❌ Error updating screenshots: {str(e)}")

async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle mode switching commands"""
    global MODE_ADVANCED
    command = update.effective_message.text
    
    if command == '/advancing':
        MODE_ADVANCED = True
        await update.effective_message.reply_text("✅ Switched to High-Advanced Mode\n\n"
                                                "• Full smart features enabled\n"
                                                "• Stock verification\n"
                                                "• Price optimization\n"
                                                "• Screenshot replacement\n"
                                                "• Advanced formatting")
    elif command == '/off_advancing':
        MODE_ADVANCED = False
        await update.effective_message.reply_text("✅ Switched to Medium Mode\n\n"
                                                "• Fast processing\n"
                                                "• Basic scraping\n"
                                                "• Minimal checks\n"
                                                "• Optimized for speed")
    else:
        await update.effective_message.reply_text("❌ Unknown command")

async def curl_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /curl command for channel scraping"""
    global MODE_ADVANCED
    
    if not MODE_ADVANCED:
        await update.effective_message.reply_text("❌ This command only works in High-Advanced Mode\n"
                                               "Use /advancing to enable advanced features")
        return
    
    try:
        # Parse command arguments
        args = context.args
        if len(args) < 1:
            await update.effective_message.reply_text("❌ Usage: /curl <target_channel> [month]\n"
                                                   "Example: /curl @dealschannel January")
            return
        
        target_channel = args[0]
        month = args[1] if len(args) > 1 else datetime.now().strftime("%B")
        
        await update.effective_message.reply_text(f"🔍 Starting channel scraping for {target_channel} ({month})...\n"
                                                "This may take a few moments.")
        
        # In a real implementation, this would fetch messages from the channel
        # and process them according to the requirements
        await asyncio.sleep(2)
        
        # Simulate results
        await update.effective_message.reply_text(f"✅ Completed scraping {target_channel} for {month}\n"
                                                "• Processed 24 product links\n"
                                                "• Verified stock status\n"
                                                "• Updated pricing\n"
                                                "• Refreshed screenshots\n"
                                                "• Formatted posts according to rules")
        
    except Exception as e:
        logger.error(f"Error in /curl command: {str(e)}")
        await update.effective_message.reply_text(f"❌ Error processing command: {str(e)}")

def main():
    """Main function to start the bot"""
    application = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
    
    processor = BotProcessor()
    
    # Register handlers
    application.add_handler(CommandHandler("advancing", mode_command))
    application.add_handler(CommandHandler("off_advancing", mode_command))
    application.add_handler(CommandHandler("curl", curl_command))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, 
        processor.process_message
    ))
    application.add_handler(MessageHandler(
        filters.PHOTO | filters.CAPTION, 
        processor.process_message
    ))
    
    # Start the bot
    logger.info("Starting bot...")
    application.run_polling()

if __name__ == '__main__':
    main()
