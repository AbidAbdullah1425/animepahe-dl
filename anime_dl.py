import os
import re
import json
import random
import string
import asyncio
import aiohttp
import argparse
import ssl
import logging
from pathlib import Path
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from urllib.parse import quote, urljoin
from Crypto.Cipher import AES
import m3u8
import ffmpeg

class AnimePaheDownloader:
    def __init__(self):
        self.host = "https://animepahe.ru"
        self.anime_url = f"{self.host}/anime"
        self.api_url = f"{self.host}/api"
        self.referer_url = self.host
        self.session = None
        self.debug_mode = False
        self.list_only = False
        
        # Create downloads directory
        self.script_path = Path(os.getcwd())
        self.downloads_path = self.script_path / "downloads"
        self.downloads_path.mkdir(exist_ok=True)
        self.anime_list_file = self.script_path / "anime.list"

        # Add better user agent
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    async def init_session(self):
        """Initialize aiohttp session with required headers"""
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        cookie = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
        headers = {
            "User-Agent": self.user_agent,
            "Referer": self.referer_url,
            "Cookie": f"__ddg2_={cookie}",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1"
        }
        
        connector = aiohttp.TCPConnector(ssl=ssl_context, force_close=True)
        self.session = aiohttp.ClientSession(headers=headers, connector=connector)

    def print_info(self, message: str):
        """Print info message"""
        if not self.list_only:
            print(f"\033[32m[INFO]\033[0m {message}")

    def print_warn(self, message: str):
        """Print warning message"""
        if not self.list_only:
            print(f"\033[33m[WARNING]\033[0m {message}")

    def print_error(self, message: str):
        """Print error message and exit"""
        print(f"\033[31m[ERROR]\033[0m {message}")
        raise SystemExit(1)

    async def search_anime(self, query: str) -> List[Dict]:
        """Search for anime using the API"""
        try:
            params = {
                "m": "search",
                "q": quote(query)
            }
            
            async with self.session.get(f"{self.host}/api", params=params) as resp:
                if resp.status != 200:
                    self.print_warn(f"Search request failed with status {resp.status}")
                    return []
                    
                data = await resp.json()
                if data["total"] == 0:
                    return []
                    
                return [{
                    "session": item["session"],
                    "title": item["title"],
                    "slug": item["session"]
                } for item in data["data"]]
                
        except Exception as e:
            if self.debug_mode:
                self.print_warn(f"Search error: {str(e)}")
            return []

    async def get_episode_list(self, anime_id: str) -> List[Dict]:
        """Get list of episodes for an anime"""
        page = 1
        all_episodes = []
        
        try:
            while True:
                params = {
                    "m": "release",
                    "id": anime_id,
                    "sort": "episode_asc",
                    "page": page
                }
                
                async with self.session.get(self.api_url, params=params) as resp:
                    if resp.status != 200:
                        break
                        
                    data = await resp.json()
                    all_episodes.extend(data["data"])
                    
                    if page >= data["last_page"]:
                        break
                    page += 1
            
            return all_episodes
        except Exception as e:
            if self.debug_mode:
                self.print_warn(f"Episode list error: {str(e)}")
            return []

    async def get_episode_link(self, anime_slug: str, session_id: str, quality: Optional[str] = None) -> str:
        """Get download link for an episode"""
        try:
            url = f"{self.host}/play/{anime_slug}/{session_id}"
            headers = {
                "User-Agent": self.user_agent,
                "Referer": self.host,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
            }
            
            async with self.session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    if self.debug_mode:
                        self.print_warn(f"Failed to get page: Status {resp.status}")
                    return None
                    
                html = await resp.text()
                soup = BeautifulSoup(html, 'html.parser')
                
                # Find all download buttons
                buttons = []
                for button in soup.find_all('button', attrs={'data-src': True}):
                    resolution = button.get('data-resolution', '')
                    av1 = button.get('data-av1', '1')
                    if av1 == '0':  # Only non-AV1 streams
                        buttons.append((button['data-src'], resolution))
                
                if not buttons:
                    if self.debug_mode:
                        self.print_warn("No download buttons found")
                    return None
                    
                # Filter by quality if specified
                if quality:
                    matching_buttons = [b for b in buttons if b[1] == quality]
                    if matching_buttons:
                        return matching_buttons[0][0]
                
                # Return highest quality
                buttons.sort(key=lambda x: int(x[1]) if x[1].isdigit() else 0)
                return buttons[-1][0]
                
        except Exception as e:
            if self.debug_mode:
                self.print_warn(f"Failed to get episode link: {str(e)}")
            return None

    async def get_playlist_link(self, kwik_url: str) -> str:
        """Get m3u8 playlist link from kwik page"""
        try:
            headers = {
                "User-Agent": self.user_agent,
                "Referer": self.host
            }
            
            async with self.session.get(kwik_url, headers=headers) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()

            # Extract and process javascript
            script = re.search(r'<script>(eval.*?)</script>', html, re.DOTALL)
            if not script:
                return None

            # Process javascript to get the source URL
            js_code = script.group(1)
            js_code = js_code.replace('document', 'process')
            js_code = js_code.replace('querySelector', 'exit')
            
            # Execute javascript to get m3u8 link
            import nodejs
            result = nodejs.eval(js_code)
            
            # Extract m3u8 URL
            m3u8_url = re.search(r"source='(.*?\.m3u8)'", result)
            if m3u8_url:
                return m3u8_url.group(1)
            return None

        except Exception as e:
            if self.debug_mode:
                self.print_warn(f"Failed to get playlist link: {str(e)}")
            return None

    async def download_episode(self, anime_name: str, episode_id: str, quality: Optional[str] = None):
        """Download a single episode"""
        try:
            # Get episode link
            self.print_info(f"Getting link for episode {episode_id}...")
            link = await self.get_episode_link(anime_name, episode_id, quality)
            if not link:
                self.print_warn(f"Failed to get link for episode {episode_id}")
                return

            # Get m3u8 playlist
            self.print_info("Getting playlist...")
            playlist_url = await self.get_playlist_link(link)
            if not playlist_url:
                self.print_warn("Failed to get playlist")
                return

            if self.list_only:
                print(playlist_url)
                return

            # Download and process segments
            output_file = self.downloads_path / f"{anime_name}_EP{episode_id}.mp4"
            await self.download_m3u8(playlist_url, output_file)
            
            self.print_info(f"Episode {episode_id} downloaded successfully!")
            
        except Exception as e:
            if self.debug_mode:
                self.print_warn(f"Failed to download episode: {str(e)}")

    async def download_m3u8(self, playlist_url: str, output_file: Path):
        """Download and process m3u8 playlist"""
        try:
            # Download playlist
            async with self.session.get(playlist_url) as resp:
                if resp.status != 200:
                    raise Exception(f"Failed to get playlist: Status {resp.status}")
                playlist = m3u8.loads(await resp.text())

            # Create temporary directory for segments
            temp_dir = output_file.parent / f"temp_{output_file.stem}"
            temp_dir.mkdir(exist_ok=True)

            # Download segments
            self.print_info("Downloading segments...")
            tasks = []
            for i, segment in enumerate(playlist.segments):
                task = asyncio.create_task(
                    self.download_segment(segment.uri, temp_dir / f"{i:04d}.ts")
                )
                tasks.append(task)

            await asyncio.gather(*tasks)

            # Combine segments
            self.print_info("Combining segments...")
            with open(temp_dir / "filelist.txt", "w") as f:
                for i in range(len(tasks)):
                    f.write(f"file '{i:04d}.ts'\n")

            # Use ffmpeg to combine segments
            try:
                ffmpeg.input(
                    str(temp_dir / "filelist.txt"),
                    f="concat",
                    safe=0
                ).output(
                    str(output_file),
                    c="copy",
                    loglevel="error"
                ).overwrite_output().run(
                    capture_stdout=True,
                    capture_stderr=True
                )
            except ffmpeg.Error as e:
                self.print_error(f"FFmpeg error: {e.stderr.decode()}")

            # Cleanup
            if not self.debug_mode:
                import shutil
                shutil.rmtree(temp_dir)

        except Exception as e:
            self.print_error(f"Failed to process m3u8: {str(e)}")

    async def download_segment(self, url: str, output_path: Path):
        """Download a single segment"""
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                async with self.session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        output_path.write_bytes(data)
                        return
                    retry_count += 1
            except Exception as e:
                retry_count += 1
                if retry_count == max_retries:
                    self.print_warn(f"Failed to download segment {url}: {str(e)}")
                    raise
                await asyncio.sleep(1)

