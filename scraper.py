#!/usr/bin/env python3
"""
ToffeeLive Complete Channel Scraper
Creates M3U playlist with all channels, headers, and cookies
"""

import json
import os
import re
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


class ToffeeScraper:
    def __init__(self):
        self.base_url = "https://toffeelive.com/en"
        self.channels = []
        self.global_cookies = {}
        self.session_headers = {}
        self.output_dir = "output"
        
        # Create output directories
        os.makedirs(f"{self.output_dir}/headers", exist_ok=True)
        
    def scrape(self):
        """Main scraping function"""
        print("🚀 Starting ToffeeLive scraper...")
        
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
            
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0",
                viewport={"width": 1920, "height": 1080}
            )
            
            page = context.new_page()
            
            # Store all network activity
            network_log = []
            stream_urls = []
            
            def handle_route(route, request):
                """Capture all network requests"""
                url = request.url
                
                # Capture M3U/M3U8 streams
                if any(ext in url.lower() for ext in ['.m3u8', '.m3u', 'playlist', 'manifest']):
                    stream_urls.append({
                        "url": url,
                        "headers": dict(request.headers),
                        "timestamp": datetime.now().isoformat(),
                        "page": request.frame.url if request.frame else "unknown"
                    })
                
                # Capture API calls
                if any(x in url.lower() for x in ['/api/', 'stream', 'channel', 'live']):
                    network_log.append({
                        "url": url,
                        "method": request.method,
                        "headers": dict(request.headers),
                        "resource_type": request.resource_type
                    })
                
                route.continue_()
            
            page.route("**/*", handle_route)
            
            # Navigate to site
            print(f"📍 Loading {self.base_url}...")
            try:
                page.goto(self.base_url, wait_until="networkidle", timeout=60000)
                time.sleep(5)
            except PlaywrightTimeout:
                print("⚠️ Timeout loading main page, continuing...")
            
            # Get global cookies
            self.global_cookies = {c["name"]: c["value"] for c in context.cookies()}
            print(f"🍪 Global cookies captured: {len(self.global_cookies)}")
            
            # Extract channel list
            print("🔍 Searching for channels...")
            channels_data = self._extract_channels(page)
            
            # Process each channel
            total = len(channels_data)
            print(f"📺 Found {total} channels")
            
            for idx, channel in enumerate(channels_data, 1):
                print(f"\n⏳ Processing [{idx}/{total}]: {channel.get('name', 'Unknown')}")
                
                channel_data = self._process_channel(page, channel, context)
                if channel_data:
                    self.channels.append(channel_data)
                
                # Small delay between channels
                time.sleep(2)
            
            # Add intercepted streams to channels if not already captured
            self._match_streams_to_channels(stream_urls)
            
            browser.close()
        
        # Save all data
        self._save_data()
        self._generate_m3u()
        
        print(f"\n✅ Scraping complete!")
        print(f"📊 Total channels: {len(self.channels)}")
        print(f"📁 Files saved in: {self.output_dir}/")
        
    def _extract_channels(self, page):
        """Extract channel list from page"""
        channels = []
        
        # Try multiple selectors for channel elements
        selectors = [
            '[data-channel-id]',
            '.channel-item',
            '.channel-card',
            '[class*="channel"]',
            '.live-channel',
            '.tv-channel',
            '.stream-item',
            '.grid-item'
        ]
        
        for selector in selectors:
            elements = page.query_selector_all(selector)
            if elements:
                print(f"✅ Found channels using selector: {selector}")
                for el in elements:
                    try:
                        channel = {
                            "id": el.get_attribute("data-channel-id") or el.get_attribute("id") or "",
                            "name": self._get_text(el, ['.channel-name', '.title', 'h3', 'h4', '.name', 'span', 'div']) or "Unknown",
                            "logo": self._get_attribute(el, ['img', '.logo', '.channel-logo'], 'src') or "",
                            "group": self._get_text(el, ['.category', '.group', '.genre']) or "General",
                            "element_html": el.inner_html()
                        }
                        if channel["name"] != "Unknown":
                            channels.append(channel)
                    except Exception as e:
                        continue
                break
        
        # If no channels found via selectors, try JavaScript evaluation
        if not channels:
            print("🔧 Trying JavaScript extraction...")
            try:
                js_channels = page.evaluate("""
                    () => {
                        const results = [];
                        // Look for common patterns
                        const patterns = [
                            window.channels,
                            window.channelList,
                            window.__CHANNELS__,
                            window.__DATA__?.channels,
                            window.__INITIAL_STATE__?.channels
                        ];
                        
                        for (const data of patterns) {
                            if (data && Array.isArray(data)) {
                                results.push(...data);
                            }
                        }
                        return results;
                    }
                """)
                
                if js_channels:
                    for ch in js_channels:
                        channels.append({
                            "id": str(ch.get("id", "")),
                            "name": ch.get("name", ch.get("title", "Unknown")),
                            "logo": ch.get("logo", ch.get("icon", "")),
                            "group": ch.get("category", ch.get("group", "General")),
                            "source": "javascript"
                        })
            except Exception as e:
                print(f"JS extraction failed: {e}")
        
        # Remove duplicates by name
        seen = set()
        unique = []
        for ch in channels:
            if ch["name"] not in seen:
                seen.add(ch["name"])
                unique.append(ch)
        
        return unique
    
    def _process_channel(self, page, channel, context):
        """Process individual channel and get stream data"""
        result = {
            "id": channel.get("id", ""),
            "name": channel.get("name", "Unknown"),
            "logo": channel.get("logo", ""),
            "group": channel.get("group", "General"),
            "stream_url": None,
            "headers": {},
            "cookies": {},
            "captured_at": datetime.now().isoformat()
        }
        
        try:
            # Try to click on channel to load stream
            channel_selector = f'[data-channel-id="{channel.get("id", "")}"]'
            
            # Find and click channel
            channel_el = page.query_selector(f'text={channel["name"]}') or \
                        page.query_selector(channel_selector)
            
            if channel_el:
                print(f"   🖱️ Clicking channel...")
                channel_el.click()
                time.sleep(3)
                
                # Get page URL after click
                result["page_url"] = page.url
                
                # Capture cookies after click
                cookies = context.cookies()
                result["cookies"] = {c["name"]: c["value"] for c in cookies}
                
                # Look for video element
                video = page.query_selector("video")
                if video:
                    src = video.get_attribute("src")
                    if src:
                        result["stream_url"] = src
                        print(f"   ✅ Stream URL found: {src[:80]}...")
                
                # Check for iframe
                iframe = page.query_selector("iframe")
                if iframe:
                    src = iframe.get_attribute("src")
                    if src:
                        result["iframe_url"] = src
                
                # Extract from page source
                content = page.content()
                m3u_matches = re.findall(r'https?://[^\s"\']+\.m3u8?[^\s"\']*', content)
                if m3u_matches:
                    result["stream_url"] = m3u_matches[0]
                    print(f"   ✅ M3U found in page source")
                
                # Get request headers from network activity
                result["headers"] = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0",
                    "Referer": page.url,
                    "Origin": "https://toffeelive.com"
                }
                
        except Exception as e:
            print(f"   ⚠️ Error: {str(e)[:50]}")
        
        return result
    
    def _match_streams_to_channels(self, stream_urls):
        """Match intercepted streams to channels"""
        for stream in stream_urls:
            # Try to find matching channel or create new entry
            matched = False
            for ch in self.channels:
                if not ch.get("stream_url"):
                    ch["stream_url"] = stream["url"]
                    ch["headers"].update(stream["headers"])
                    matched = True
                    break
            
            if not matched:
                # Create new channel entry from stream
                self.channels.append({
                    "name": f"Channel_{len(self.channels)+1}",
                    "stream_url": stream["url"],
                    "headers": stream["headers"],
                    "cookies": self.global_cookies,
                    "captured_at": stream["timestamp"],
                    "source": "network_intercept"
                })
    
    def _get_text(self, element, selectors):
        """Try multiple selectors to get text"""
        for sel in selectors:
            try:
                el = element.query_selector(sel)
                if el:
                    return el.inner_text().strip()
            except:
                continue
        return None
    
    def _get_attribute(self, element, selectors, attr):
        """Try multiple selectors to get attribute"""
        for sel in selectors:
            try:
                el = element.query_selector(sel)
                if el:
                    return el.get_attribute(attr)
            except:
                continue
        return None
    
    def _save_data(self):
        """Save all collected data"""
        # Save channels JSON
        with open(f"{self.output_dir}/channels.json", "w", encoding="utf-8") as f:
            json.dump({
                "updated_at": datetime.now().isoformat(),
                "total_channels": len(self.channels),
                "channels": self.channels
            }, f, indent=2, ensure_ascii=False)
        
        # Save individual channel headers
        for idx, ch in enumerate(self.channels):
            safe_name = re.sub(r'[^\w]', '_', ch["name"])[:50]
            header_file = f"{self.output_dir}/headers/{idx+1:03d}_{safe_name}.json"
            
            with open(header_file, "w", encoding="utf-8") as f:
                json.dump({
                    "channel_name": ch["name"],
                    "channel_id": ch.get("id", ""),
                    "stream_url": ch.get("stream_url", ""),
                    "headers": ch.get("headers", {}),
                    "cookies": ch.get("cookies", {}),
                    "captured_at": ch.get("captured_at", "")
                }, f, indent=2)
        
        # Save global cookies
        with open(f"{self.output_dir}/cookies.json", "w") as f:
            json.dump(self.global_cookies, f, indent=2)
    
    def _generate_m3u(self):
        """Generate M3U playlist file"""
        m3u_path = f"{self.output_dir}/playlist.m3u"
        
        with open(m3u_path, "w", encoding="utf-8") as f:
            # Write header
            f.write("#EXTM3U\n")
            f.write(f'#PLAYLIST:Toffee Live - {datetime.now().strftime("%Y-%m-%d")}\n')
            f.write(f'#UPDATED:{datetime.now().isoformat()}\n')
            f.write(f'#TOTAL_CHANNELS:{len(self.channels)}\n')
            f.write(f'#SOURCE:toffeelive.com\n\n')
            
            # Write each channel
            for ch in self.channels:
                if ch.get("stream_url"):
                    # EXTINF line with metadata
                    logo_attr = f' tvg-logo="{ch["logo"]}"' if ch.get("logo") else ""
                    group_attr = f' group-title="{ch["group"]}"' if ch.get("group") else ""
                    id_attr = f' tvg-id="{ch["id"]}"' if ch.get("id") else ""
                    
                    f.write(f'#EXTINF:-1{id_attr}{logo_attr}{group_attr},{ch["name"]}\n')
                    
                    # Add headers as comments (for advanced players)
                    headers = ch.get("headers", {})
                    if headers.get("Referer"):
                        f.write(f'#EXTVLCOPT:http-referrer={headers["Referer"]}\n')
                    if headers.get("User-Agent"):
                        f.write(f'#EXTVLCOPT:http-user-agent={headers["User-Agent"]}\n')
                    
                    # Cookies if available
                    cookies = ch.get("cookies", {})
                    if cookies:
                        cookie_str = "; ".join([f"{k}={v}" for k, v in cookies.items()])
                        f.write(f'#EXTVLCOPT:http-cookie={cookie_str}\n')
                    
                    # Stream URL
                    f.write(f'{ch["stream_url"]}\n\n')
        
        print(f"📝 M3U playlist saved: {m3u_path}")
        
        # Also create a simple URL list
        with open(f"{self.output_dir}/urls.txt", "w") as f:
            for ch in self.channels:
                if ch.get("stream_url"):
                    f.write(f'{ch["name"]}: {ch["stream_url"]}\n')


if __name__ == "__main__":
    scraper = ToffeeScraper()
    scraper.scrape()
