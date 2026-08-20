#!/usr/bin/env python3
import requests
import json
import re
import os
from datetime import datetime

class ToffeeScraper:
    def __init__(self):
        self.base_url = "https://toffeelive.com"
        self.api_base = "https://toffeelive.com/api"
        self.session = requests.Session()
        
        # Headers যা ব্রাউজার থেকে আসে
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Origin': 'https://toffeelive.com',
            'Referer': 'https://toffeelive.com/',
        })
        
        self.channels_data = []
    
    def get_channel_list(self):
        """লাইভ টিভি চ্যানেল লিস্ট API থেকে নাও"""
        try:
            # Storefront API থেকে চ্যানেল লিস্ট
            url = f"{self.api_base}/storefront/v1/views/live"
            
            response = self.session.get(url, timeout=30)
            data = response.json()
            
            if data.get('success') and data.get('data'):
                rails = data['data'].get('rails', {}).get('list', [])
                channels = []
                
                for rail in rails:
                    if rail.get('type') == 'editorial':
                        # প্রতিটি রেল থেকে চ্যানেল আইটেম নাও
                        rail_data = self.get_rail_data(rail.get('apiPath'))
                        channels.extend(rail_data)
                
                return channels
            return []
        except Exception as e:
            print(f"Error fetching channel list: {e}")
            return []
    
    def get_rail_data(self, api_path):
        """নির্দিষ্ট রেল/ক্যাটাগরি থেকে চ্যানেল নাও"""
        try:
            url = f"{self.base_url}/{api_path}"
            response = self.session.get(url, timeout=30)
            data = response.json()
            
            channels = []
            if data.get('success') and data.get('data'):
                items = data['data'].get('items', [])
                for item in items:
                    channels.append({
                        'id': item.get('id'),
                        'title': item.get('title'),
                        'type': item.get('type'),
                        'poster': item.get('posterUrl')
                    })
            return channels
        except Exception as e:
            print(f"Error fetching rail data: {e}")
            return []
    
    def get_stream_url(self, channel_id):
        """চ্যানেলের স্ট্রিম URL বের করুন"""
        try:
            # Step 1: Asset details নাও
            asset_url = f"{self.api_base}/storefront/v1/asset/{channel_id}"
            response = self.session.get(asset_url, timeout=30)
            asset_data = response.json()
            
            if not asset_data.get('success'):
                return None
            
            media_info = asset_data['data'].get('media', [])
            if not media_info:
                return None
            
            media_id = media_info[0].get('mediaId')
            
            # Step 2: Playback URL জন্য API কল
            # Toffee সম্ভবত DRM/Token সহ URL দেয়
            playback_url = f"{self.api_base}/playback/v1/media/{media_id}"
            
            playback_response = self.session.get(playback_url, timeout=30)
            playback_data = playback_response.json()
            
            if playback_data.get('success'):
                # M3U8 URL খুঁজুন
                sources = playback_data['data'].get('sources', [])
                for source in sources:
                    if source.get('type') == 'application/x-mpegURL':
                        return {
                            'url': source.get('src'),
                            'drm': source.get('drm')
                        }
                # অথবা সরাসরি URL
                return playback_data['data'].get('url')
            
            return None
            
        except Exception as e:
            print(f"Error getting stream for {channel_id}: {e}")
            return None
    
    def scrape_from_html(self, html_content):
        """HTML থেকে embedded JSON data বের করুন"""
        try:
            # Next.js data extraction
            pattern = r'self\.__next_f\.push\(\[1,"(.*?)"\]\)'
            matches = re.findall(pattern, html_content, re.DOTALL)
            
            for match in matches:
                try:
                    # Escape sequences handle করুন
                    cleaned = match.replace('\\\\"', '"').replace('\\\\n', '\n')
                    if 'get-storefront-asset' in cleaned:
                        # JSON data খুঁজুন
                        json_pattern = r'"data":\s*(\{.*?"v_type":"channels".*?\})'
                        json_match = re.search(json_pattern, cleaned)
                        if json_match:
                            data = json.loads(json_match.group(1))
                            return data
                except:
                    continue
            return None
        except Exception as e:
            print(f"Error parsing HTML: {e}")
            return None
    
    def generate_m3u(self, output_path="output/toffee_playlist.m3u"):
        """M3U প্লেলিস্ট তৈরি করুন"""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("#EXTM3U\n")
            f.write(f"# Toffee Live Playlist\n")
            f.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# Total Channels: {len(self.channels_data)}\n\n")
            
            for channel in self.channels_data:
                if channel.get('stream_url'):
                    f.write(f"#EXTINF:-1 tvg-id=\"{channel['id']}\"")
                    if channel.get('poster'):
                        f.write(f" tvg-logo=\"{channel['poster']}\"")
                    f.write(f",{channel['title']}\n")
                    
                    # DRM থাকলে #KODIPROP যোগ করুন
                    if channel.get('drm'):
                        f.write(f"#KODIPROP:inputstream.adaptive.license_type={channel['drm'].get('type', 'clearkey')}\n")
                        f.write(f"#KODIPROP:inputstream.adaptive.license_key={channel['drm'].get('key', '')}\n")
                    
                    f.write(f"{channel['stream_url']}\n\n")
        
        print(f"✅ Playlist saved: {output_path}")
        print(f"📺 Total channels: {len(self.channels_data)}")
        return output_path
    
    def scrape(self):
        """মূল স্ক্র্যাপিং প্রসেস"""
        print("🚀 Starting Toffee Scraper...")
        
        # HTML থেকে সরাসরি ডেটা নেওয়ার চেষ্টা
        # অথবা API কল করুন
        
        channel_ids = [
            "wHLVIJ4B7a1HdMSjaGLJ",  # NTV
            # আরও চ্যানেল IDs যোগ করুন
        ]
        
        for channel_id in channel_ids:
            print(f"📡 Processing: {channel_id}")
            
            # Asset details নাও
            stream_info = self.get_stream_url(channel_id)
            
            if stream_info:
                self.channels_data.append({
                    'id': channel_id,
                    'title': f"Channel {channel_id[:8]}",
                    'stream_url': stream_info.get('url') if isinstance(stream_info, dict) else stream_info,
                    'drm': stream_info.get('drm') if isinstance(stream_info, dict) else None,
                    'poster': None
                })
                print(f"   ✅ Stream found")
            else:
                print(f"   ❌ No stream")
        
        # প্লেলিস্ট জেনারেট করুন
        if self.channels_data:
            self.generate_m3u()
        else:
            print("⚠️ No channels found!")

if __name__ == "__main__":
    scraper = ToffeeScraper()
    scraper.scrape()