async def main():
    parser = argparse.ArgumentParser(description="Download anime from animepahe")
    parser.add_argument("-a", "--anime", help="Anime name to search for")
    parser.add_argument("-s", "--slug", help="Anime slug/UUID")
    parser.add_argument("-e", "--episode", help="Episode number(s) to download")
    parser.add_argument("-r", "--resolution", help="Video resolution (e.g., 1080, 720)")
    parser.add_argument("-l", "--list", action="store_true", help="Show m3u8 playlist link only")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug mode")
    args = parser.parse_args()

    downloader = AnimePaheDownloader()
    downloader.debug_mode = args.debug
    downloader.list_only = args.list

    try:
        await downloader.init_session()

        if args.anime:
            results = await downloader.search_anime(args.anime)
            if not results:
                downloader.print_error("No results found!")
                return

            print("\nFound anime:")
            for i, result in enumerate(results, 1):
                print(f"{i}. {result['title']}")
            
            choice = int(input("\nSelect anime (number): ")) - 1
            if 0 <= choice < len(results):
                anime = results[choice]
            else:
                downloader.print_error("Invalid selection!")
                return

        elif args.slug:
            anime = {"slug": args.slug}
        else:
            downloader.print_error("Please provide either anime name (-a) or slug (-s)!")
            return

        episodes = await downloader.get_episode_list(anime["slug"])
        if not episodes:
            downloader.print_error("No episodes found!")
            return

        if not args.episode:
            print("\nAvailable episodes:")
            for ep in episodes:
                print(f"Episode {ep['episode']}")
            episode_input = input("\nEnter episode number(s) (e.g., 1-3,5,7-9): ")
        else:
            episode_input = args.episode

        episode_numbers = []
        for part in episode_input.split(","):
            if "-" in part:
                start, end = map(int, part.split("-"))
                episode_numbers.extend(range(start, end + 1))
            else:
                episode_numbers.append(int(part))

        for ep in sorted(set(episode_numbers)):
            for episode in episodes:
                if int(episode['episode']) == ep:
                    await downloader.download_episode(
                        anime["slug"],
                        episode['session'],  # Use session ID instead of episode number
                        args.resolution
                    )
                    break

    finally:
        await downloader.close()

if __name__ == "__main__":
    asyncio.run(main())            